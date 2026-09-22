"""Games of chance: slots, wheel, dice, coin flip. No stakes, no FACEIT data."""
import asyncio
import random
import re

import discord
from discord import app_commands

import economy

# Multipliers on the stake, tuned against the measured spin distribution to leave
# the house a few percent: coins should drain slowly, not evaporate.
PAIR_PAYOUT = 2
TRIPLE_PAYOUT = 10
SEVENS_PAYOUT = 200

# Eight fairly flat weights, because the chance of three of a kind is the sum of
# each symbol's cubed probability: fewer or more lopsided symbols make jackpots
# far too common. These give a pair ~34% of spins, three of a kind ~1 in 58, and
# three sevens ~1 in 2000 — rare enough to chase, common enough to ever happen.
SYMBOLS = ["\U0001F352", "\U0001F34B", "\U0001F34A", "\U0001F349",
           "\U0001F514", "⭐", "\U0001F48E", "7️⃣"]
WEIGHTS = [16, 15, 14, 13, 12, 11, 11, 8]
BLANK = "❓"
REEL_PAUSE = 0.65

LOSE_COLOUR = 0x3A3A42
PAIR_COLOUR = 0xCA5325
JACKPOT_COLOUR = 0xFFC800

MAX_DICE, MAX_SIDES = 50, 1000
MAX_WHEEL_OPTIONS = 20
DICE_PATTERN = re.compile(r"^\s*(\d*)\s*d\s*(\d+)\s*$", re.IGNORECASE)


def _panel(reels: list[str]) -> str:
    return "┃ " + " ┃ ".join(reels) + " ┃"


def _player(embed: discord.Embed, member) -> discord.Embed:
    """Name and avatar in the embed's author line, so it is clear who is playing."""
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return embed


def payout_multiple(reels: list[str]) -> int:
    """What the stake is multiplied by; 0 means the stake is lost."""
    if reels[0] == reels[1] == reels[2]:
        return SEVENS_PAYOUT if reels[0] == SYMBOLS[-1] else TRIPLE_PAYOUT
    return PAIR_PAYOUT if any(reels.count(s) == 2 for s in set(reels)) else 0


def spin() -> tuple[list[str], int]:
    """One spin: the reels and what the stake is multiplied by."""
    reels = random.choices(SYMBOLS, weights=WEIGHTS, k=3)
    return reels, payout_multiple(reels)


def dice_round(count: int, sides: int) -> tuple[int, int, int]:
    """One roll against the house: your total, theirs, and the stake multiplier."""
    mine = sum(random.randint(1, sides) for _ in range(count))
    house = sum(random.randint(1, sides) for _ in range(count))
    return mine, house, 2 if mine > house else (1 if mine == house else 0)


def outcome(reels: list[str]) -> tuple[str, int]:
    """Result line and embed colour for a finished spin."""
    if reels[0] == reels[1] == reels[2]:
        prize = "JACKPOT" if reels[0] != SYMBOLS[-1] else "MEGA JACKPOT"
        return f"**{prize}** — three {reels[0]}", JACKPOT_COLOUR
    for symbol in set(reels):
        if reels.count(symbol) == 2:
            return f"Two {symbol} — small win", PAIR_COLOUR
    return "No win", LOSE_COLOUR


def parse_dice(notation: str) -> tuple[int, int]:
    match = DICE_PATTERN.match(notation)
    if not match:
        raise ValueError("Use dice notation like `2d6`, `1d20` or `d100`.")
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    if not 1 <= count <= MAX_DICE:
        raise ValueError(f"Roll between 1 and {MAX_DICE} dice.")
    if not 2 <= sides <= MAX_SIDES:
        raise ValueError(f"Dice need between 2 and {MAX_SIDES} sides.")
    return count, sides


def parse_options(raw: str) -> list[str]:
    options = [part.strip() for part in raw.split(",") if part.strip()]
    if len(options) < 2:
        raise ValueError("Give at least two options, separated by commas.")
    if len(options) > MAX_WHEEL_OPTIONS:
        raise ValueError(f"That is more than {MAX_WHEEL_OPTIONS} options.")
    return options


