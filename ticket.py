import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
from datetime import datetime
import requests

BUTTON_CHANNEL_ID = 1382079493711200549
TICKET_CHANNEL_ID = 1381754826710585527
TICKET_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1385054753754714162/ticket_small.jpg"

JSONBIN_API_KEY = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
BIN_ID = "68540cf68561e97a50273222"
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

def save_all_data(main_msg_id: int | None, submitted_tickets=None):
    data = {
        "ticket_button_message_id": str(main_msg_id) if main_msg_id is not None else None,
        "submitted_tickets": submitted_tickets or []
    }
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}"
    response = requests.put(url, json=data, headers=HEADERS)
    if response.status_code == 200:
        print("✅ Data saved to jsonbin.io")
    else:
        print(f"❌ Error saving data: {response.status_code} {response.text}")

def load_all_data():
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}/latest"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json().get("record", {})
    else:
        print(f"❌ Error loading all data: {response.status_code}")
        return {}

def append_ticket_submission(message_id: int, user_id: int):
    data = load_all_data()
    submitted = data.get("submitted_tickets", [])
    submitted.append({"message_id": message_id, "user_id": user_id})
    save_all_data(data.get("ticket_button_message_id"), submitted)

class ReplyModal(Modal, title="Reply to User"):
    def __init__(self, target_user: discord.User):
        super().__init__()
        self.target_user = target_user
        self.reply_input = TextInput(label="Your reply", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.reply_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.target_user.send(f"\U0001F4EC You received a reply from a moderator:\n\n{self.reply_input.value}")
            await interaction.followup.send("✅ Reply sent successfully.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Could not send DM – user might have DMs disabled.", ephemeral=True)

class ReplyButton(Button):
    def __init__(self, user: discord.User):
        super().__init__(label="Reply to User", style=discord.ButtonStyle.blurple)
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ReplyModal(self.user))

class TicketReplyView(View):
    def __init__(self, user: discord.User):
        super().__init__(timeout=None)
        self.add_item(ReplyButton(user))

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

        view = TicketReplyView(interaction.user)
        self.bot.add_view(view)

        sent_msg = await channel.send(
            content=f"{interaction.user.mention} submitted a ticket:",
            embed=embed,
            view=view
        )

        append_ticket_submission(sent_msg.id, interaction.user.id)
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

        data = load_all_data()
        message_id = data.get("ticket_button_message_id")

        if message_id is None:
            view = TicketView(self.bot)
            msg = await channel.send("Click the button below to open a ticket:", view=view)
            print(f"➕ New ticket button message posted: {msg.id}")
            save_all_data(msg.id, data.get("submitted_tickets", []))
        else:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(view=TicketView(self.bot))
                print(f"♻️ Loaded button message and attached view: {message_id}")
            except discord.NotFound:
                print(f"❌ Stored message {message_id} not found! Resetting.")
                save_all_data(None, data.get("submitted_tickets", []))

    @commands.Cog.listener()
    async def on_ready(self):
        data = load_all_data()
        channel = self.bot.get_channel(BUTTON_CHANNEL_ID)
        if not channel:
            print("❌ Button channel not found on_ready")
            return

        msg_id = data.get("ticket_button_message_id")
        if msg_id:
            try:
                message = await channel.fetch_message(int(msg_id))
                self.bot.add_view(TicketView(self.bot), message_id=message.id)
                print(f"🔄 View re-attached to main ticket button: {message.id}")
            except discord.NotFound:
                print(f"❌ Main ticket button message not found: {msg_id}")

        for entry in data.get("submitted_tickets", []):
            try:
                msg = await channel.fetch_message(int(entry["message_id"]))
                user = await self.bot.fetch_user(int(entry["user_id"]))
                self.bot.add_view(TicketReplyView(user), message_id=msg.id)
                print(f"🔄 Restored reply button on message {msg.id} for user {user.id}")
            except Exception as e:
                print(f"⚠️ Failed to re-attach reply view: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
