import asyncio
import logging
import os
import traceback

import discord
from discord.ext import commands
from dotenv import load_dotenv

# -----------------------------------------------------------
# Config / Env
# -----------------------------------------------------------
load_dotenv()

TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
DEV_GUILD_ID_RAW = (os.getenv("DEV_GUILD_ID") or "").strip()

DEV_GUILD = None
if DEV_GUILD_ID_RAW:
    try:
        DEV_GUILD = discord.Object(id=int(DEV_GUILD_ID_RAW))
    except ValueError:
        print(f"⚠️ DEV_GUILD_ID is invalid: {DEV_GUILD_ID_RAW}. Falling back to global sync.")

# -----------------------------------------------------------
# Logging
# -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("hutbot")

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
bot.synced_once = False  # type: ignore[attr-defined]

EXTENSIONS = [
    "pepper",
    "hutmember",
    "anti-mommy",      # falls das nicht lädt: Dateiname/Modulname prüfen
    "ticket",
    "status_manager",
    "hut_dm",
    "hut_dm_app",
    "venice_cog",
    "gather",
    "reset",
    "riddle",
    "hutvote",
    "birthday",
    "champions_cog",
    "hutthreadvote",
]

# -----------------------------------------------------------
# Helpers
# -----------------------------------------------------------
def is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, discord.HTTPException):
        if getattr(exc, "status", None) == 429:
            return True
        if "429" in str(exc):
            return True
    return False


async def load_extensions() -> list[str]:
    failed: list[str] = []

    for ext in EXTENSIONS:
        try:
            if ext in bot.extensions:
                await bot.unload_extension(ext)
                logger.info("♻️ Unloaded old extension: %s", ext)

            await bot.load_extension(ext)
            logger.info("✅ Loaded extension: %s", ext)
        except Exception:
            failed.append(ext)
            logger.error("❌ Fehler beim Laden von %s:", ext)
            traceback.print_exc()

    return failed


async def sync_commands_once():
    if getattr(bot, "synced_once", False):
        return

    try:
        if DEV_GUILD:
            logger.info("🧪 Syncing commands to DEV guild...")
            synced = await tree.sync(guild=DEV_GUILD)
        else:
            logger.info("🌍 Syncing commands globally...")
            synced = await tree.sync()

        logger.info("✅ Synced %s command(s).", len(synced))
        bot.synced_once = True  # type: ignore[attr-defined]
    except Exception:
        logger.error("❌ Failed to sync commands:")
        traceback.print_exc()


# -----------------------------------------------------------
# Events
# -----------------------------------------------------------
@bot.event
async def on_ready():
    logger.info("✅ Bot connected as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")
    await sync_commands_once()


# -----------------------------------------------------------
# MAIN
# -----------------------------------------------------------
async def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing or empty.")

    async with bot:
        failed = await load_extensions()
        if failed:
            logger.warning("⚠️ Some extensions failed to load: %s", ", ".join(failed))

        initial_wait = 10
        logger.info("⏳ Waiting %ss before first connection attempt...", initial_wait)
        await asyncio.sleep(initial_wait)

        max_attempts = 10
        base_wait_seconds = 30

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info("🔌 Attempt %s/%s to connect...", attempt, max_attempts)
                await bot.start(TOKEN)
                break  # wenn bot sauber stoppt
            except discord.LoginFailure:
                logger.error("🛑 Invalid Discord token (LoginFailure). Stop retries.")
                break
            except discord.HTTPException as e:
                if is_rate_limit_error(e):
                    wait_s = min(300, base_wait_seconds * attempt)
                    logger.warning("⚠️ Rate limited by Discord. Waiting %ss before retry...", wait_s)
                    await asyncio.sleep(wait_s)
                    continue

                logger.error("❌ HTTPException during bot.start():")
                traceback.print_exc()
                break
            except (OSError, asyncio.TimeoutError) as e:
                wait_s = min(300, base_wait_seconds * attempt)
                logger.warning("🌐 Network error (%r). Retry in %ss...", e, wait_s)
                await asyncio.sleep(wait_s)
                continue
            except Exception:
                logger.error("❌ Unexpected error during bot.start():")
                traceback.print_exc()
                break
        else:
            logger.error("🛑 Could not connect after %s attempts. Exiting.", max_attempts)


# -----------------------------------------------------------
# ENTRYPOINT
# -----------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot manually stopped.")
    except Exception:
        logger.error("❌ Unexpected error in main loop:")
        traceback.print_exc()