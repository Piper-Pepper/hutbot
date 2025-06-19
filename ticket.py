import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
from datetime import datetime

BUTTON_CHANNEL_ID = 1382079493711200549
TICKET_CHANNEL_ID = 1381754826710585527
TICKET_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1385054753754714162/ticket_small.jpg"

class TicketModal(Modal, title="Submit Your Ticket"):
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.message_input = TextInput(label="Your Message", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        channel = self.bot.get_channel(TICKET_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❌ Ticket channel not found.", ephemeral=True)
            return

        embed = discord.Embed(
            description=self.message_input.value,
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        embed.set_image(url=TICKET_IMAGE_URL)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(
            text=interaction.guild.name if interaction.guild else "Unknown Guild",
            icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else discord.Embed.Empty
        )

        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Your ticket was sent!", ephemeral=True)

class TicketButton(Button):
    def __init__(self, bot: commands.Bot):
        super().__init__(label="Open Ticket", style=discord.ButtonStyle.green)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketModal(self.bot))

class TicketView(View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(TicketButton(bot))

class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.post_button_message.start()

    def cog_unload(self):
        self.post_button_message.cancel()

    @tasks.loop(count=1)
    async def post_button_message(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(BUTTON_CHANNEL_ID)
        if not channel:
            print("❌ Button channel not found.")
            return

        # Alte Bot-Nachrichten löschen (max 10)
        async for message in channel.history(limit=10):
            if message.author == self.bot.user:
                try:
                    await message.delete()
                    print(f"Deleted old bot message {message.id}")
                except Exception as e:
                    print(f"Failed to delete message {message.id}: {e}")

        view = TicketView(self.bot)
        msg = await channel.send("Click the button below to open a ticket:", view=view)
        print(f"Posted new ticket button message: {msg.id}")

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(TicketView(self.bot))  # Persistiere die View nach Neustart

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
