import asyncio
import io
import os
import random
import sys
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

import chart
import elo_roles
import faceit
import levels
import links
from discord.ext import tasks
from faceit import FaceitError, Player

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
OWNER_ID = int(os.getenv("OWNER_ID") or 0)
MATCH_WINDOW = 30
MAX_HISTORY = 100  # the FACEIT endpoint caps its page size here
REFRESH_MINUTES = 5  # one batched FACEIT request per cycle, whatever the member count
RESULT_RUN = 5       # most recent results shown by /today
ASSETS = Path(__file__).parent / "assets"

if not TOKEN:
    sys.exit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill in your bot token.")


class RollBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        try:
            await ensure_result_emoji()
        except discord.HTTPException as exc:
            print(f"could not set up result emoji, falling back to letters: {exc}", flush=True)
        refresh_elo_roles.start()
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


client = RollBot()
tree = client.tree


def player_card(player: Player) -> tuple[discord.Embed, discord.File]:
    """Embed carrying the player's avatar, nickname and level badge."""
    level = levels.level_for_elo(player.elo)
    icon = levels.icon_file(level)
    file = discord.File(icon, filename=icon.name)

    embed = discord.Embed(title=f"{player.elo} ELO", colour=levels.LEVEL_COLOR[level])
    embed.set_author(name=player.nickname,
                     url=f"https://www.faceit.com/en/players/{player.nickname}",
                     icon_url=player.avatar)
    embed.set_thumbnail(url=f"attachment://{icon.name}")
    return embed, file


async def resolve(interaction: discord.Interaction, nickname: str | None) -> Player:
    """The nickname given, or the caller's own linked account when it is left out."""
    if nickname:
        return await faceit.player(nickname)
    guild_id = interaction.guild.id if interaction.guild else None
    entry = links.find_player(interaction.user.id, guild_id)
    if entry is None:
        raise FaceitError("Give a nickname, or run `/loginfaceit` to link your account.")
    return await faceit.player_by_id(entry["player_id"])


NICKNAME_HELP = "FACEIT nickname (defaults to your linked account)"


HELP = {
    "roll": "Random number",
    "elo": "Elo and trend chart",
    "stats": "Recent match stats",
    "today": "Today's stats",
    "avg": "Average kills per match",
    "whoplayed": "Who played today",
    "loginfaceit": "Link your FACEIT account",
    "logoutfaceit": "Unlink your account",
    "faceitchannel": "Set announcement channel",
    "loginfaceitforce": "Link someone else (owner)",
    "logoutfaceitforce": "Unlink someone else (owner)",
    "help": "This list",
}


@tree.command(name="help", description="List every command")
async def help_command(interaction: discord.Interaction):
    lines = []
    for command in sorted(tree.get_commands(), key=lambda c: c.name):
        params = " ".join(f"[{p.name}]" if not p.required else f"<{p.name}>"
                          for p in command.parameters)
        usage = f"/{command.name} {params}".strip()
        # Fall back to the command's own description so a new command still shows up.
        lines.append(f"**{usage}** — {HELP.get(command.name, command.description)}")
    embed = discord.Embed(title="Commands", description="\n".join(lines), colour=0xCA5325)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="roll", description="Roll a random number")
@app_commands.describe(maximum="Upper bound of the roll (default 100)")
async def roll(interaction: discord.Interaction, maximum: app_commands.Range[int, 1, 1_000_000] = 100):
    await interaction.response.send_message(f"\U0001F3B2 {random.randint(0, maximum)} (0-{maximum})")


@tree.command(name="elo", description="Show a player's FACEIT CS2 elo and recent trend")
@app_commands.describe(nickname=NICKNAME_HELP, matches="Matches to plot (default 30, max 100)")
async def elo(interaction: discord.Interaction, nickname: str = None,
              matches: app_commands.Range[int, 2, MAX_HISTORY] = MATCH_WINDOW):
    await interaction.response.defer()
    player = await resolve(interaction, nickname)
    history = await faceit.elo_history(player.id, matches)
    embed, file = player_card(player)

    image = await asyncio.to_thread(chart.render_elo, history)
    embed.set_image(url="attachment://elo.png")
    change = history[-1] - history[0]
    embed.set_footer(text=f"Last {len(history)} matches • {change:+} elo")

    await interaction.followup.send(embed=embed,
                                    files=[file, discord.File(io.BytesIO(image), "elo.png")])


RESULT_EMOJI: dict[bool, str] = {}


async def ensure_result_emoji() -> None:
    """Upload the green W and red L once as application emoji.

    Application emoji belong to the bot rather than a server, so they render in
    every guild and in DMs with no per-server setup.
    """
    existing = {emoji.name: emoji for emoji in await client.fetch_application_emojis()}
    for name, won in (("fcwin", True), ("fcloss", False)):
        emoji = existing.get(name)
        if emoji is None:
            image = (ASSETS / ("win.png" if won else "loss.png")).read_bytes()
            emoji = await client.create_application_emoji(name=name, image=image)
        RESULT_EMOJI[won] = str(emoji)


