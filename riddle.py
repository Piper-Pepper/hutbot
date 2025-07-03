import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, Button, View
import aiohttp  # For asynchronous HTTP requests
import logging

# 🔐 JSONBin Configuration
JSONBIN_BIN_ID = "685442458a456b7966b13207"
JSONBIN_API_KEY = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
JSONBIN_BASE_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"

HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

# 🪵 Logging for debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 📤 Modal for Riddle Editing
class RiddleEditModal(Modal, title="Edit Riddle"):
    def __init__(self, data):
        super().__init__()

        # Required inputs
        self.text = TextInput(
            label="Text", 
            default=data.get("text", ""), 
            required=True, 
            style=discord.TextStyle.paragraph
        )
        self.solution = TextInput(
            label="Solution", 
            default=data.get("solution", ""), 
            required=True
        )
        
        # Optional inputs
        self.award = TextInput(
            label="Award", 
            default=data.get("award", ""), 
            required=False
        )
        self.image_url = TextInput(
            label="Image URL", 
            default=data.get("image-url", ""), 
            required=False
        )
        self.solution_url = TextInput(
            label="Solution Image URL", 
            default=data.get("solution-url", ""), 
            required=False
        )
        
        # Add items to modal
        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

        # Only add button_id if it exists and is not empty
        button_id_value = data.get("button-id", "")
        if button_id_value:
            self.button_id = TextInput(
                label="Button ID", 
                default=button_id_value, 
                required=False
            )
            self.add_item(self.button_id)

    async def on_submit(self, interaction: discord.Interaction):
        updated_data = {
            "text": self.text.value,
            "solution": self.solution.value,
            "award": self.award.value,
            "image-url": self.image_url.value,
            "solution-url": self.solution_url.value,
            "button-id": self.button_id.value if hasattr(self, 'button_id') else ""  # Handle absence of button_id
        }

        logger.info(f"[Modal Submit] New Data: {updated_data}")

        # Use aiohttp for asynchronous HTTP requests
        async with aiohttp.ClientSession() as session:
            try:
                async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=updated_data) as response:
                    if response.status == 200:
                        await interaction.response.send_message("✅ Riddle successfully updated!", ephemeral=True)
                    else:
                        logger.error(f"Error saving: {response.status} – {await response.text()}")
                        await interaction.response.send_message(f"❌ Error saving: {response.status} – {await response.text()}", ephemeral=True)
            except aiohttp.ClientError as e:
                logger.exception("Network error while saving:")
                await interaction.response.send_message(f"❌ Network error: {e}", ephemeral=True)


# 🔧 Cog Class
class RiddleEditor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle", description="Load and edit the current riddle.")
    async def riddle(self, interaction: discord.Interaction):
        logger.info(f"[Slash Command] /riddle by {interaction.user}")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as response:
                    if response.status != 200:
                        logger.error(f"Error loading: {response.status} – {await response.text()}")
                        await interaction.response.send_message("❌ Error loading the riddle.", ephemeral=True)
                        return

                    data = await response.json()
                    logger.info(f"[GET] Successfully loaded: {data}")

                    # Check if the record exists and is valid
                    if "record" not in data or not data["record"]:
                        logger.error("Received invalid or empty record data.")
                        await interaction.response.send_message("❌ No riddle data found.", ephemeral=True)
                        return

                    modal = RiddleEditModal(data=data["record"])
                    await interaction.response.send_modal(modal)

            except aiohttp.ClientError as e:
                logger.exception("Network error while loading:")
                await interaction.response.send_message(f"❌ Network error while loading: {e}", ephemeral=True)

# 🚀 Setup Function
async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))
