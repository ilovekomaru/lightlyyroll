"""Games of chance: slots, wheel, dice, coin flip. No stakes, no FACEIT data."""
import asyncio
import random
import re

import discord
from discord import app_commands

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

    def __init__(self, challenger: discord.User):
        super().__init__(timeout=90)
        self.challenger = challenger
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

        self.opponent = interaction.user
        button.disabled = True
        self.stop()

        side = random.choice(["Heads", "Tails"])
        winner = self.challenger if side == "Heads" else self.opponent
        embed = discord.Embed(
            title=f"\U0001FA99 {side}",
            description=(f"{self.challenger.mention} called heads, "
                         f"{self.opponent.mention} got tails.\n\n"
                         f"**{winner.display_name} wins.**"),
            colour=PAIR_COLOUR)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


def setup(tree: app_commands.CommandTree) -> None:

    @tree.command(name="slot", description="Spin the slot machine")
    async def slot(interaction: discord.Interaction):
        reels = random.choices(SYMBOLS, weights=WEIGHTS, k=3)

        embed = discord.Embed(title="\U0001F3B0  S L O T S  \U0001F3B0",
                              description=_panel([BLANK] * 3), colour=LOSE_COLOUR)
        await interaction.response.send_message(embed=embed)

        # Reveal one reel at a time by editing, which is the whole animation.
        for stop in range(1, 4):
            await asyncio.sleep(REEL_PAUSE)
            shown = reels[:stop] + [BLANK] * (3 - stop)
            embed.description = _panel(shown)
            if stop == 3:
                line, embed.colour = outcome(reels)
                embed.description = f"{_panel(reels)}\n\n{line}"
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
    @app_commands.describe(notation="Dice to roll, like 2d6, 1d20 or d100")
    async def dice(interaction: discord.Interaction, notation: str = "1d6"):
        try:
            count, sides = parse_dice(notation)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        rolls = [random.randint(1, sides) for _ in range(count)]
        embed = discord.Embed(title=f"\U0001F3B2 {count}d{sides}",
                              description=" + ".join(str(roll) for roll in rolls)
                              if count > 1 else f"**{rolls[0]}**",
                              colour=PAIR_COLOUR)
        if count > 1:
            embed.description += f"\n\n**{sum(rolls)}**"
        await interaction.response.send_message(embed=embed)

    @tree.command(name="coinflip", description="Flip a coin against whoever joins first")
    async def coinflip(interaction: discord.Interaction):
        embed = discord.Embed(
            title="\U0001FA99 Coin flip",
            description=(f"{interaction.user.mention} is flipping a coin.\n"
                         "First to join takes tails."),
            colour=LOSE_COLOUR)
        await interaction.response.send_message(embed=embed, view=CoinFlip(interaction.user))
