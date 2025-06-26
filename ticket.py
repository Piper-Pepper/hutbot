import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
from datetime import datetime
import requests
import os
from dotenv import load_dotenv

load_dotenv()

BUTTON_CHANNEL_ID = 1382079493711200549
TICKET_CHANNEL_ID = 1381754826710585527
TICKET_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1385054753754714162/ticket_small.jpg"

# Load from .env
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
BIN_ID = os.getenv("TICKET_BIN")  # Formerly hardcoded BIN_ID
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

def save_message_id(message_id: int | None):
    print(f"[DEBUG] Saving message ID: {message_id}")
    data = {
        "ticket_button_message_id": str(message_id) if message_id is not None else None
    }
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}"
    response = requests.put(url, json=data, headers=HEADERS)
    if response.status_code == 200:
        print(f"✅ Message ID saved to jsonbin.io: {message_id}")
    else:
        print(f"❌ Error saving Message ID: {response.status_code} {response.text}")

def load_message_id():
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}/latest"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        data = response.json()
        raw_id = data['record'].get('ticket_button_message_id')
        try:
            return int(raw_id) if raw_id is not None else None
        except ValueError:
            print(f"❌ Invalid message ID format in jsonbin: {raw_id}")
            return None
    else:
        print(f"❌ Error loading Message ID: {response.status_code} {response.text}")
        return None

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

        await channel.send(content=f"{interaction.user.mention} submitted a ticket:", embed=embed)
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

        message_id = load_message_id()

        if message_id is None:
            view = TicketView(self.bot)
            msg = await channel.send("Click the button below to open a ticket:", view=view)
            print(f"➕ New ticket button message posted: {msg.id}")
            save_message_id(msg.id)
        else:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(view=TicketView(self.bot))
                print(f"♻️ Loaded button message and attached view: {message_id}")
            except discord.NotFound:
                print(f"❌ Stored message {message_id} not found! Resetting message ID and reposting.")
                save_message_id(None)
                view = TicketView(self.bot)
                msg = await channel.send("Click the button below to open a ticket:", view=view)
                print(f"➕ New ticket button message posted: {msg.id}")
                save_message_id(msg.id)
            except Exception as e:
                print(f"⚠️ Error loading/editing message: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        channel = self.bot.get_channel(BUTTON_CHANNEL_ID)
        if not channel:
            print("❌ Button channel not found on_ready")
            return
        message_id = load_message_id()
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                self.bot.add_view(TicketView(self.bot), message_id=message.id)
                print(f"🔄 View attached to message {message_id} on on_ready")
            except discord.NotFound:
                print(f"❌ Stored message {message_id} not found on_ready! Please restart or reset the message ID.")
        else:
            self.bot.add_view(TicketView(self.bot))
            print("ℹ️ View added without message ID (no persistent button)")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))