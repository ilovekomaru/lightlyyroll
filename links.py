"""Per-guild bot state: who is linked to which FACEIT player, and where to announce.

Shape: {"links": {guild_id: {user_id: {player_id, nickname, role_id, level}}},
        "channels": {guild_id: channel_id}}
Ids are strings because JSON object keys always are.

Every mutation rewrites the whole file, which is fine at this size and keeps the
on-disk state consistent: the write goes to a temp file and is then moved into
place, so an interrupted run cannot leave a half-written file behind.
"""
import json
import os
from pathlib import Path

PATH = Path(__file__).parent / "links.json"


def _read() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"links": {}, "channels": {}}
    if "links" not in data:
        data = {"links": data}  # the original flat {guild: {user: ...}} layout
    data.setdefault("channels", {})
    return data


def _write(data: dict) -> None:
    temp = PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, PATH)


def for_guild(guild_id: int) -> dict:
    return _read()["links"].get(str(guild_id), {})


def guild_ids() -> list[int]:
    return [int(key) for key in _read()["links"]]


def link(guild_id: int, user_id: int, player_id: str, nickname: str) -> None:
    data = _read()
    entry = data["links"].setdefault(str(guild_id), {}).setdefault(str(user_id), {})
    if entry.get("player_id") != player_id:
        # A different FACEIT account: the level on record belongs to the old one, and
        # keeping it would report the switch as a level change.
        entry.pop("level", None)
    entry.update(player_id=player_id, nickname=nickname)
    _write(data)


def update(guild_id: int, user_id: int, **fields) -> None:
    data = _read()
    entry = data["links"].get(str(guild_id), {}).get(str(user_id))
    if entry is None:
        return
    entry.update(fields)
    _write(data)


def unlink(guild_id: int, user_id: int) -> dict | None:
    data = _read()
    entry = data["links"].get(str(guild_id), {}).pop(str(user_id), None)
    if not data["links"].get(str(guild_id)):
        data["links"].pop(str(guild_id), None)
    _write(data)
    return entry


def find_player(user_id: int, guild_id: int | None = None) -> dict | None:
    """The member's linked account: this guild first, then any guild they linked in,
    so the lookup commands still work in a DM or a server they linked elsewhere."""
    guilds = _read()["links"]
    if guild_id is not None:
        here = guilds.get(str(guild_id), {}).get(str(user_id))
        if here:
            return here
    for entries in guilds.values():
        entry = entries.get(str(user_id))
        if entry:
            return entry
    return None


def channel_id(guild_id: int) -> int | None:
    found = _read()["channels"].get(str(guild_id))
    return int(found) if found else None


def set_channel(guild_id: int, channel: int | None) -> None:
    data = _read()
    if channel is None:
        data["channels"].pop(str(guild_id), None)
    else:
        data["channels"][str(guild_id)] = channel
    _write(data)
