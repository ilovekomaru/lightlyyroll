import asyncio
import io
import os
import random
import sys

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
MATCH_WINDOW = 30
MAX_HISTORY = 100  # the FACEIT endpoint caps its page size here
REFRESH_MINUTES = 15

if not TOKEN:
    sys.exit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill in your bot token.")


class RollBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        refresh_elo_roles.start()
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


client = RollBot()
tree = client.tree


def flag(country: str) -> str:
    if len(country) != 2 or not country.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("a")) for c in country.lower())


def player_card(player: Player) -> tuple[discord.Embed, discord.File]:
    """Embed carrying the player's avatar, nickname and level badge."""
    level = levels.level_for_elo(player.elo)
    icon = levels.icon_file(level)
    file = discord.File(icon, filename=icon.name)

    embed = discord.Embed(title=f"{player.elo} ELO", colour=levels.LEVEL_COLOR[level])
    embed.set_author(name=f"{player.nickname} {flag(player.country)}".strip(),
                     url=f"https://www.faceit.com/en/players/{player.nickname}",
                     icon_url=player.avatar)
    embed.set_thumbnail(url=f"attachment://{icon.name}")
    return embed, file


@tree.command(name="roll", description="Roll a random number")
@app_commands.describe(maximum="Upper bound of the roll (default 100)")
async def roll(interaction: discord.Interaction, maximum: app_commands.Range[int, 1, 1_000_000] = 100):
    await interaction.response.send_message(f"\U0001F3B2 {random.randint(0, maximum)} (0-{maximum})")


@tree.command(name="elo", description="Show a player's FACEIT CS2 elo and recent trend")
@app_commands.describe(nickname="FACEIT nickname", matches="Matches to plot (default 30, max 100)")
async def elo(interaction: discord.Interaction, nickname: str,
              matches: app_commands.Range[int, 2, MAX_HISTORY] = MATCH_WINDOW):
    await interaction.response.defer()
    player = await faceit.player(nickname)
    history = await faceit.elo_history(player.id, matches)
    embed, file = player_card(player)

    image = await asyncio.to_thread(chart.render_elo, history)
    embed.set_image(url="attachment://elo.png")
    change = history[-1] - history[0]
    embed.set_footer(text=f"Last {len(history)} matches • {change:+} elo")

    await interaction.followup.send(embed=embed,
                                    files=[file, discord.File(io.BytesIO(image), "elo.png")])


@tree.command(name="stats", description="Show a player's recent FACEIT CS2 stats")
@app_commands.describe(nickname="FACEIT nickname")
async def stats(interaction: discord.Interaction, nickname: str):
    await interaction.response.defer()
    player = await faceit.player(nickname)
    data = await faceit.recent_stats(player.id, MATCH_WINDOW)
    embed, file = player_card(player)
    embed.add_field(name="K/D/A", value=f"{data.kills:.0f} / {data.deaths:.0f} / {data.assists:.0f}")
    embed.add_field(name="K/D", value=f"{data.kd:.2f}")
    embed.add_field(name="K/R", value=f"{data.kr:.2f}")
    embed.add_field(name="HS", value=f"{data.headshot_pct:.0f}%")
    embed.add_field(name="ADR", value=f"{data.adr:.1f}")
    embed.add_field(name="Win Rate", value=f"{data.win_rate:.0f}%")
    embed.set_footer(text=f"Last {data.matches} matches")
    await interaction.followup.send(embed=embed, file=file)


@tree.command(name="avg", description="Show a player's average kills per match")
@app_commands.describe(nickname="FACEIT nickname")
async def avg(interaction: discord.Interaction, nickname: str):
    await interaction.response.defer()
    player = await faceit.player(nickname)
    data = await faceit.recent_stats(player.id, MATCH_WINDOW)
    embed, file = player_card(player)
    embed.add_field(name="Average kills", value=f"{data.kills:.1f}")
    embed.set_footer(text=f"Last {data.matches} matches")
    await interaction.followup.send(embed=embed, file=file)


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
    if isinstance(cause, discord.Forbidden):
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
