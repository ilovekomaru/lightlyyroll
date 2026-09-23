"""Dota 2 match history through the OpenDota API.

Dotabuff itself cannot be read: it answers every non-browser client, curl included,
with Cloudflare's interactive "Just a moment..." challenge. OpenDota serves the same
public match data as plain JSON with no key, at 60 requests a minute and 3000 a day.

Players are identified by their Steam32 account id, the Dota "friend id". Dotabuff,
OpenDota and Stratz all use it in their profile URLs, and a SteamID64 is the same
number offset by STEAM64_BASE, so every kind of profile link resolves to it.
"""
import asyncio
import json
import re
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import quote, urlencode, urlparse
from xml.etree import ElementTree

from faceit import day_start

USER_AGENT = "lightlyyroll-discord-bot"
TIMEOUT = 10

PLAYER_URL = "https://api.opendota.com/api/players/{account_id}"
MATCHES_URL = "https://api.opendota.com/api/players/{account_id}/matches"
# Steam's public profile XML needs no Web API key and carries the live profile name,
# where OpenDota's copy is only as fresh as its last crawl of the profile.
STEAM_PROFILE_URL = "https://steamcommunity.com/profiles/{steam64}?xml=1"
STEAM_VANITY_URL = "https://steamcommunity.com/id/{vanity}?xml=1"

STEAM64_BASE = 76561197960265728
RADIANT_SLOTS = 128  # player_slot below this is Radiant, at or above it is Dire
TODAY_PAGE = 50      # far more matches than anyone plays between two 03:00 resets

ID_LINK = re.compile(r"(?:dotabuff\.com|opendota\.com|stratz\.com)/players/(\d+)")
STEAM_PROFILE_LINK = re.compile(r"steamcommunity\.com/profiles/(\d+)")
STEAM_VANITY_LINK = re.compile(r"steamcommunity\.com/id/([^/?#]+)")
FINDING_HELP = ("Paste your Dotabuff or Steam profile link, "
                "e.g. `https://www.dotabuff.com/players/12345678`.")


class DotaError(Exception):
    """A failure that should be shown to the user rather than logged."""


@dataclass(frozen=True)
class DotaPlayer:
    account_id: int
    name: str
    avatar: str | None


def _fetch(url: str) -> bytes:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=TIMEOUT) as response:
            return response.read()
    except error.HTTPError as exc:
        if exc.code == 429:
            raise DotaError("OpenDota is rate limiting us. Try again in a minute.") from exc
        raise DotaError(f"{urlparse(url).hostname} returned HTTP {exc.code}.") from exc
    except (error.URLError, TimeoutError) as exc:
        raise DotaError(f"Could not reach {urlparse(url).hostname}.") from exc


async def _json(url: str):
    body = await asyncio.to_thread(_fetch, url)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise DotaError("OpenDota sent back something unreadable.") from exc


async def _steam_xml(url: str) -> ElementTree.Element | None:
    try:
        root = ElementTree.fromstring(await asyncio.to_thread(_fetch, url))
    except ElementTree.ParseError:
        return None
    # An unknown profile comes back as a 200 with <response><error>…</error></response>.
    return None if root.find("error") is not None else root


async def account_id(text: str) -> int:
    """The Steam32 account id behind whatever the member pasted: a Dotabuff, OpenDota,
    Stratz or Steam profile link, a friend id, a SteamID64 or a Steam custom URL name."""
    text = text.strip()
    if found := ID_LINK.search(text):
        return int(found.group(1))
    if found := STEAM_PROFILE_LINK.search(text):
        return int(found.group(1)) - STEAM64_BASE
    if text.isdigit():
        number = int(text)
        return number - STEAM64_BASE if number >= STEAM64_BASE else number

    found = STEAM_VANITY_LINK.search(text)
    vanity = found.group(1) if found else text
    if "/" in vanity or not vanity:
        raise DotaError(f"That does not look like a profile link. {FINDING_HELP}")
    root = await _steam_xml(STEAM_VANITY_URL.format(vanity=quote(vanity, safe="")))
    steam64 = root.findtext("steamID64") if root is not None else None
    if not steam64:
        raise DotaError(f"No Steam profile called `{vanity}`. {FINDING_HELP}")
    return int(steam64) - STEAM64_BASE


async def steam_name(account: int) -> str | None:
    """The live Steam name, or None if Steam cannot say; callers fall back to a stored one."""
    try:
        root = await _steam_xml(STEAM_PROFILE_URL.format(steam64=account + STEAM64_BASE))
    except DotaError:
        return None
    return root.findtext("steamID") if root is not None else None


async def player(account: int) -> DotaPlayer:
    profile = (await _json(PLAYER_URL.format(account_id=account))).get("profile") or {}
    # OpenDota answers any number with a profile object; one it has never seen play
    # has every field null.
    if not profile.get("personaname"):
        raise DotaError("OpenDota has no Dota 2 player with that id. "
                        "Check the link, and that the account has played Dota 2.")
    name = await steam_name(account) or profile["personaname"]
    return DotaPlayer(account, name, profile.get("avatarfull"))


async def results_today(account: int) -> tuple[bool, ...]:
    """Win or loss for each match since the 03:00 GMT reset, oldest first."""
    query = urlencode([("limit", TODAY_PAGE), ("significant", 0),  # 0 keeps Turbo and other modes
                       ("project", "start_time"), ("project", "player_slot"),
                       ("project", "radiant_win")])
    matches = await _json(MATCHES_URL.format(account_id=account) + "?" + query)
    since = day_start() // 1000
    return tuple((match["player_slot"] < RADIANT_SLOTS) == match["radiant_win"]
                 for match in reversed(matches)
                 if match["start_time"] >= since and match["radiant_win"] is not None)
