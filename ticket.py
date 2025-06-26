import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
from datetime import datetime
import requests
import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BUTTON_CHANNEL_ID = 1382079493711200549
TICKET_CHANNEL_ID = 1381754826710585527
TICKET_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1385054753754714162/ticket_small.jpg"

# Load from .env
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
BIN_ID = os.getenv("TICKET_BIN")
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

def save_message_map(data: dict):
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}"
    response = requests.put(url, json=data, headers=HEADERS)
    if response.status_code == 200:
        print(f"✅ Button message map saved: {data}")
    else:
        print(f"❌ Error saving button message map: {response.status_code} {response.text}")

def load_message_map():
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}/latest"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json().get("record", {})
    else:
        print(f"❌ Error loading message map: {response.status_code} {response.text}")
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
        self.post_button_messages.start()

    def cog_unload(self):
        self.post_button_messages.cancel()

    @tasks.loop(count=1)
    async def post_button_messages(self):
        await self.bot.wait_until_ready()
        data = load_message_map()
        changed = False

        for channel_id_str, message_id in data.copy().items():
            channel = self.bot.get_channel(int(channel_id_str))
            if not channel:
                print(f"❌ Channel {channel_id_str} not found.")
                continue

            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(view=TicketView(self.bot))
                print(f"🔄 View reattached in channel {channel_id_str} for message {message_id}")
            except discord.NotFound:
                print(f"⚠️ Old message {message_id} in channel {channel_id_str} not found. Creating new.")
                view = TicketView(self.bot)
                new_channel = self.bot.get_channel(DEFAULT_BUTTON_CHANNEL_ID)
                if new_channel:
                    new_msg = await new_channel.send("Click the button below to open a ticket:", view=view)
                    print(f"➕ Reposted new button in fallback channel: {new_msg.id}")
                    data[str(DEFAULT_BUTTON_CHANNEL_ID)] = new_msg.id
                    changed = True
            except Exception as e:
                print(f"❌ Unexpected error: {e}")

        if not data:
            print("ℹ️ No button entries found. Creating new default.")
            view = TicketView(self.bot)
            default_channel = self.bot.get_channel(DEFAULT_BUTTON_CHANNEL_ID)
            if default_channel:
                msg = await default_channel.send("Click the button below to open a ticket:", view=view)
                data[str(DEFAULT_BUTTON_CHANNEL_ID)] = msg.id
                changed = True

        if changed:
            save_message_map(data)

    @commands.Cog.listener()
    async def on_ready(self):
        data = load_message_map()
        for channel_id_str, message_id in data.items():
            channel = self.bot.get_channel(int(channel_id_str))
            if channel:
                try:
                    await channel.fetch_message(message_id)
                    self.bot.add_view(TicketView(self.bot), message_id=message_id)
                    print(f"🔁 View reattached to message {message_id} in channel {channel_id_str}")
                except Exception as e:
                    print(f"⚠️ Could not reattach view: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))