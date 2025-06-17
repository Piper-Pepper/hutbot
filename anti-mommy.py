import discord
from discord import app_commands
from discord.ext import commands
intents = discord.Intents.default()
intents.members = True  # ganz wichtig!

BLACKLISTED_WORDS = ["mommy"]  # You can add more words here


class AntiNameFilter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="kick_mommy", description="Kick all members with 'mommy' in their name.")
    @app_commands.checks.has_permissions(administrator=True)
    async def kick_blacklisted_users(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        guild = interaction.guild
        kicked = 0
        failed = 0

        for member in guild.members:
            name = member.name.lower()
            nick = (member.nick or "").lower()
            display = member.display_name.lower()

            if any(word in name for word in BLACKLISTED_WORDS) or \
               any(word in nick for word in BLACKLISTED_WORDS) or \
               any(word in display for word in BLACKLISTED_WORDS):
                try:
                    await member.send("🚫 You have been removed from the server because your name contains a forbidden word.")
                except discord.Forbidden:
                    pass
                try:
                    await member.kick(reason="Blacklist word in name ('mommy')")
                    kicked += 1
                except discord.HTTPException:
                    failed += 1

        await interaction.followup.send(f"✅ Operation complete.\n👢 Kicked: `{kicked}`\n⚠️ Failed: `{failed}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNameFilter(bot))
