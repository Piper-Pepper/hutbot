import asyncio
import datetime
import subprocess
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import os

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True
intents.guilds = True
intents.message_content = True
intents.members = True  # falls du mit Mitgliederinformationen arbeitest

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1381375333491675217/idcard_small.png"
DEFAULT_HUTMEMBER_IMAGE_URL = DEFAULT_IMAGE_URL

synced_once = False  # wird genutzt, um tree.sync() nur einmal durchzuführen


# --- Slash Command Cleanup (z.B. /generate nach 13 Sek löschen) ---
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


# --- on_ready: Präsenz setzen & Slash Commands syncen ---
@bot.event
async def on_ready():
    global synced_once
    print(f"✅ Bot connected as {bot.user}!")
    await bot.change_presence(activity=discord.Game(name=".. with her Cum-Kitty"))

    if not synced_once:
        try:
            print("🔄 Syncing slash commands...")
            synced = await tree.sync()
            print(f"✅ Synced {len(synced)} command(s).")
            synced_once = True
        except Exception as e:
            print(f"❌ Failed to sync commands: {e}")


# --- Bot Main Runner ---
async def main():
    async with bot:
        await bot.load_extension("pepper")
        await bot.load_extension("hutmember")
        await bot.load_extension("dm_logger")
        await bot.load_extension("anti-mommy")
        await bot.load_extension("auto_kick_mommy")
        await bot.load_extension("dm_forwarder")
        await bot.load_extension("ticket")
        # await bot.load_extension("riddle_commands")

        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
