import os
import requests
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
import uuid
from datetime import datetime
from dotenv import load_dotenv

from riddle_embeds import (
    build_riddle_embed,
    build_solution_submission_embed,
    build_wrong_solution_embed,
    build_win_embed
)

# Load environment
load_dotenv()

# JSONBin Config
RIDDLE_BIN_ID = os.getenv("RIDDLE_BIN_ID")
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

# Discord Config
RIDDLE_CHANNEL_ID = 1346843244067160074
LOG_CHANNEL_ID = 1381754826710585527
MOD_ROLE_ID = 1380610400416043089

# In-memory cache
riddle_cache = {}

# Load riddles from JSONBin
def load_riddles():
    url = f"https://api.jsonbin.io/v3/b/{RIDDLE_BIN_ID}/latest"
    print(f"DEBUG load URL: {url}")
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        global riddle_cache
        payload = resp.json()
        riddle_cache = payload.get("record", {})
        print(f"✅ Riddles loaded. Count: {len(riddle_cache)}")
        print(f"DEBUG cache content: {riddle_cache}")
    else:
        print(f"❌ Failed to load riddles: {resp.status_code} {resp.text}")

# Save riddles to JSONBin
def save_riddles():
    url = f"https://api.jsonbin.io/v3/b/{RIDDLE_BIN_ID}"
    payload = {"record": riddle_cache}
    print(f"DEBUG save URL: {url}")
    print(f"DEBUG save payload: {payload}")
    response = requests.put(url, json=payload, headers=HEADERS)
    print(f"DEBUG save response: {response.status_code} {response.text}")
    if response.status_code != 200:
        print(f"❌ Error saving riddles: {response.status_code} {response.text}")

# Call load on startup
load_riddles()

# -------- Hilfsfunktion für zentrales Edit-Modal --------
async def open_riddle_edit_modal(bot, interaction: discord.Interaction, riddle_id: str):
    """Öffnet das Editier-Modal für ein bestimmtes Riddle."""
    await interaction.response.send_modal(RiddleEditModal(bot, riddle_id))

# --------- List-View mit Buttons ---------
class RiddleListView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        for riddle_id, r in list(riddle_cache.items())[:20]:
            label = r["text"][0:20].replace("\n", " ")
            self.add_item(RiddleButton(bot, riddle_id, label))

class RiddleButton(Button):
    def __init__(self, bot, riddle_id, label):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await open_riddle_edit_modal(self.bot, interaction, self.riddle_id)

# -------- Modal for Editing Riddle --------
class RiddleEditModal(Modal, title="Edit Riddle"):
    def __init__(self, bot, riddle_id):
        super().__init__()
        self.bot = bot
        self.riddle_id = riddle_id
        r = riddle_cache.get(riddle_id, {})

        self.text = TextInput(label="Riddle Text", style=discord.TextStyle.paragraph, default=r.get("text", ""))
        self.solution = TextInput(label="Solution", default=r.get("solution", ""))
        self.image_url = TextInput(label="Image URL (optional)", default=r.get("image_url", ""), required=False)
        self.solution_url = TextInput(label="Solution Image URL (optional)", default=r.get("solution_url", ""), required=False)
        self.mentions = TextInput(
            label="Mention Role IDs (max 2, comma separated)",
            default=",".join(r.get("mentions", [])),
            required=False
        )
        self.award = TextInput(label="Award Text or Emoji (optional)", default=r.get("award", ""), required=False)

        for item in [self.text, self.solution, self.image_url, self.solution_url, self.mentions, self.award]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        r = riddle_cache[self.riddle_id]
        r.update({
            "text": self.text.value,
            "solution": self.solution.value,
            "image_url": self.image_url.value or "",
            "solution_url": self.solution_url.value or "",
            "mentions": [x.strip() for x in self.mentions.value.split(",") if x.strip()],
            "award": self.award.value
        })
        save_riddles()
        await interaction.followup.send("✅ Riddle updated.", ephemeral=True)
        await interaction.user.send(embed=build_riddle_embed(r, interaction.guild, interaction.user), view=RiddleEditView(self.bot, self.riddle_id))

# -------- View: Edit, Post, Close, Delete Buttons --------
class RiddleEditView(View):
    def __init__(self, bot, riddle_id):
        super().__init__(timeout=None)
        self.add_item(EditRiddleButton(bot, riddle_id))
        self.add_item(PostRiddleButton(bot, riddle_id))
        self.add_item(CloseRiddleButton(bot, riddle_id))
        self.add_item(DeleteRiddleButton(bot, riddle_id))

class EditRiddleButton(Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Edit", style=discord.ButtonStyle.primary)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await open_riddle_edit_modal(self.bot, interaction, self.riddle_id)

class PostRiddleButton(Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Post", style=discord.ButtonStyle.success)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        r = riddle_cache[self.riddle_id]
        channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
        mentions = f"<@&{MOD_ROLE_ID}>" + "".join(f" <@&{x}>" for x in r.get("mentions", [])[:2])
        embed = build_riddle_embed(r, interaction.guild, interaction.user)
        view = SubmitView(self.bot, self.riddle_id)
        msg = await channel.send(content=mentions, embed=embed, view=view)
        r["channel_id"] = str(RIDDLE_CHANNEL_ID)
        r["button_id"] = str(msg.id)
        save_riddles()
        await interaction.followup.send("✅ Riddle posted.", ephemeral=True)

class CloseRiddleButton(Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Close", style=discord.ButtonStyle.secondary)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await close_riddle_with_winner(self.bot, self.riddle_id, winner_id=None)
        await interaction.followup.send("✅ Riddle closed.", ephemeral=True)

class DeleteRiddleButton(Button):
    def __init__(self, bot, riddle_id):
        super().__init__(label="Delete", style=discord.ButtonStyle.danger)
        self.bot = bot
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if self.riddle_id in riddle_cache:
            del riddle_cache[self.riddle_id]
            save_riddles()
        await interaction.followup.send("🗑️ Riddle deleted.", ephemeral=True)

# -------- Main Cog --------
class RiddleCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle_add", description="Create a new riddle (Mods only)")
    async def riddle_add(self, interaction: discord.Interaction,
                         text: str,
                         solution: str,
                         image_url: str = "",
                         mentions: str = "",
                         solution_image: str = "",
                         award: str = ""):
        if MOD_ROLE_ID not in [role.id for role in interaction.user.roles]:
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        riddle_id = str(uuid.uuid4())[:8]
        riddle_data = {
            "text": text,
            "solution": solution,
            "image_url": image_url,
            "solution_url": solution_image,
            "mentions": [x.strip() for x in mentions.split(",") if x.strip()][:2],
            "award": award,
            "riddle_id": riddle_id,
            "ersteller": str(interaction.user.id),
            "winner": None,
            "created_at": datetime.utcnow().isoformat()
        }

        print(f"Adding riddle: {riddle_data}")

        riddle_cache[riddle_id] = riddle_data
        save_riddles()

        embed = build_riddle_embed(riddle_data, interaction.guild, interaction.user)
        await interaction.followup.send(
            "🧩 Riddle created. Here’s your preview:",
            embed=embed,
            view=RiddleEditView(self.bot, riddle_id),
            ephemeral=True
        )

    @app_commands.command(name="riddle_list", description="List all riddles with buttons")
    async def riddle_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="🧩 Active Riddles",
            description="Select a riddle below to edit it.",
            color=discord.Color.blurple()
        )

        try:
            view = RiddleListView(self.bot)
        except Exception as e:
            print(f"❌ Error building riddle list: {e}")
            embed.description = "⚠️ Failed to load riddles."
            view = None

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCommands(bot))
