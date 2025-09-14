import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import traceback

# 🔐 Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True
intents.guilds = True
intents.message_content = True
intents.members = True

# ⛓️ Create bot and command tree
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
synced_once = False  # Only sync slash commands once


@bot.event
async def on_ready():
    global synced_once
    print(f"✅ Bot connected as {bot.user}!")

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
        # 📦 Liste aller Extensions
        extensions = [
            "pepper",
            "hutmember",
            "anti-mommy",
            "ticket",
            "status_manager",
            # "birthday_cog",  # optional
            "hut_dm",
            "hut_dm_app",
            # "hutkick",
            "venice_cog",
            "gather",
            "reset",
            "riddle",
            "riddle_post"
        ]

        # 🔍 Lade Extensions mit Fehlerausgabe
        for ext in extensions:
            try:
                await bot.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception as e:
                print(f"❌ Fehler beim Laden von {ext}: {e}")
                traceback.print_exc()

        # 🎂 Optional: persistent View für Geburtstag
        try:
            from birthday_cog import BirthdayButtonView
            bot.add_view(BirthdayButtonView(bot))
            print("🎂 Birthday view geladen.")
        except Exception as e:
            print(f"⚠️ Birthday view konnte nicht geladen werden: {e}")

        # 🚀 Start the bot
        try:
            await bot.start(TOKEN)
        except Exception as e:
            print(f"❌ Fehler beim Starten des Bots: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot manuell gestoppt.")
    except Exception as e:
        print(f"❌ Unerwarteter Fehler: {e}")
        traceback.print_exc()
