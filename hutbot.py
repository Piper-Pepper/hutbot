import asyncio
import datetime
import subprocess
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import os

intents = discord.Intents.all()
intents.members = True
intents.message_content = True

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1381375333491675217/idcard_small.png"
DEFAULT_HUTMEMBER_IMAGE_URL = DEFAULT_IMAGE_URL


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith("/generate"):
        try:
            await asyncio.sleep(13)
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
    await bot.process_commands(message)


async def main():
    async with bot:
        # Lade die Extensions (Cogs)
        await bot.load_extension("pepper")
        await bot.load_extension("hutmember")
        await bot.load_extension("riddle_cog")

@bot.event
async def on_ready():
    print(f"✅ Bot connected as {bot.user}!")
    await bot.change_presence(activity=discord.Game(name=".. with her Cum-Kitty"))
    try:
        print("Starting to sync commands...")
        synced = await tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync: {e}")
        
async def main():
    async with bot:
        # Lade die Extensions (Cogs)
        await bot.load_extension("pepper")
        await bot.load_extension("hutmember")
        await bot.load_extension("riddle_cog")


if __name__ == "__main__":
    asyncio.run(main())