class CoinFlip(discord.ui.View):
    """A challenge anyone but the caller can accept; the first to click plays."""

    def __init__(self, challenger: discord.User, stake: int, income_for):
        super().__init__(timeout=90)
        self.challenger = challenger
        self.stake = stake
        self.income_for = income_for
        self.opponent: discord.User | None = None

    @discord.ui.button(label="Join the flip", style=discord.ButtonStyle.primary, emoji="\U0001FA99")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.challenger.id:
            await interaction.response.send_message(
                "You cannot flip against yourself.", ephemeral=True)
            return
        if self.opponent is not None:
            await interaction.response.send_message(
                "Someone already joined this flip.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        joiner_income = self.income_for(guild_id, interaction.user.id)
        joiner = economy.account(guild_id, interaction.user.id, joiner_income)
        if joiner["coins"] < self.stake:
            await interaction.response.send_message(
                f"You need {self.stake} coins to join. You have {joiner['coins']}.",
                ephemeral=True)
            return

        # The challenger was checked when they opened the flip, but that was up to
        # 90 seconds ago and they may have spent the coins since.
        challenger_income = self.income_for(guild_id, self.challenger.id)
        challenger = economy.account(guild_id, self.challenger.id, challenger_income)
        if challenger["coins"] < self.stake:
            button.disabled = True
            self.stop()
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="\U0001FA99 Flip cancelled",
                    description=f"**{self.challenger.display_name}** can no longer cover "
                                f"the {self.stake} coin stake.",
                    colour=LOSE_COLOUR),
                view=self)
            return

        self.opponent = interaction.user
        button.disabled = True
        self.stop()

        side = random.choice(["Heads", "Tails"])
        winner, loser = ((self.challenger, self.opponent) if side == "Heads"
                         else (self.opponent, self.challenger))
        economy.settle_pot(guild_id, winner.id, self.income_for(guild_id, winner.id),
                           loser.id, self.income_for(guild_id, loser.id), self.stake)

        embed = discord.Embed(
            title=f"\U0001FA99 {side}",
            description=(f"**{self.challenger.display_name}** called heads, "
                         f"**{self.opponent.display_name}** got tails.\n\n"
                         f"**{winner.display_name} wins {self.stake} coins.**"),
            colour=PAIR_COLOUR)
        # The winner takes the avatar slot, so the result reads at a glance.
        _player(embed, winner)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


