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
from urllib import error, request
from urllib.parse import quote, urlencode

USER_AGENT = "lightlyyroll-discord-bot"
TIMEOUT = 10

USER_URL = "https://www.faceit.com/api/users/v1/nicknames/{nickname}"
STATS_URL = "https://www.faceit.com/api/stats/v1/stats/time/users/{player_id}/games/cs2"

# Per-match keys in the stats payload, decoded by correlating against a known profile
# and cross-checked against the payload's own ratio fields (c2=K/D, c3=K/R, c4=HS%, c10=ADR).
KILLS, ASSISTS, DEATHS, WIN = "i6", "i7", "i8", "i10"
ROUNDS, HEADSHOTS, DAMAGE = "i12", "i13", "i20"


class FaceitError(Exception):
    """A failure that should be shown to the user rather than logged."""


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


def _fetch(url: str):
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=TIMEOUT) as response:
            return json.load(response)
    except error.HTTPError as exc:
        if exc.code == 404:
            raise FaceitError("Player not found on FACEIT.") from exc
        raise FaceitError(f"FACEIT returned HTTP {exc.code}.") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise FaceitError("Could not reach FACEIT.") from exc


async def _get(url: str):
    return await asyncio.to_thread(_fetch, url)


async def player(nickname: str) -> Player:
    url = USER_URL.format(nickname=quote(nickname, safe=""))
    payload = (await _get(url))["payload"]
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


async def recent_stats(player_id: str, size: int = 30) -> Stats:
    url = STATS_URL.format(player_id=quote(player_id, safe="")) + "?" + urlencode({"size": size})
    matches = await _get(url)
    if not matches:
        raise FaceitError("No recent CS2 matches found for this player.")

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
    )
