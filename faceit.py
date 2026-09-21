"""Minimal client for the JSON endpoints faceit.com's own web app uses.

These are undocumented and unauthenticated. They are used instead of the official
Data API because they expose recent-form stats and ADR, which the official
lifetime endpoints do not, and because they return exactly the figures shown on
a player's profile page.

Requests go through urllib rather than aiohttp: FACEIT's CDN rejects aiohttp's
TLS signature on the stats endpoint with a 403, regardless of headers. The
blocking calls are handed to a worker thread so they do not stall the event loop.
"""
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib import error, request
from urllib.parse import quote, urlencode

USER_AGENT = "lightlyyroll-discord-bot"
TIMEOUT = 10

USER_URL = "https://www.faceit.com/api/users/v1/nicknames/{nickname}"
USER_BY_ID_URL = "https://www.faceit.com/api/users/v1/users/{player_id}"
USERS_URL = "https://www.faceit.com/api/users/v1/users"
SEARCH_URL = "https://www.faceit.com/api/searcher/v1/players"
STATS_URL = "https://www.faceit.com/api/stats/v1/stats/time/users/{player_id}/games/cs2"

# Per-match keys in the stats payload, decoded by correlating against a known profile
# and cross-checked against the payload's own ratio fields (c2=K/D, c3=K/R, c4=HS%, c10=ADR).
KILLS, ASSISTS, DEATHS, WIN = "i6", "i7", "i8", "i10"
ROUNDS, HEADSHOTS, DAMAGE = "i12", "i13", "i20"

DAY_RESET_HOUR = 3  # a FACEIT "day" is counted from 03:00 GMT
MAX_PAGE = 100      # the stats endpoint refuses larger pages


class FaceitError(Exception):
    """A failure that should be shown to the user rather than logged."""


class PlayerNotFound(FaceitError):
    pass


@dataclass(frozen=True)
class Player:
    id: str
    nickname: str
    avatar: str | None
    country: str
    elo: int


@dataclass(frozen=True)
class Stats:
    matches: int
    kills: float
    deaths: float
    assists: float
    kd: float
    kr: float
    headshot_pct: float
    adr: float
    win_rate: float
    elo_delta: int
    results: tuple[bool, ...]  # one per match, oldest first


def _fetch(url: str):
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=TIMEOUT) as response:
            return json.load(response)
    except error.HTTPError as exc:
        if exc.code == 404:
            raise PlayerNotFound("Player not found on FACEIT.") from exc
        if exc.code == 429:
            raise FaceitError("FACEIT is rate limiting us. Try again in a minute.") from exc
        raise FaceitError(f"FACEIT returned HTTP {exc.code}.") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise FaceitError("Could not reach FACEIT.") from exc


async def _get(url: str):
    return await asyncio.to_thread(_fetch, url)


async def _profile(nickname: str) -> dict:
    return (await _get(USER_URL.format(nickname=quote(nickname, safe=""))))["payload"]


async def _match_nickname(nickname: str) -> str | None:
    """Find the real spelling of a nickname that differs only by case.

    Search is fuzzy — querying "el0t3" also returns "EL0T3RR0RIST", a different
    account — so only an exact case-insensitive equality counts as a match.
    """
    url = SEARCH_URL + "?" + urlencode({"query": nickname, "limit": 20, "offset": 0})
    wanted = nickname.casefold()
    for result in (await _get(url))["payload"]:
        if result["nickname"].casefold() == wanted:
            return result["nickname"]
    return None


async def player(nickname: str) -> Player:
    try:
        payload = await _profile(nickname)
    except PlayerNotFound:
        # The nicknames endpoint matches case exactly; the search endpoint does not.
        canonical = await _match_nickname(nickname)
        if canonical is None:
            raise
        payload = await _profile(canonical)

    return _to_player(payload)


async def player_by_id(player_id: str) -> Player:
    """Look up by the stable player id, which survives a FACEIT nickname change."""
    payload = (await _get(USER_BY_ID_URL.format(player_id=quote(player_id, safe=""))))["payload"]
    return _to_player(payload)


async def players_by_ids(player_ids: list[str]) -> dict[str, Player]:
    """Look up many players in a single request, keyed by player id.

    The endpoint takes a repeated `id` parameter. A comma-separated `ids` is
    accepted but silently ignored, returning arbitrary players, so do not use it.
    Players without a CS2 profile are left out rather than failing the batch.
    """
    if not player_ids:
        return {}
    query = urlencode([("id", player_id) for player_id in player_ids])
    payload = (await _get(f"{USERS_URL}?{query}"))["payload"]
    return {entry["id"]: _to_player(entry)
            for entry in payload if entry.get("games", {}).get("cs2")}


def _to_player(payload: dict) -> Player:
    cs2 = payload.get("games", {}).get("cs2")
    if not cs2:
        raise FaceitError(f"**{payload['nickname']}** has no CS2 profile on FACEIT.")
    return Player(
        id=payload["id"],
        nickname=payload["nickname"],
        avatar=payload.get("avatar") or None,
        country=payload.get("country", ""),
        elo=cs2["faceit_elo"],
    )


async def _matches(player_id: str, size: int) -> list[dict]:
    url = STATS_URL.format(player_id=quote(player_id, safe="")) + "?" + urlencode({"size": size})
    matches = await _get(url)
    if not matches:
        raise FaceitError("No recent CS2 matches found for this player.")
    return matches


async def elo_history(player_id: str, size: int = 30) -> list[int]:
    """Elo after each of the last `size` matches, oldest first."""
    matches = await _matches(player_id, size)
    return [int(match["elo"]) for match in reversed(matches) if match.get("elo") is not None]


def day_start() -> int:
    """Epoch ms of the most recent 03:00 GMT boundary."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=DAY_RESET_HOUR, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return int(start.timestamp() * 1000)


async def recent_stats(player_id: str, size: int = 30) -> Stats:
    return _aggregate(await _matches(player_id, size))


async def stats_today(player_id: str) -> Stats | None:
    """Stats since the 03:00 GMT reset, or None if nothing has been played."""
    since = day_start()
    played = [match for match in await _matches(player_id, MAX_PAGE)
              if int(match["date"]) >= since]
    return _aggregate(played) if played else None


def _aggregate(matches: list[dict]) -> Stats:

    def total(key: str) -> float:
        return sum(float(match[key]) for match in matches)

    # Ratios come from summed totals, not the mean of each match's own ratio: that is
    # what faceit.com reports, and it is the only correct way to average a per-round
    # figure across matches with different round counts.
    played = len(matches)
    kills, deaths, rounds = total(KILLS), total(DEATHS), total(ROUNDS)
    return Stats(
        matches=played,
        kills=kills / played,
        deaths=deaths / played,
        assists=total(ASSISTS) / played,
        kd=kills / deaths,
        kr=kills / rounds,
        headshot_pct=total(HEADSHOTS) / kills * 100,
        adr=total(DAMAGE) / rounds,
        win_rate=total(WIN) / played * 100,
        elo_delta=sum(int(match.get("elo_delta") or 0) for match in matches),
        # the endpoint returns newest first, so reverse into reading order
        results=tuple(float(match[WIN]) == 1 for match in reversed(matches)),
    )
