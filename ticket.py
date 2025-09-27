import asyncio
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
from datetime import datetime
import requests
import os
from dotenv import load_dotenv

load_dotenv()

# ---------------- Config ----------------
BUTTON_CHANNEL_ID = 1382079493711200549
TICKET_CHANNEL_ID = 1390430555124007145
TICKET_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1385054753754714162/ticket_small.jpg"

REQUIRED_ROLE_ID = 1377051179615522926  # Role für PMVJ & Pepper Police

# Sheriff Config
SHERIFF_CHANNEL_IDS = [
    1362109155531423894,  # erster Sheriff-Embed Channel
    1346414909180870706   # zusätzlicher Sheriff-Embed Channel
]
SHERIFF_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1421560571861532807/western_beware.gif"

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
BIN_ID = os.getenv("TICKET_BIN")
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

# ---------------- JSONBin Helpers ----------------
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

# ---------------- Modal ----------------
class TicketModal(Modal, title="🛖Contact Staff🚨"):
    def __init__(self, bot: commands.Bot, title_prefix: str = "", image_url: str = TICKET_IMAGE_URL):
        super().__init__()
        self.bot = bot
        self.title_prefix = title_prefix
        self.image_url = image_url
        self.message_input = TextInput(label="Your Message", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        channel = self.bot.get_channel(TICKET_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❌ Ticket channel not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title=self.title_prefix if self.title_prefix else None,
            description=self.message_input.value,
            color=discord.Color.green() if self.title_prefix else discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        embed.set_image(url=self.image_url)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(
            text=interaction.guild.name if interaction.guild else "Unknown Guild",
            icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else discord.Embed.Empty
        )

        await channel.send(content=f"{interaction.user.mention} submitted a ticket:", embed=embed)
        await interaction.response.send_message("✅ Your request was sent!", ephemeral=True)

# ---------------- Ticket Buttons ----------------
class TicketButton(Button):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(label="🎫Open Ticket", style=discord.ButtonStyle.red, custom_id="ticket_open_button")
        self.bot = bot
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketModal(self.bot))

class ApplyPMVJButton(Button):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(label="📺Apply for PMVJ", style=discord.ButtonStyle.gray, custom_id="apply_pm_button")
        self.bot = bot
        self.channel_id = channel_id
        self.pmvj_image = "https://cdn.discordapp.com/attachments/1383652563408392232/1383839288235397292/goon_tv_party.gif"

    async def callback(self, interaction: discord.Interaction):
        member_roles = [role.id for role in interaction.user.roles]
        if REQUIRED_ROLE_ID not in member_roles:
            role = interaction.guild.get_role(REQUIRED_ROLE_ID)
            role_name = role.name if role else "Required Role"
            await interaction.response.send_message(
                f"⚠️ You have to be at least Level 4 and inhabit the role **{role_name}** to do this.",
                ephemeral=True
            )
            return
        await interaction.response.send_modal(TicketModal(bot=self.bot, title_prefix="Apply for PMVJ", image_url=self.pmvj_image))

class ApplyHutRiddlerButton(Button):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(label="⁉️Apply for Hut Riddler", style=discord.ButtonStyle.green, custom_id="apply_riddler_button")
        self.bot = bot
        self.channel_id = channel_id
        self.riddler_image = "https://cdn.discordapp.com/attachments/1383652563408392232/1391058634099785892/riddle_sexy.jpg"

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketModal(bot=self.bot, title_prefix="Apply for Hut Riddler", image_url=self.riddler_image))

class ApplyPepperPoliceButton(Button):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(label="👮‍♂️Apply for Pepper Police", style=discord.ButtonStyle.blurple, custom_id="apply_police_button")
        self.bot = bot
        self.channel_id = channel_id
        self.police_image = "https://cdn.discordapp.com/attachments/1383652563408392232/1395870940054814831/police_join.gif"

    async def callback(self, interaction: discord.Interaction):
        member_roles = [role.id for role in interaction.user.roles]
        if REQUIRED_ROLE_ID not in member_roles:
            role = interaction.guild.get_role(REQUIRED_ROLE_ID)
            role_name = role.name if role else "Required Role"
            await interaction.response.send_message(
                f"⚠️ You have to be at least Level 5 and inhabit the role **{role_name}** to do this.",
                ephemeral=True
            )
            return
        await interaction.response.send_modal(TicketModal(bot=self.bot, title_prefix="Apply for Pepper Police", image_url=self.police_image))

# ---------------- Ticket View ----------------
class TicketView(View):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(TicketButton(bot, channel_id))
        self.add_item(ApplyPMVJButton(bot, channel_id))
        self.add_item(ApplyHutRiddlerButton(bot, channel_id))
        self.add_item(ApplyPepperPoliceButton(bot, channel_id))

