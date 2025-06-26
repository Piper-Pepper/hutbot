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

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
BIN_ID = os.getenv("TICKET_BIN")
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

def load_button_data():
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}/latest"
    try:
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            return response.json().get("record", {})
        else:
            print(f"❌ Failed to load button data: {response.status_code}")
            return {}
    except Exception as e:
        print(f"❌ Exception during loading button data: {e}")
        return {}

def save_button_data(data):
    url = f"https://api.jsonbin.io/v3/b/{BIN_ID}"
    try:
        response = requests.put(url, json=data, headers=HEADERS)
        if response.status_code == 200:
            print("✅ Button data saved to JSONBin")
        else:
            print(f"❌ Failed to save button data: {response.status_code} {response.text}")
    except Exception as e:
        print(f"❌ Exception during saving button data: {e}")

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
        self.post_buttons.start()

    def cog_unload(self):
        self.post_buttons.cancel()

    @tasks.loop(count=1)
    async def post_buttons(self):
        await self.bot.wait_until_ready()
        data = load_button_data()

        for channel_id_str, msg_id in data.items():
            channel_id = int(channel_id_str)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                print(f"❌ Channel {channel_id} not found.")
                continue
            try:
                message = await channel.fetch_message(int(msg_id))
                await message.edit(view=TicketView(self.bot))
                self.bot.add_view(TicketView(self.bot), message_id=message.id)
                print(f"🔁 Reattached button in channel {channel_id} to message {message.id}")
            except discord.NotFound:
                print(f"⚠️ Message not found in channel {channel_id}. Reposting...")
                view = TicketView(self.bot)
                msg = await channel.send("Click the button below to open a ticket:", view=view)
                data[str(channel.id)] = msg.id
                save_button_data(data)
            except Exception as e:
                print(f"❌ Error in channel {channel_id}: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        data = load_button_data()
        for channel_id_str, msg_id in data.items():
            try:
                channel = self.bot.get_channel(int(channel_id_str))
                if channel:
                    self.bot.add_view(TicketView(self.bot), message_id=int(msg_id))
                    print(f"🔄 View reattached in channel {channel_id_str} to message {msg_id}")
            except Exception as e:
                print(f"❌ Failed to reattach view: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))