import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import traceback

# 🔐 Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Optional: Dev guild for faster slash command sync
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")  # e.g., "123456789012345678"
DEV_GUILD = discord.Object(id=int(DEV_GUILD_ID)) if DEV_GUILD_ID else None

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True
intents.guilds = True
intents.message_content = True
intents.members = True

# ⛓️ Create bot
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
synced_once = False

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
            synced_once = True
        except Exception as e:
            print(f"❌ Failed to sync commands: {e}")

async def main():
    async with bot:
        # 📦 Load extensions
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
            "hutvote",  # <- your vote cog
            "hutthreadvote",
            "riddle_post"
        ]

        for ext in extensions:
            try:
                await bot.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception as e:
                print(f"❌ Fehler beim Laden von {ext}: {e}")
                traceback.print_exc()

        # 🎂 Optional: persistent Birthday View
        try:
            from birthday_cog import BirthdayButtonView
            bot.add_view(BirthdayButtonView(bot))
            print("🎂 Birthday view loaded.")
        except Exception as e:
            print(f"⚠️ Birthday view not loaded: {e}")

        # 🚀 Start the bot
        try:
            await bot.start(TOKEN)
        except Exception as e:
            print(f"❌ Error starting bot: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot manually stopped.")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        traceback.print_exc()