# ---------------- Sheriff View ----------------
class SheriffView(View):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        # Re-using Ticket Buttons for Sheriff
        self.add_item(TicketButton(bot, channel_id))
        self.add_item(ApplyPMVJButton(bot, channel_id))
        self.add_item(ApplyHutRiddlerButton(bot, channel_id))
        self.add_item(ApplyPepperPoliceButton(bot, channel_id))

# ---------------- Cog ----------------
class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.post_button_message.start()
        self.post_sheriff_messages.start()

    def cog_unload(self):
        self.post_button_message.cancel()
        self.post_sheriff_messages.cancel()

    # Ticket-Buttons
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
            print(f"[INFO] No saved message ID, sending new button...")
            view = TicketView(self.bot, BUTTON_CHANNEL_ID)
            msg = await channel.send("... so...🫦 what do you want, darlin'?♥️", view=view)
            data[str(BUTTON_CHANNEL_ID)] = str(msg.id)
            save_buttons_data(data)
            print(f"➕ New ticket button message posted: {msg.id}")
        else:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(view=TicketView(self.bot, BUTTON_CHANNEL_ID))
                print(f"♻️ Loaded button message and attached view: {message_id}")
            except discord.NotFound:
                print(f"❌ Stored message {message_id} not found! Posting new one...")
                view = TicketView(self.bot, BUTTON_CHANNEL_ID)
                msg = await channel.send("... so...🫦 what do you want, darlin'?♥️", view=view)
                data[str(BUTTON_CHANNEL_ID)] = str(msg.id)
                save_buttons_data(data)
                print(f"➕ New ticket button message posted: {msg.id}")
            except Exception as e:
                print(f"⚠️ Error loading/editing message: {e}")

    # Sheriff-Embeds
    @tasks.loop(count=1)
    async def post_sheriff_messages(self):
        await self.bot.wait_until_ready()
        data = load_buttons_data()

        for channel_id in SHERIFF_CHANNEL_IDS:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                print(f"❌ Sheriff channel {channel_id} not found.")
                continue

            message_id = data.get(str(channel_id))
            embed = discord.Embed(
                title="Sheriff's Office",
                description="The Sheriff is watching you...",
                color=discord.Color.dark_gold()
            )
            embed.set_image(url=SHERIFF_IMAGE_URL)

            if message_id is None:
                print(f"[INFO] No saved Sheriff message ID in {channel_id}, sending new one...")
                view = SheriffView(self.bot, channel_id)
                msg = await channel.send(embed=embed, view=view)
                data[str(channel_id)] = str(msg.id)
                save_buttons_data(data)
                print(f"➕ New Sheriff message posted in {channel_id}: {msg.id}")
            else:
                try:
                    message = await channel.fetch_message(int(message_id))
                    await message.edit(embed=embed, view=SheriffView(self.bot, channel_id))
                    print(f"♻️ Loaded Sheriff message and attached view in {channel_id}: {message_id}")
                except discord.NotFound:
                    print(f"❌ Stored Sheriff message {message_id} not found in {channel_id}! Posting new one...")
                    view = SheriffView(self.bot, channel_id)
                    msg = await channel.send(embed=embed, view=view)
                    data[str(channel_id)] = str(msg.id)
                    save_buttons_data(data)
                    print(f"➕ New Sheriff message posted in {channel_id}: {msg.id}")
                except Exception as e:
                    print(f"⚠️ Error loading/editing Sheriff message in {channel_id}: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        # Ticket
        channel = self.bot.get_channel(BUTTON_CHANNEL_ID)
        if channel:
            data = load_buttons_data()
            message_id = data.get(str(BUTTON_CHANNEL_ID))
            if message_id:
                try:
                    message = await channel.fetch_message(int(message_id))
                    await message.edit(view=TicketView(self.bot, BUTTON_CHANNEL_ID))
                    print(f"🔄 View attached to ticket message {message_id} on on_ready")
                except discord.NotFound:
                    print(f"❌ Stored ticket message {message_id} not found on_ready.")
        # Sheriff
        for channel_id in SHERIFF_CHANNEL_IDS:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            data = load_buttons_data()
            message_id = data.get(str(channel_id))
            if message_id:
                try:
                    message = await channel.fetch_message(int(message_id))
                    await message.edit(view=SheriffView(self.bot, channel_id))
                    print(f"🔄 View attached to Sheriff message {message_id} in {channel_id} on on_ready")
                except discord.NotFound:
                    print(f"❌ Stored Sheriff message {message_id} not found in {channel_id} on_ready.")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
