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
# MAIN – load all extensions & start bot with rate-limit handling
# -----------------------------------------------------------
async def main():
    async with bot:
        # Load extensions
        extensions = [
            "pepper",
            "hutmember",
            "anti-mommy",
            "status_manager",
            "hut_dm",
            "hut_dm_app",
            "venice_cog",
            "gather",
            "reset",
            "hutvote",
            "champions_cog",
            "video_cog",
            "hutthreadvote"
        ]

        for ext in extensions:
            try:
                if ext in bot.extensions:
                    await bot.unload_extension(ext)
                    print(f"♻️ Unloaded old extension: {ext}")
                await bot.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception:
                print(f"❌ Fehler beim Laden von {ext}:")
                traceback.print_exc()



        # Initial wait (Container, Cloudflare, etc.)
        initial_wait = 10  # Sekunden, kann auch 30 oder 60 sein
        print(f"⏳ Waiting {initial_wait}s before first connection attempt...")
        await asyncio.sleep(initial_wait)

        # Retry loop für Rate-Limits
        max_attempts = 10
        sleep_on_rate_limit = 60  # Sekunden warten bei 429, kann länger sein

        for attempt in range(1, max_attempts + 1):
            try:
                print(f"🔌 Attempt {attempt} to connect...")
                await bot.start(TOKEN)
                break  # Erfolgreich verbunden
            except discord.errors.HTTPException as e:
                if "429" in str(e):
                    print(f"⚠️ Rate limited by Discord. Waiting {sleep_on_rate_limit}s before retry...")
                    await asyncio.sleep(sleep_on_rate_limit)
                else:
                    print("❌ Other HTTPException occurred:")
                    traceback.print_exc()
                    break
            except Exception:
                print("❌ Unexpected error occurred during start:")
                traceback.print_exc()
                break
        else:
            print("🛑 Could not connect after multiple attempts. Exiting.")

# -----------------------------------------------------------
# ENTRYPOINT
# -----------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot manually stopped.")
    except Exception:
        print("❌ Unexpected error in main loop:")
        traceback.print_exc()
