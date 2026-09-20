import os
import random
import sys

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN:
    sys.exit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill in your bot token.")

client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)


@tree.command(name="roll", description="Roll a random number")
@app_commands.describe(maximum="Upper bound of the roll (default 100)")
async def roll(interaction: discord.Interaction, maximum: app_commands.Range[int, 1, 1_000_000] = 100):
    await interaction.response.send_message(f"\U0001F3B2 {random.randint(0, maximum)} (0-{maximum})")


@client.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print(f"Logged in as {client.user}", flush=True)


client.run(TOKEN)
