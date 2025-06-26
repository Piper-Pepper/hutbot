import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os

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

    # Riddle persistent Views registrieren
    # riddle_cog = bot.get_cog("RiddleCog")
    # if riddle_cog:
    #     await riddle_cog.setup_persistent_views()
    #     print("🔁 Riddle persistent Views loaded.")

@bot.command()
@commands.has_permissions(ban_members=True)
async def preban(ctx, user_id: int, *, reason=None):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.ban(user, reason=reason)
        await ctx.send(f"🔨 User {user} was pre-banned.")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

async def main():
    async with bot:
        # 📦 Load all extensions
        await bot.load_extension("generate_cleaner")
        await bot.load_extension("pepper")
        await bot.load_extension("hutmember")
        await bot.load_extension("dm_logger")
        await bot.load_extension("anti-mommy")
        await bot.load_extension("dm_forwarder")
        await bot.load_extension("ticket")
        await bot.load_extension("status_manager")
        await bot.load_extension("birthday_cog")
        await bot.load_extension("hut_dm")
        await bot.load_extension("hut_dm_app")

        # 🧩 Riddle Cog laden
        # await bot.load_extension("riddle")
        # await bot.load_extension("riddle_commands")

        # 🎂 Optional: persistent View für Geburtstag
        from birthday_cog import BirthdayButtonView
        bot.add_view(BirthdayButtonView(bot))

        # 🚀 Start the bot
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
