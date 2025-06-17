import discord
from discord.ext import commands

BLACKLISTED_WORDS = ["mommy"]  # Wörter, die nicht im Namen vorkommen dürfen

class AutoKickMommy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        name = member.name.lower()
        nick = (member.nick or "").lower()
        display = member.display_name.lower()

        if any(word in name for word in BLACKLISTED_WORDS) or \
           any(word in nick for word in BLACKLISTED_WORDS) or \
           any(word in display for word in BLACKLISTED_WORDS):
            try:
                await member.send("🚫 You have been kicked because your name contains a forbidden word.")
            except discord.Forbidden:
                pass
            await member.kick(reason="Name contains forbidden word ('mommy')")

async def setup(bot):
    await bot.add_cog(AutoKickMommy(bot))
