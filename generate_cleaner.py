import asyncio
from discord.ext import commands

class GenerateCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.content.startswith("/generate"):
            try:
                await asyncio.sleep(13)
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

async def setup(bot):
    await bot.add_cog(GenerateCleaner(bot))
