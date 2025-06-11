import discord
from discord.ext import commands

FORWARD_TO_USER_ID = 1379906834588106883
TARGET_CHANNEL_ID = 1346418734360956972

class DMForwarder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Nur DMs verarbeiten, die NICHT vom Bot selbst stammen
        if not isinstance(message.channel, discord.DMChannel) or message.author.bot:
            return

        # Prüfen, ob der Empfänger der Nachricht der gewünschte User ist
        recipient_id = message.channel.recipient.id if hasattr(message.channel, "recipient") else None
        if recipient_id != FORWARD_TO_USER_ID:
            return

        # Channel holen
        channel = self.bot.get_channel(TARGET_CHANNEL_ID)
        if not channel:
            print("Zielchannel nicht gefunden.")
            return

        # Embed bauen
        embed = discord.Embed(
            title="📥 New Forwarded DM",
            description=message.content or "*[No text]*",
            color=discord.Color.blurple()
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.set_footer(text=f"User ID: {message.author.id}")

        # Datei anhängen, wenn vorhanden
        files = []
        for attachment in message.attachments:
            try:
                files.append(await attachment.to_file())
            except:
                pass  # Optional: Logging

        await channel.send(embed=embed, files=files)

# Bot Setup
async def setup(bot):
    await bot.add_cog(DMForwarder(bot))
