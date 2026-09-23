import asyncio
import io
import os
import random
import sys
from collections import Counter
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

import chart
import dota
import economy
import elo_roles
import faceit
import games
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
DOTA_COLOUR = 0xA72714
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
        # 127 uploads on a first run take a while under Discord's rate limit, so they
        # happen in the background; until each lands, that hero just shows no icon.
        self.hero_emoji_task = asyncio.create_task(ensure_hero_emoji())
        refresh_elo_roles.start()
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


client = RollBot()
tree = client.tree


def weekly_income(guild_id: int, user_id: int) -> int:
    """A member's weekly coins: their elo, or a flat base without a linked account.

    Elo is read from the cache the role sync keeps on each link, so paying income
    never costs a FACEIT request.
    """
    entry = links.for_guild(guild_id).get(str(user_id)) or {}
    return entry.get("elo") or economy.BASE_INCOME


games.setup(tree, weekly_income)


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
    "Games": {
        "slot": "Spin the slot machine",
        "coinflip": "Flip against whoever joins",
        "dice": "Roll dice, like 2d6",
        "roll": "Random number, free",
        "wheel": "Pick from your options, free",
    },
    "Coins": {
        "balance": "Your coins and income",
        "pay": "Give coins to someone",
        "rich": "Who has the most coins",
    },
    "FACEIT": {
        "elo": "Elo and trend chart",
        "stats": "Recent match stats",
        "today": "Today's stats",
        "avg": "Average kills per match",
        "whoplayed": "Who played today",
    },
    "Dota 2": {
        "whoplayeddota": "Who played Dota today",
    },
    "Account": {
        "loginfaceit": "Link your FACEIT account",
        "logoutfaceit": "Unlink your account",
        "faceitchannel": "Set announcement channel",
        "logindota": "Link your Dota 2 account",
        "logoutdota": "Unlink your Dota 2 account",
        "help": "This list",
    },
}


def is_owner_only(command: app_commands.Command) -> bool:
    return any(getattr(check, "owner_only", False) for check in command.checks)


@tree.command(name="help", description="List every command")
async def help_command(interaction: discord.Interaction):
    listed = {command.name: command for command in tree.get_commands()
              if not is_owner_only(command)}
    embed = discord.Embed(title="Commands", colour=0xCA5325)

    for theme, blurbs in HELP.items():
        lines = []
        for name, blurb in blurbs.items():
            command = listed.pop(name, None)
            if command is None:
                continue
            params = " ".join(f"[{p.name}]" if not p.required else f"<{p.name}>"
                              for p in command.parameters)
            lines.append(f"**{f'/{name} {params}'.strip()}** — {blurb}")
        if lines:
            embed.add_field(name=theme, value="\n".join(lines), inline=False)

    # Anything without a theme still gets listed rather than silently vanishing.
    if listed:
        embed.add_field(name="Other", inline=False, value="\n".join(
            f"**/{name}** — {command.description}"
            for name, command in sorted(listed.items())))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="balance", description="Your coins, weekly income and gift allowance")
@app_commands.describe(member="Whose balance to show (default yours)")
@app_commands.guild_only()
async def balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    income = weekly_income(interaction.guild.id, member.id)
    state = economy.account(interaction.guild.id, member.id, income)

    embed = discord.Embed(title=f"{state['coins']} coins", colour=0xFFC800)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.add_field(name="Weekly income", value=f"{income}")
    embed.add_field(name="Next payout", value=f"<t:{int(state['next_payout'])}:R>")
    embed.add_field(name="Can still give away", value=f"{state['gift_left']}")
    embed.set_footer(text=f"Max bet right now: {economy.max_bet(state['coins'])}")
    if state["credited"]:
        embed.description = f"Weekly income of **{state['credited']}** coins just landed."
    await interaction.response.send_message(embed=embed)


@tree.command(name="pay", description="Give coins to another member")
@app_commands.describe(member="Who to pay", amount="How many coins")
@app_commands.guild_only()
async def pay(interaction: discord.Interaction, member: discord.Member, amount: int):
    if member.bot:
        raise economy.EconomyError("Bots have no use for coins.")
    left, allowance = economy.transfer(
        interaction.guild.id,
        interaction.user.id, weekly_income(interaction.guild.id, interaction.user.id),
        member.id, weekly_income(interaction.guild.id, member.id), amount)
    await interaction.response.send_message(
        f"{interaction.user.mention} gave **{amount}** coins to {member.mention}.\n"
        f"You have {left} left, and can give away {allowance} more this week.")


