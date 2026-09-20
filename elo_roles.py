"""One hoisted role per linked player, ordered so the member list ranks by elo.

Discord groups the member list by each member's highest hoisted role and orders
those groups by role position, so a hoisted role per player, positioned by elo,
turns the sidebar into a live leaderboard.

The roles have to sit directly beneath the bot's own top role: Discord refuses to
let a bot place a role above itself, so an admin has to drag the bot's role to the
top once. `ceiling_warning` reports when that has not been done.
"""
from dataclasses import dataclass

import discord

import faceit
import levels
import links

REASON = "FACEIT elo sync"


@dataclass(frozen=True)
class LevelChange:
    member: discord.Member
    player: faceit.Player
    before: int
    after: int

    @property
    def promoted(self) -> bool:
        return self.after > self.before


class RoleSyncError(Exception):
    """A misconfiguration the user can fix, worth showing to them."""


def ceiling_warning(guild: discord.Guild) -> str | None:
    """Tell the user if elo roles will not actually sit on top."""
    above = [role for role in guild.roles
             if role.position > guild.me.top_role.position and not role.is_default()]
    if not above:
        return None
    names = ", ".join(f"**{role.name}**" for role in above[:3])
    return (f"My role sits below {names}, so elo roles cannot go above those. "
            f"Drag **{guild.me.top_role.name}** to the top of the role list to fix it.")


async def sync(guild: discord.Guild) -> list[LevelChange]:
    """Refresh roles and report whose skill level moved since the last run."""
    entries = links.for_guild(guild.id)
    if not entries:
        return []
    if not guild.me.guild_permissions.manage_roles:
        raise RoleSyncError("I need the **Manage Roles** permission to do this.")

    ranked = []
    for user_id, entry in list(entries.items()):
        member = await _member(guild, int(user_id))
        if member is None:
            await _drop(guild, int(user_id), entry)
            continue
        player = await faceit.player_by_id(entry["player_id"])
        if player.nickname != entry.get("nickname"):
            links.update(guild.id, int(user_id), nickname=player.nickname)
        ranked.append((member, entry, player))

    if not ranked:
        return []
    ceiling = guild.me.top_role.position
    if ceiling - len(ranked) < 1:
        raise RoleSyncError(
            f"There is no room above the other roles for {len(ranked)} elo roles. "
            f"Move **{guild.me.top_role.name}** higher up the role list.")

    ranked.sort(key=lambda row: row[2].elo, reverse=True)
    positions = {}
    changes = []
    for index, (member, entry, player) in enumerate(ranked):
        level = levels.level_for_elo(player.elo)
        before = entry.get("level")
        if before is not None and before != level:
            changes.append(LevelChange(member, player, before, level))
        if before != level:
            links.update(guild.id, member.id, level=level)

        role = await _ensure_role(guild, member, entry, player, level)
        positions[role] = ceiling - 1 - index

    await guild.edit_role_positions(positions=positions, reason=REASON)
    return changes


async def forget(guild: discord.Guild, user_id: int) -> bool:
    entry = links.unlink(guild.id, user_id)
    if entry is None:
        return False
    await _delete_role(guild, entry.get("role_id"))
    return True


async def _member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    """Fetch over REST when absent from cache — the members intent is not enabled."""
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None


async def _ensure_role(guild: discord.Guild, member: discord.Member,
                       entry: dict, player: faceit.Player, level: int) -> discord.Role:
    colour = discord.Colour(levels.LEVEL_COLOR[level])
    name = f"{player.elo} {player.nickname}"

    role = guild.get_role(entry.get("role_id") or 0)
    if role is None:
        role = await guild.create_role(name=name, colour=colour, hoist=True, reason=REASON)
        links.update(guild.id, member.id, role_id=role.id)
    elif role.name != name or role.colour != colour:
        # Only touch the role when something actually changed; role edits are the
        # rate-limited part of this and most refreshes change nothing.
        await role.edit(name=name, colour=colour, reason=REASON)

    if role not in member.roles:
        await member.add_roles(role, reason=REASON)
    return role


async def _drop(guild: discord.Guild, user_id: int, entry: dict) -> None:
    links.unlink(guild.id, user_id)
    await _delete_role(guild, entry.get("role_id"))


async def _delete_role(guild: discord.Guild, role_id: int | None) -> None:
    role = guild.get_role(role_id or 0)
    if role is not None:
        await role.delete(reason=REASON)
