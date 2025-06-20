import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True
intents.guilds = True
intents.message_content = True
intents.members = True  # falls mit Memberinfos gearbeitet wird

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

synced_once = False  # sync slash commands nur einmal

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

async def main():
    async with bot:
        # Lade alle Cogs/extensions
        await bot.load_extension("generate_cleaner")  # dein Cog für /generate cleanup
        await bot.load_extension("pepper")
        await bot.load_extension("hutmember")
        await bot.load_extension("dm_logger")
        await bot.load_extension("anti-mommy")
        await bot.load_extension("auto_kick_mommy")
        await bot.load_extension("dm_forwarder")
        await bot.load_extension("ticket")
        await bot.load_extension("riddle")  # Riddle Cog

        # Lade Riddle Daten & persistent views
        from riddle import riddle_manager, setup_persistent_views
        await riddle_manager.load_data()
        await setup_persistent_views(bot)

        # Starte Bot
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