@tree.command(name="rich", description="Who has the most coins here")
@app_commands.guild_only()
async def rich(interaction: discord.Interaction):
    ranked = economy.richest(interaction.guild.id)
    if not ranked:
        raise economy.EconomyError("Nobody here has an account yet. Try `/balance`.")
    lines = []
    for place, (user_id, coins) in enumerate(ranked, start=1):
        holder = interaction.guild.get_member(user_id)
        name = holder.display_name if holder else f"<@{user_id}>"
        lines.append(f"**{place}.** {name} — {coins} coins")
    embed = discord.Embed(title="\U0001F4B0 Richest", description="\n".join(lines),
                          colour=0xFFC800)
    await interaction.response.send_message(embed=embed)


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


HERO_EMOJI: dict[int, str] = {}
HERO_EMOJI_PREFIX = "dh_"


async def ensure_hero_emoji() -> None:
    """Upload each Dota hero's minimap icon once as an application emoji.

    The hero list is re-read at every start, so a newly released hero gets its icon
    on the next restart with no code change.
    """
    try:
        existing = {emoji.name: emoji for emoji in await client.fetch_application_emojis()}
        heroes = await dota.heroes()
    except (dota.DotaError, discord.HTTPException) as exc:
        print(f"could not set up hero emoji, showing results without heroes: {exc}", flush=True)
        return
    for hero in heroes:
        name = HERO_EMOJI_PREFIX + hero.slug
        emoji = existing.get(name)
        if emoji is None:
            try:
                emoji = await client.create_application_emoji(
                    name=name, image=await dota.download(hero.icon))
            except (dota.DotaError, discord.HTTPException) as exc:
                print(f"could not upload the {hero.name} emoji: {exc}", flush=True)
                continue
        HERO_EMOJI[hero.id] = str(emoji)


def dota_run(results: tuple[dota.Result, ...]) -> str:
    """Each match as a W or L followed by its hero's icon. Matches are split by an
    em space, which Discord keeps where it would collapse a run of plain spaces."""
    return " ".join(result_run((result.won,)) + HERO_EMOJI.get(result.hero_id, "")
                         for result in results)


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


@tree.command(name="whoplayeddota", description="Everyone linked here who has played Dota 2 since 03:00 GMT")
@app_commands.guild_only()
async def whoplayeddota(interaction: discord.Interaction):
    await interaction.response.defer()
    entries = links.dota_for_guild(interaction.guild.id)
    if not entries:
        raise dota.DotaError("Nobody here has linked a Dota 2 account yet. Try `/logindota`.")

    played, unavailable = [], 0
    for user_id, entry in entries.items():
        # One request per player, so one failing must not sink the whole command.
        try:
            results = await dota.results_today(entry["account_id"])
        except dota.DotaError:
            unavailable += 1
            continue
        if not results:
            continue
        name = await dota.steam_name(entry["account_id"]) or entry["name"]
        if name != entry["name"]:
            links.link_dota(interaction.guild.id, int(user_id), entry["account_id"], name)
        played.append((name, results))

    if not played:
        raise dota.DotaError("Nobody has played Dota yet today.")

    def wins(results: tuple[dota.Result, ...]) -> int:
        return sum(result.won for result in results)

    played.sort(key=lambda row: 2 * wins(row[1]) - len(row[1]), reverse=True)
    rows = []
    for name, results in played:
        rows.append(f"**{discord.utils.escape_markdown(name)}** — "
                    f"{wins(results)}W {len(results) - wins(results)}L  {dota_run(results)}")

    embed = discord.Embed(title="Played Dota today", description="\n".join(rows),
                          colour=DOTA_COLOUR)
    footer = f"{len(played)} of {len(entries)} linked • since 03:00 GMT"
    if unavailable:
        footer += f" • {unavailable} unavailable"
    embed.set_footer(text=footer)
    await interaction.followup.send(embed=embed)


@tree.command(name="logindota", description="Link your Dota 2 account")
@app_commands.describe(profile="Your Dotabuff or Steam profile link")
@app_commands.guild_only()
async def logindota(interaction: discord.Interaction, profile: str):
    await interaction.response.defer(ephemeral=True)
    player = await dota.player(await dota.account_id(profile))
    links.link_dota(interaction.guild.id, interaction.user.id, player.account_id, player.name)
    await interaction.followup.send(
        f"Linked to **{discord.utils.escape_markdown(player.name)}** — "
        f"<https://www.dotabuff.com/players/{player.account_id}>", ephemeral=True)


