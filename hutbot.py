import asyncio
import os
import traceback

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# -----------------------------------------------------------
# Helpers
# -----------------------------------------------------------
def env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}

def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None

# -----------------------------------------------------------
# Load environment variables
# -----------------------------------------------------------
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID_RAW = os.getenv("DEV_GUILD_ID")
DEV_GUILD_ID = parse_int(DEV_GUILD_ID_RAW)
DEV_GUILD = discord.Object(id=DEV_GUILD_ID) if DEV_GUILD_ID else None

# Optional toggles
# 1 = einmal globale Commands löschen (gegen doppelte Einträge), dann wieder auf 0 setzen
PURGE_GLOBAL_ONCE = env_bool("PURGE_GLOBAL_ONCE", False)
# optionaler global sync zusätzlich zum DEV sync
SYNC_GLOBAL_ON_READY = env_bool("SYNC_GLOBAL_ON_READY", False)

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env")

if DEV_GUILD_ID_RAW and DEV_GUILD_ID is None:
    print(f"⚠️ DEV_GUILD_ID ungültig: {DEV_GUILD_ID_RAW!r}. Fallback auf global sync.")

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
# Tree Error Handler (stale slash interactions)
# -----------------------------------------------------------
@tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandNotFound):
        # Alte gecachte Interactions (z.B. gelöschte Commands) sauber ignorieren
        return

    print("❌ App command tree error:")
    traceback.print_exception(type(error), error, error.__traceback__)

# -----------------------------------------------------------
# Sync Helper
# -----------------------------------------------------------
async def sync_commands_once():
    global synced_once
    if synced_once:
        return

    try:
        if DEV_GUILD:
            print("🧪 DEV sync (copy global -> guild)...")

            # Optional: einmal global purge (gegen doppelte Commands)
            if PURGE_GLOBAL_ONCE:
                print("🧹 PURGE_GLOBAL_ONCE=1 -> clearing GLOBAL commands...")
                tree.clear_commands(guild=None)
                purged = await tree.sync()
                print(f"🧹 Global commands after purge sync: {len(purged)}")

            # DEV guild sauber neu aufbauen
            tree.clear_commands(guild=DEV_GUILD)
            tree.copy_global_to(guild=DEV_GUILD)
            synced = await tree.sync(guild=DEV_GUILD)
            print(f"✅ Synced {len(synced)} DEV guild command(s).")

            if SYNC_GLOBAL_ON_READY:
                print("🌍 Additional global sync enabled...")
                gsynced = await tree.sync()
                print(f"✅ Synced {len(gsynced)} global command(s).")
        else:
            print("🌍 Syncing commands globally...")
            synced = await tree.sync()
            print(f"✅ Synced {len(synced)} global command(s).")

    except Exception:
        print("❌ Failed to sync commands:")
        traceback.print_exc()

    synced_once = True

# -----------------------------------------------------------
# Bot Ready Event
# -----------------------------------------------------------
@bot.event
async def on_ready():
    print(f"✅ Bot connected as {bot.user}!")
    await sync_commands_once()

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
            "gather",
            "reset",
            "hutvote",
            "champions_cog",
            "riddle",
            "hutthreadvote",
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

        # Debug: lokale App-Commands anzeigen
        try:
            cmds = tree.get_commands()
            print(f"🧾 Local app commands in tree: {len(cmds)}")
            for c in cmds:
                print(f"   - /{c.name}")
        except Exception:
            print("⚠️ Konnte lokale Command-Liste nicht anzeigen.")
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
                status = getattr(e, "status", None)
                if status == 429 or "429" in str(e):
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