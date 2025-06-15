import discord
from discord.ext import commands
from datetime import datetime

BOT_ID = 1379906834588106883  # Deine Bot-ID
LOG_CHANNEL_ID = 1381754826710585527  # Kanal, in dem die DMs als Embed gepostet werden

class DMLogger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignoriere Nachrichten vom Bot selbst oder anderen Bots
        if message.author.bot:
            return
        
        # Check ob DM an den Bot
        if isinstance(message.channel, discord.DMChannel) and message.guild is None:
            # Falls die Nachricht NICHT an diesen Bot ist (sicher ist besser)
            if message.recipient and message.recipient.id != BOT_ID:
                return
            
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if not log_channel:
                print(f"Log channel {LOG_CHANNEL_ID} nicht gefunden!")
                return

            embed = discord.Embed(
                title="Neue DM an den Bot",
                description=message.content or "*Keine Textnachricht*",
                color=discord.Color.blue(),
                timestamp=message.created_at
            )
            embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
            embed.add_field(name="Gesendet am", value=message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)

            # Falls Anhang vorhanden
            if message.attachments:
                attach_urls = "\n".join(attach.url for attach in message.attachments)
                embed.add_field(name="Anhänge", value=attach_urls, inline=False)

            await log_channel.send(embed=embed)

# In deiner main.py oder wo du den Bot startest:
async def setup(bot):
    await bot.add_cog(DMLogger(bot))
