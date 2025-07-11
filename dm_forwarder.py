import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput

TARGET_CHANNEL_ID = 1390430555124007145  # Put your target channel ID here
BOT_ID = 123456789012345678  # Put your bot's user ID here

class AnswerModal(Modal):
    def __init__(self, recipient: discord.User):
        super().__init__(title="Reply to Sender")
        self.recipient = recipient

        self.response = TextInput(
            label="Your reply",
            style=discord.TextStyle.paragraph,
            placeholder="Type your message here...",
            required=True,
            max_length=500
        )
        self.add_item(self.response)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.recipient.send(f"📬 Reply from {interaction.user}:\n{self.response.value}")
            await interaction.response.send_message("Reply sent!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to send reply: {e}", ephemeral=True)

class AnswerView(View):
    def __init__(self, recipient: discord.User):
        super().__init__(timeout=None)  # Button stays active indefinitely
        self.recipient = recipient

        self.answer_button = Button(label="Answer", style=discord.ButtonStyle.primary)
        self.answer_button.callback = self.answer_callback
        self.add_item(self.answer_button)

    async def answer_callback(self, interaction: discord.Interaction):
        modal = AnswerModal(self.recipient)
        await interaction.response.send_modal(modal)

class DMForwarder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore messages from bots (including itself)
        if message.author.bot or message.author.id == BOT_ID:
            return

        # Check if message is a DM
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

                view = AnswerView(message.author)
                await target_channel.send(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        print("🟢 DMForwarder Cog loaded – DMs will be forwarded.")

async def setup(bot):
    await bot.add_cog(DMForwarder(bot))
