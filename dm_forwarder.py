import discord
from discord.ext import commands

TARGET_CHANNEL_ID = 1381754826710585527  # Replace with your target channel ID

class DMForwarder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore messages from bots (including itself)
        if message.author.bot:
            return

        # Check if this is a DM to the bot
        if isinstance(message.channel, discord.DMChannel):
            print(f"📩 DM received from {message.author}: {message.content}")

            target_channel = self.bot.get_channel(TARGET_CHANNEL_ID)
            if target_channel:
                embed = discord.Embed(
                    title="📬 New DM to the Goon Hut Sheriff",
                    description=message.content,
                    color=discord.Color.blue()
                )
                embed.set_author(
                    name=f"{message.author} ({message.author.id})",
                    icon_url=message.author.display_avatar.url
                )

                await target_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        print("🟢 DMForwarder Cog loaded – DMs will be forwarded.")

async def setup(bot):
    await bot.add_cog(DMForwarder(bot))