def result_run(results: tuple[bool, ...]) -> str:
    """Wins green, losses red. Falls back to plain letters if the emoji are missing,
    which is the only way to colour text without wrapping it in a code block."""
    if RESULT_EMOJI:
        return " ".join(RESULT_EMOJI[won] for won in results)
    return " ".join("W" if won else "L" for won in results)


def add_stat_fields(embed: discord.Embed, data: faceit.Stats) -> None:
    embed.add_field(name="K/D/A", value=f"{data.kills:.0f} / {data.deaths:.0f} / {data.assists:.0f}")
    embed.add_field(name="K/D", value=f"{data.kd:.2f}")
    embed.add_field(name="K/R", value=f"{data.kr:.2f}")
    embed.add_field(name="HS", value=f"{data.headshot_pct:.0f}%")
    embed.add_field(name="ADR", value=f"{data.adr:.1f}")
    embed.add_field(name="Win Rate", value=f"{data.win_rate:.0f}%")


@tree.command(name="stats", description="Show a player's recent FACEIT CS2 stats")
@app_commands.describe(nickname=NICKNAME_HELP)
async def stats(interaction: discord.Interaction, nickname: str = None):
    await interaction.response.defer()
    player = await resolve(interaction, nickname)
    data = await faceit.recent_stats(player.id, MATCH_WINDOW)
    embed, file = player_card(player)
    embed.description = result_run(data.results[-RESULT_RUN:])
    add_stat_fields(embed, data)
    embed.set_footer(text=f"Last {data.matches} matches")
    await interaction.followup.send(embed=embed, file=file)


@tree.command(name="today", description="Today's FACEIT CS2 stats, counted from 03:00 GMT")
@app_commands.describe(nickname=NICKNAME_HELP)
async def today(interaction: discord.Interaction, nickname: str = None):
    await interaction.response.defer()
    player = await resolve(interaction, nickname)
    data = await faceit.stats_today(player.id)
    embed, file = player_card(player)
    if data is None:
        embed.description = "No matches played today."
    else:
        embed.description = result_run(data.results[-RESULT_RUN:])
        add_stat_fields(embed, data)
        wins = sum(data.results)
        embed.set_footer(
            text=f"{wins}W {data.matches - wins}L • {data.elo_delta:+} elo")
    await interaction.followup.send(embed=embed, file=file)


@tree.command(name="avg", description="Show a player's average kills per match")
@app_commands.describe(nickname=NICKNAME_HELP)
async def avg(interaction: discord.Interaction, nickname: str = None):
    await interaction.response.defer()
    player = await resolve(interaction, nickname)
    data = await faceit.recent_stats(player.id, MATCH_WINDOW)
    embed, file = player_card(player)
    embed.add_field(name="Average kills", value=f"{data.kills:.1f}")
    embed.set_footer(text=f"Last {data.matches} matches")
    await interaction.followup.send(embed=embed, file=file)


@tree.command(name="whoplayed", description="Everyone linked here who has played since 03:00 GMT")
@app_commands.guild_only()
async def whoplayed(interaction: discord.Interaction):
    await interaction.response.defer()
    entries = links.for_guild(interaction.guild.id)
    if not entries:
        raise FaceitError("Nobody here has linked an account yet. Try `/loginfaceit`.")

    players = await faceit.players_by_ids([e["player_id"] for e in entries.values()])
    played, unavailable = [], 0
    for player in players.values():
        # Today's matches are per-player; that endpoint rate limits, so one player
        # being throttled must not sink the whole command.
        try:
            data = await faceit.stats_today(player.id)
        except FaceitError:
            unavailable += 1
            continue
        if data is not None:
            played.append((player, data))

    if not played:
        raise FaceitError("Nobody has played yet today.")

    played.sort(key=lambda row: row[1].elo_delta, reverse=True)
    rows = []
    for player, data in played:
        wins = sum(data.results)
        rows.append(f"**{player.nickname}** — {player.elo} elo · "
                    f"{wins}W {data.matches - wins}L · {data.elo_delta:+} elo")

    embed = discord.Embed(title="Played today", description="\n".join(rows),
                          colour=levels.LEVEL_COLOR[levels.level_for_elo(played[0][0].elo)])
    footer = f"{len(played)} of {len(entries)} linked • since 03:00 GMT"
    if unavailable:
        footer += f" • {unavailable} unavailable"
    embed.set_footer(text=footer)
    await interaction.followup.send(embed=embed)