def setup(tree: app_commands.CommandTree, income_for) -> None:
    """`income_for(guild_id, user_id)` gives a member's weekly coin income."""

    async def stake(interaction: discord.Interaction, bet: int) -> int | None:
        """Take the bet up front. None means the caller was told why not; otherwise
        the weekly income that happened to land on the way in, so it can be shown —
        coins appearing mid-game with no explanation looks like a bug."""
        income = income_for(interaction.guild.id, interaction.user.id)
        state = economy.account(interaction.guild.id, interaction.user.id, income)
        try:
            economy.check_bet(state["coins"], bet)
        except economy.EconomyError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return None
        economy.settle(interaction.guild.id, interaction.user.id, income, -bet)
        return state["credited"]

    def pay(interaction: discord.Interaction, amount: int) -> int:
        income = income_for(interaction.guild.id, interaction.user.id)
        return economy.settle(interaction.guild.id, interaction.user.id, income, amount)

    @tree.command(name="slot", description="Spin the slot machine")
    @app_commands.describe(bet="Coins to stake")
    @app_commands.guild_only()
    async def slot(interaction: discord.Interaction, bet: int):
        credited = await stake(interaction, bet)
        if credited is None:
            return
        reels = random.choices(SYMBOLS, weights=WEIGHTS, k=3)
        header = f"Staked **{bet}** coins"
        if credited:
            header = f"Weekly income of **{credited}** landed · " + header

        embed = discord.Embed(title="\U0001F3B0  S L O T S  \U0001F3B0",
                              description=f"{header}\n\n{_panel([BLANK] * 3)}",
                              colour=LOSE_COLOUR)
        _player(embed, interaction.user)
        await interaction.response.send_message(embed=embed)

        # Reveal one reel at a time by editing, which is the whole animation.
        for stop in range(1, 4):
            await asyncio.sleep(REEL_PAUSE)
            shown = reels[:stop] + [BLANK] * (3 - stop)
            embed.description = f"{header}\n\n{_panel(shown)}"
            if stop == 3:
                line, embed.colour = outcome(reels)
                multiple = payout_multiple(reels)
                coins = pay(interaction, bet * multiple)
                won = bet * multiple - bet
                embed.description = (f"{header}\n\n{_panel(reels)}\n\n{line}\n"
                                     f"**{won:+}** coins · balance {coins}")
            await interaction.edit_original_response(embed=embed)

    @tree.command(name="wheel", description="Spin a wheel of your own options")
    @app_commands.describe(options="Options separated by commas")
    async def wheel(interaction: discord.Interaction, options: str):
        try:
            choices = parse_options(options)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        winner = random.choice(choices)
        listed = "\n".join(f"• **{option}**" if option == winner else f"• {option}"
                           for option in choices)
        embed = discord.Embed(title="\U0001F3AF The wheel picked",
                              description=f"{listed}\n\n**{winner}**", colour=PAIR_COLOUR)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="dice", description="Roll dice, e.g. 2d6 or 1d20")
    @app_commands.describe(notation="Dice to roll, like 2d6, 1d20 or d100",
                           bet="Coins to stake against the house; leave out to just roll")
    @app_commands.guild_only()
    async def dice(interaction: discord.Interaction, notation: str = "1d6", bet: int = 0):
        try:
            count, sides = parse_dice(notation)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls)
        embed = discord.Embed(title=f"\U0001F3B2 {count}d{sides}", colour=PAIR_COLOUR)
        detail = " + ".join(str(roll) for roll in rolls) if count > 1 else ""

        if not bet:
            embed.description = (f"{detail}\n\n**{total}**" if detail else f"**{total}**")
            await interaction.response.send_message(embed=embed)
            return

        credited = await stake(interaction, bet)
        if credited is None:
            return
        house = sum(random.randint(1, sides) for _ in range(count))
        if total > house:
            line, delta, embed.colour = "**You win.**", bet * 2, JACKPOT_COLOUR
        elif total < house:
            line, delta, embed.colour = "**The house wins.**", 0, LOSE_COLOUR
        else:
            line, delta = "**Tie** — your bet is returned.", bet

        coins = pay(interaction, delta)
        _player(embed, interaction.user)
        embed.description = (
            (f"Weekly income of **{credited}** landed · " if credited else "")
            + f"Staked **{bet}** coins\n\n"
            + (f"{detail}\n" if detail else "")
            + f"You **{total}** · house **{house}**\n\n"
            f"{line}\n**{delta - bet:+}** coins · balance {coins}")
        await interaction.response.send_message(embed=embed)

    @tree.command(name="coinflip", description="Flip a coin against whoever joins first")
    @app_commands.describe(bet="Coins each side stakes")
    @app_commands.guild_only()
    async def coinflip(interaction: discord.Interaction, bet: int):
        income = income_for(interaction.guild.id, interaction.user.id)
        coins = economy.account(interaction.guild.id, interaction.user.id, income)["coins"]
        try:
            economy.check_bet(coins, bet)
        except economy.EconomyError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        # Neither side is charged until someone joins, so an unanswered
        # challenge costs the caller nothing.
        embed = discord.Embed(
            title="\U0001FA99 Coin flip",
            description=(f"Flipping for **{bet}** coins.\n"
                         "First to join takes tails."),
            colour=LOSE_COLOUR)
        _player(embed, interaction.user)
        await interaction.response.send_message(
            embed=embed, view=CoinFlip(interaction.user, bet, income_for))
