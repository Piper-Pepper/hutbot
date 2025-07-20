import discord
from discord.ext import commands
from datetime import datetime

BOT_ID = 1379906834588106883  # Your bot's ID
LOG_CHANNEL_ID = 1381754826710585527  # Channel where DMs will be logged

class DMLogger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages from bots (including itself)
        if message.author.bot:
            return

        # Check if message is a direct message (DM)
        if isinstance(message.channel, discord.DMChannel):
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if not log_channel:
                print(f"Log channel with ID {LOG_CHANNEL_ID} not found.")
                return

            embed = discord.Embed(
                title="New DM to the Bot",
                description=message.content or "*No text content*",
                color=discord.Color.blue(),
                timestamp=message.created_at
            )
            embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
            embed.add_field(
                name="Sent At",
                value=message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                inline=False
            )

            # If there are any attachments, list them
            if message.attachments:
                attachment_urls = "\n".join(attachment.url for attachment in message.attachments)
                embed.add_field(name="Attachments", value=attachment_urls, inline=False)

            await log_channel.send(embed=embed)

# In your main.py or bot setup file
async def setup(bot):
    await bot.add_cog(DMLogger(bot))