@tree.command(name="loginfaceit", description="Link your FACEIT account and get an elo role")
@app_commands.describe(nickname="Your FACEIT nickname")
@app_commands.guild_only()
async def loginfaceit(interaction: discord.Interaction, nickname: str):
    await interaction.response.defer(ephemeral=True)
    player = await faceit.player(nickname)
    links.link(interaction.guild.id, interaction.user.id, player.id, player.nickname)
    await announce(interaction.guild, await elo_roles.sync(interaction.guild))

    message = f"Linked to **{player.nickname}** — {player.elo} elo."
    warning = elo_roles.ceiling_warning(interaction.guild)
    await interaction.followup.send(f"{message}\n{warning}" if warning else message, ephemeral=True)


def owner_only():
    """Restrict to the account in OWNER_ID. Discord has no owner-only visibility,
    so the command is still listed for everyone; it just refuses."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return OWNER_ID != 0 and interaction.user.id == OWNER_ID
    return app_commands.check(predicate)


@tree.command(name="loginfaceitforce", description="Link someone else's FACEIT account")
@app_commands.describe(member="Who to link", nickname="Their FACEIT nickname")
@app_commands.guild_only()
@owner_only()
async def loginfaceitforce(interaction: discord.Interaction, member: discord.Member, nickname: str):
    await interaction.response.defer(ephemeral=True)
    player = await faceit.player(nickname)
    links.link(interaction.guild.id, member.id, player.id, player.nickname)
    await announce(interaction.guild, await elo_roles.sync(interaction.guild))

    message = f"Linked {member.mention} to **{player.nickname}** — {player.elo} elo."
    warning = elo_roles.ceiling_warning(interaction.guild)
    await interaction.followup.send(f"{message}\n{warning}" if warning else message, ephemeral=True)


@tree.command(name="logoutfaceit", description="Unlink your FACEIT account and remove your elo role")
@app_commands.guild_only()
async def logoutfaceit(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    removed = await elo_roles.forget(interaction.guild, interaction.user.id)
    await interaction.followup.send(
        "Unlinked, and your elo role is gone." if removed else "You are not linked.",
        ephemeral=True)


@tree.command(name="faceitchannel", description="Choose where level-up announcements are posted")
@app_commands.describe(channel="Channel to post in, or leave empty to turn announcements off")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def faceitchannel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    links.set_channel(interaction.guild.id, channel.id if channel else None)
    await interaction.response.send_message(
        f"Level changes will be announced in {channel.mention}." if channel
        else "Level announcements are off.", ephemeral=True)


async def announce(guild: discord.Guild, changes: list[elo_roles.LevelChange]) -> None:
    channel = guild.get_channel(links.channel_id(guild.id) or 0)
    if channel is None:
        return
    for change in changes:
        icon = levels.icon_file(change.after)
        verb = "reached" if change.promoted else "dropped to"
        embed = discord.Embed(
            colour=levels.LEVEL_COLOR[change.after],
            description=(f"{change.member.mention} — **{change.player.nickname}** "
                         f"{verb} **level {change.after}**\n"
                         f"{change.player.elo} elo • was level {change.before}"))
        embed.set_thumbnail(url=f"attachment://{icon.name}")
        await channel.send(embed=embed, file=discord.File(icon, filename=icon.name))


@tree.command(name="logoutfaceitforce", description="Unlink someone else's FACEIT account")
@app_commands.describe(member="Who to unlink")
@app_commands.guild_only()
@owner_only()
async def logoutfaceitforce(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    removed = await elo_roles.forget(interaction.guild, member.id)
    await interaction.followup.send(
        f"Unlinked {member.mention} and removed their elo role." if removed
        else f"{member.mention} is not linked.", ephemeral=True)


@tasks.loop(minutes=REFRESH_MINUTES)
async def refresh_elo_roles():
    for guild_id in links.guild_ids():
        guild = client.get_guild(guild_id)
        if guild is None:
            continue
        try:
            await announce(guild, await elo_roles.sync(guild))
        except (FaceitError, elo_roles.RoleSyncError, discord.HTTPException) as exc:
            print(f"elo role sync failed in {guild.name}: {exc}", flush=True)


@refresh_elo_roles.before_loop
async def _wait_for_login():
    await client.wait_until_ready()


@tree.error
async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    cause = error.__cause__ if isinstance(error, app_commands.CommandInvokeError) else error
    shown = (FaceitError, elo_roles.RoleSyncError)
    message = str(cause) if isinstance(cause, shown) else "Something went wrong."
    if isinstance(cause, app_commands.CheckFailure):
        message = "Only the bot owner can use that command."
    elif isinstance(cause, discord.Forbidden):
        message = "I do not have permission to manage roles here."
    elif not isinstance(cause, shown):
        print(f"Unhandled command error: {error!r}", flush=True)
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}", flush=True)


if __name__ == "__main__":
    client.run(TOKEN)
