import asyncio
import discord
from discord.ext import commands, tasks  # tasks hier importieren
from discord.ui import View, Button, Modal, TextInput
from datetime import datetime
import requests
import os
from dotenv import load_dotenv

load_dotenv()

BUTTON_CHANNEL_ID = 1382079493711200549
TICKET_CHANNEL_ID = 1390430555124007145
TICKET_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1385054753754714162/ticket_small.jpg"

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
BIN_ID = os.getenv("TICKET_BIN")
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

def save_buttons_data(data: dict):
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}"
    print(f"[DEBUG] Saving merged data: {data}")
    response = requests.put(url, json=data, headers=HEADERS)
    if response.status_code == 200:
        print(f"✅ Data saved to jsonbin.io successfully.")
    else:
        print(f"❌ Error saving data: {response.status_code} {response.text}")

def load_buttons_data():
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}/latest"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        data = response.json().get('record', {})
        print(f"[DEBUG] RAW from JSONBin: {response.json()}")
        return data
    else:
        print(f"❌ Error loading data: {response.status_code} {response.text}")
        return {}

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
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(label="Open Ticket", style=discord.ButtonStyle.green, custom_id="ticket_open_button")
        self.bot = bot
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        # Direkt Modal senden, ohne defer
        await interaction.response.send_modal(TicketModal(self.bot))


class TicketView(View):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(timeout=None)  # ✅ View ohne Timeout (persistent)
        self.bot = bot
        self.add_item(TicketButton(bot, channel_id))

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

        data = load_buttons_data()

        message_id = data.get(str(BUTTON_CHANNEL_ID))

        if message_id is None:
            print(f"[INFO] No saved message ID for channel {BUTTON_CHANNEL_ID}, sending new button...")
            view = TicketView(self.bot, BUTTON_CHANNEL_ID)
            msg = await channel.send("Click the button below to open a ticket:", view=view)
            # WICHTIG: ID als STRING speichern
            data[str(BUTTON_CHANNEL_ID)] = str(msg.id)
            save_buttons_data(data)
            print(f"➕ New ticket button message posted: {msg.id}")
        else:
            try:
                # INT konvertieren vor fetch_message
                message = await channel.fetch_message(int(message_id))
                await message.edit(view=TicketView(self.bot, BUTTON_CHANNEL_ID))
                print(f"♻️ Loaded button message and attached view: {message_id}")
            except discord.NotFound:
                print(f"❌ Stored message {message_id} not found! Posting new button and updating JSON...")
                view = TicketView(self.bot, BUTTON_CHANNEL_ID)
                msg = await channel.send("Click the button below to open a ticket:", view=view)
                data[str(BUTTON_CHANNEL_ID)] = str(msg.id)
                save_buttons_data(data)
                print(f"➕ New ticket button message posted: {msg.id}")
            except Exception as e:
                print(f"⚠️ Error loading/editing message: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        channel = self.bot.get_channel(BUTTON_CHANNEL_ID)
        if not channel:
            print("❌ Button channel not found on_ready")
            return

        data = load_buttons_data()
        message_id = data.get(str(BUTTON_CHANNEL_ID))

        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
                # Verwende .add_view korrekt
                await message.edit(view=TicketView(self.bot, BUTTON_CHANNEL_ID))  # View hinzufügen
                print(f"🔄 View attached to message {message_id} on on_ready")
            except discord.NotFound:
                print(f"❌ Stored message {message_id} not found on_ready! Please restart or reset the message ID.")
        else:
            view = TicketView(self.bot, BUTTON_CHANNEL_ID)
            msg = await channel.send("Click the button below to open a ticket:", view=view)
            data[str(BUTTON_CHANNEL_ID)] = str(msg.id)
            save_buttons_data(data)
            print("ℹ️ View added without message ID (no persistent button)")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