@tree.command(name="logoutdota", description="Unlink your Dota 2 account")
@app_commands.guild_only()
async def logoutdota(interaction: discord.Interaction):
    removed = links.unlink_dota(interaction.guild.id, interaction.user.id)
    await interaction.response.send_message(
        "Unlinked." if removed else "You are not linked.", ephemeral=True)


def owner_only():
    """Restrict to the account in OWNER_ID. Discord has no owner-only visibility,
    so the command is still listed for everyone; it just refuses."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return OWNER_ID != 0 and interaction.user.id == OWNER_ID
    predicate.owner_only = True  # /help reads this to keep these out of the list
    return app_commands.check(predicate)


@tree.command(name="autobet", description="Play a game repeatedly and report the return")
@app_commands.describe(game="Which game", bet="Coins per round",
                       times="How many rounds", notation="Dice to roll, when playing dice")
@app_commands.choices(game=[app_commands.Choice(name="slot", value="slot"),
                           app_commands.Choice(name="dice", value="dice")])
@app_commands.guild_only()
@owner_only()
async def autobet(interaction: discord.Interaction, game: app_commands.Choice[str], bet: int,
                  times: app_commands.Range[int, 1, 1000] = 100, notation: str = "2d6"):
    income = weekly_income(interaction.guild.id, interaction.user.id)
    start = economy.account(interaction.guild.id, interaction.user.id, income)["coins"]
    economy.check_bet(start, bet)
    count, sides = games.parse_dice(notation) if game.value == "dice" else (0, 0)

    # The whole run is played in memory and settled once, rather than writing the
    # balance file twice per round.
    coins, staked, returned, rounds = start, 0, 0, 0
    tally = Counter()
    for _ in range(times):
        if economy.max_bet(coins) < bet:
            break
        if game.value == "slot":
            _, multiple = games.spin()
            tally[{0: "lose", 2: "pair", games.TRIPLE_PAYOUT: "three of a kind",
                   games.SEVENS_PAYOUT: "three sevens"}[multiple]] += 1
        else:
            _, _, multiple = games.dice_round(count, sides)
            tally[{0: "lose", 1: "tie", 2: "win"}[multiple]] += 1
        coins += bet * multiple - bet
        staked += bet
        returned += bet * multiple
        rounds += 1

    economy.settle(interaction.guild.id, interaction.user.id, income, coins - start)
    label = f"{count}d{sides}" if game.value == "dice" else "slot"
    embed = discord.Embed(
        title=f"Autobet — {label}, {bet} coins × {rounds}",
        description=(f"Balance **{start}** → **{coins}**\n"
                     f"Staked {staked} · returned {returned} · net **{coins - start:+}**\n"
                     f"Return **{100 * returned / staked:.2f}%**" if rounds else
                     "Stopped before the first round."),
        colour=0xFFC800 if coins >= start else 0xCA5325)
    if rounds:
        embed.add_field(name="Outcomes", inline=False, value=" · ".join(
            f"{name} {n}" for name, n in tally.most_common()))
    if rounds < times:
        embed.set_footer(text=f"Stopped after {rounds} of {times} — funds ran low.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


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


@tree.command(name="logindotaforce", description="Link someone else's Dota 2 account")
@app_commands.describe(member="Who to link", profile="Their Dotabuff or Steam profile link")
@app_commands.guild_only()
@owner_only()
async def logindotaforce(interaction: discord.Interaction, member: discord.Member, profile: str):
    await interaction.response.defer(ephemeral=True)
    player = await dota.player(await dota.account_id(profile))
    links.link_dota(interaction.guild.id, member.id, player.account_id, player.name)
    await interaction.followup.send(
        f"Linked {member.mention} to **{discord.utils.escape_markdown(player.name)}** — "
        f"<https://www.dotabuff.com/players/{player.account_id}>", ephemeral=True)


@tree.command(name="logoutdotaforce", description="Unlink someone else's Dota 2 account")
@app_commands.describe(member="Who to unlink")
@app_commands.guild_only()
@owner_only()
async def logoutdotaforce(interaction: discord.Interaction, member: discord.Member):
    removed = links.unlink_dota(interaction.guild.id, member.id)
    await interaction.response.send_message(
        f"Unlinked {member.mention}." if removed else f"{member.mention} is not linked.",
        ephemeral=True)


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
    shown = (FaceitError, dota.DotaError, elo_roles.RoleSyncError, economy.EconomyError)
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
