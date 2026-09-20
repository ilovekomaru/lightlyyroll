"""Which Discord member is which FACEIT player, kept in a small JSON file.

Shape: {guild_id: {user_id: {player_id, nickname, role_id}}}. Ids are strings
because JSON object keys always are.

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
        return json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write(data: dict) -> None:
    temp = PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, PATH)


def for_guild(guild_id: int) -> dict:
    return _read().get(str(guild_id), {})


def guild_ids() -> list[int]:
    return [int(key) for key in _read()]


def link(guild_id: int, user_id: int, player_id: str, nickname: str) -> None:
    data = _read()
    entry = data.setdefault(str(guild_id), {}).setdefault(str(user_id), {})
    entry.update(player_id=player_id, nickname=nickname)
    _write(data)


def update(guild_id: int, user_id: int, **fields) -> None:
    data = _read()
    entry = data.get(str(guild_id), {}).get(str(user_id))
    if entry is None:
        return
    entry.update(fields)
    _write(data)


def unlink(guild_id: int, user_id: int) -> dict | None:
    data = _read()
    entry = data.get(str(guild_id), {}).pop(str(user_id), None)
    if not data.get(str(guild_id)):
        data.pop(str(guild_id), None)
    _write(data)
    return entry
