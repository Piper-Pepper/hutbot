import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput
import aiohttp
import logging
from typing import Optional

# 🔐 JSONBin Configuration
JSONBIN_BIN_ID = "685442458a456b7966b13207"
JSONBIN_API_KEY = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
JSONBIN_BASE_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 📤 Modal for Riddle Editing
class RiddleEditModal(Modal, title="Edit Riddle"):
    def __init__(self, data, guild: discord.Guild):
        super().__init__()
        self.guild = guild

        self.text = TextInput(label="Text", default=data.get("text", ""), required=True, style=discord.TextStyle.paragraph)
        self.solution = TextInput(label="Solution", default=data.get("solution", ""), required=True)
        self.award = TextInput(label="Award", default=data.get("award", ""), required=False)
        self.image_url = TextInput(label="Image URL", default=data.get("image-url", ""), required=False)
        self.solution_url = TextInput(label="Solution Image URL", default=data.get("solution-url", ""), required=False)

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

        self.button_id = data.get("button-id", "")  # 👈 merken für später

    async def on_submit(self, interaction: discord.Interaction):
        updated_data = {
            "text": self.text.value,
            "solution": self.solution.value,
            "award": self.award.value,
            "image-url": self.image_url.value,
            "solution-url": self.solution_url.value,
            "button-id": self.button_id  # bleibt unverändert erhalten
        }

        logger.info(f"[Modal Submit] New Data: {updated_data}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=updated_data) as response:
                    if response.status == 200:
                        # 🎯 Footer-Antwort mit Erwähnung der Gruppe, falls gesetzt
                        group_note = f"\n🔖 Assigned Group: <@&{self.button_id}>" if self.button_id else ""
                        await interaction.response.send_message(f"✅ Riddle successfully updated!{group_note}", ephemeral=True)
                    else:
                        logger.error(f"Error saving: {response.status} – {await response.text()}")
                        await interaction.response.send_message(f"❌ Error saving: {response.status}", ephemeral=True)
            except aiohttp.ClientError as e:
                logger.exception("Network error while saving:")
                await interaction.response.send_message(f"❌ Network error: {e}", ephemeral=True)

class RiddleEditor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle", description="Load and edit the current riddle.")
    @app_commands.describe(mention="Optional group to tag (will be stored)")
    async def riddle(self, interaction: discord.Interaction, mention: Optional[discord.Role] = None):
        required_role_id = 1380610400416043089
        has_role = any(role.id == required_role_id for role in interaction.user.roles)

        if not has_role:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return

        logger.info(f"[Slash Command] /riddle by {interaction.user}")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as response:
                    if response.status != 200:
                        logger.error(f"Error loading: {response.status} – {await response.text()}")
                        await interaction.response.send_message("❌ Error loading the riddle.", ephemeral=True)
                        return

                    data = await response.json()
                    record = data.get("record", {})

                    if not record:
                        await interaction.response.send_message("❌ No riddle data found.", ephemeral=True)
                        return

                    if mention:
                        logger.info(f"Saving mention role {mention.name} ({mention.id}) as button-id")
                        record["button-id"] = str(mention.id)
                        async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=record) as update_response:
                            if update_response.status != 200:
                                await interaction.response.send_message("❌ Failed to store role mention.", ephemeral=True)
                                return

                    modal = RiddleEditModal(data=record, guild=interaction.guild)
                    await interaction.response.send_modal(modal)

            except aiohttp.ClientError as e:
                logger.exception("Network error while loading:")
                await interaction.response.send_message(f"❌ Network error while loading: {e}", ephemeral=True)

    @app_commands.command(name="riddle_preview", description="Show a preview of the current riddle from JSONBin.")
    async def riddle_preview(self, interaction: discord.Interaction):
        logger.info(f"[Slash Command] /riddle_preview by {interaction.user}")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as response:
                    if response.status != 200:
                        logger.error(f"Error loading: {response.status} – {await response.text()}")
                        await interaction.response.send_message("❌ Error loading the riddle.", ephemeral=True)
                        return

                    data = await response.json()
                    record = data.get("record", {})

                    if not record or "text" not in record or "solution" not in record:
                        await interaction.response.send_message("❌ The riddle data is incomplete or missing.", ephemeral=True)
                        return

                    embed = discord.Embed(
                        title="🧩 Riddle Preview",
                        description=record["text"],
                        color=discord.Color.blurple()
                    )
                    embed.add_field(name="✅ Correct Solution", value=record.get("solution", "*None*"), inline=False)
                    if record.get("award"):
                        embed.add_field(name="🏆 Award", value=record["award"], inline=False)
                    if record.get("image-url"):
                        embed.set_image(url=record["image-url"])
                    embed.set_footer(text="Preview from JSONBin")

                    await interaction.response.send_message(embed=embed, ephemeral=True)

            except aiohttp.ClientError as e:
                logger.exception("Network error while loading preview:")
                await interaction.response.send_message(f"❌ Network error: {e}", ephemeral=True)

# 🚀 Setup
async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))
