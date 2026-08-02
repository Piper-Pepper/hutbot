import asyncio
import os
import traceback

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# -----------------------------------------------------------
# Load environment variables
# -----------------------------------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in .env")

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
# Tree Error Handler
# -----------------------------------------------------------
@tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandNotFound):
        # stale cached interaction ignorieren
        return
    print("❌ App command tree error:")
    traceback.print_exception(type(error), error, error.__traceback__)

# -----------------------------------------------------------
# Bot Ready Event
# -----------------------------------------------------------
@bot.event
async def on_ready():
    global synced_once
    print(f"✅ Bot connected as {bot.user}!")

    if not synced_once:
        try:
            print("🌍 Syncing commands globally...")
            synced = await tree.sync()
            print(f"✅ Synced {len(synced)} global command(s).")
        except Exception:
            print("❌ Failed to sync commands:")
            traceback.print_exc()

        synced_once = True

# -----------------------------------------------------------
# MAIN – load all extensions & start bot with retry handling
# -----------------------------------------------------------
async def main():
    async with bot:
        extensions = [
            "pepper",
            "hutmember",
            "anti-mommy",
            "status_manager",
            "hut_dm",
            "hut_dm_app",
            "venice_cog",
            "video_cog",
            "hutvote",
            "riddle",
            "venice_face_cog",
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

        try:
            cmds = tree.get_commands()
            print(f"🧾 Local app commands in tree: {len(cmds)}")
            for c in cmds:
                print(f"   - /{c.name}")
        except Exception:
            traceback.print_exc()

        initial_wait = 10
        print(f"⏳ Waiting {initial_wait}s before first connection attempt...")
        await asyncio.sleep(initial_wait)

        max_attempts = 10
        sleep_on_rate_limit = 60

        for attempt in range(1, max_attempts + 1):
            try:
                print(f"🔌 Attempt {attempt} to connect...")
                await bot.start(TOKEN)
                break
            except discord.HTTPException as e:
                if getattr(e, "status", None) == 429 or "429" in str(e):
                    print(f"⚠️ Rate limited by Discord. Waiting {sleep_on_rate_limit}s before retry...")
                    await asyncio.sleep(sleep_on_rate_limit)
                    continue
                print("❌ HTTPException during start:")
                traceback.print_exc()
                break
            except Exception:
                print("❌ Unexpected error during start:")
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