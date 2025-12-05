import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import traceback

# -----------------------------------------------------------
# Load environment variables
# -----------------------------------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")

DEV_GUILD = discord.Object(id=int(DEV_GUILD_ID)) if DEV_GUILD_ID else None

# -----------------------------------------------------------
# Intents
# -----------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True
intents.guilds = True

# -----------------------------------------------------------
# Bot Setup
# -----------------------------------------------------------
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
synced_once = False

# -----------------------------------------------------------
# Bot Ready Event
# -----------------------------------------------------------
@bot.event
async def on_ready():
    global synced_once
    print(f"✅ Bot connected as {bot.user}!")

    if not synced_once:
        try:
            if DEV_GUILD:
                print("🧪 Syncing commands to DEV guild...")
                synced = await tree.sync(guild=DEV_GUILD)
            else:
                print("🌍 Syncing commands globally...")
                synced = await tree.sync()

            print(f"✅ Synced {len(synced)} command(s).")
        except Exception:
            print("❌ Failed to sync commands:")
            traceback.print_exc()

        synced_once = True

# -----------------------------------------------------------
# MAIN – load all extensions & start bot
# -----------------------------------------------------------
async def main():
    async with bot:
        extensions = [
            "pepper",
            "hutmember",
            "anti-mommy",
            "ticket",
            "status_manager",
            "hut_dm",
            "hut_dm_app",
            "venice_cog",
            "gather",
            "reset",
            "riddle",
            "hutvote_new",  # <- nur die neue Version laden
            "hutthreadvote",
            "character_creator",
            "riddle_post"
        ]

        for ext in extensions:
            try:
                # Unload alte Version falls schon geladen
                if ext in bot.extensions:
                    await bot.unload_extension(ext)
                    print(f"♻️ Unloaded old extension: {ext}")

                # Load neue Version
                await bot.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception:
                print(f"❌ Fehler beim Laden von {ext}:")
                traceback.print_exc()

        # Load persistent birthday view
        try:
            from birthday_cog import BirthdayButtonView
            bot.add_view(BirthdayButtonView(bot))
            print("🎂 Birthday view loaded.")
        except Exception:
            print("⚠️ Birthday view not loaded:")
            traceback.print_exc()

        # Start bot
        try:
            await bot.start(TOKEN)
        except Exception:
            print("❌ Error starting bot:")
            traceback.print_exc()

# -----------------------------------------------------------
# ENTRYPOINT
# -----------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot manually stopped.")
    except Exception:
        print("❌ Unexpected error:")
        traceback.print_exc()
