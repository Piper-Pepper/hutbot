import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Select, Button
import json
import os

from riddle_utils import riddle_cache, close_riddle_no_winner

RIDDLE_FILE = "riddles.json"

# --- Auswahlmenü ---
class RiddleSelect(Select):
    def __init__(self, riddles: dict):
        options = []
        for riddle_id, data in riddles.items():
            label = f"ID {riddle_id} - {data['text'][:30]}..."
            options.append(discord.SelectOption(label=label, value=riddle_id))

        super().__init__(placeholder="Select a riddle to view...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        riddle_id = self.values[0]
        riddle_data = riddle_cache.get(riddle_id)

        if not riddle_data:
            await interaction.response.send_message("Riddle not found.", ephemeral=True)
            return

        creator = await interaction.client.fetch_user(riddle_data['creator_id'])

        embed = discord.Embed(
            title=f"Riddle ID: {riddle_id}",
            description=riddle_data['text'],
            color=discord.Color.orange()
        )
        embed.add_field(name="Solution", value=riddle_data['solution'], inline=False)
        embed.add_field(name="Creator", value=creator.mention, inline=True)
        embed.add_field(name="Created At", value=riddle_data['created_at'], inline=True)
        embed.add_field(name="Channel ID", value=riddle_data['channel_id'], inline=True)
        embed.set_thumbnail(url=riddle_data.get('image_url', 'https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg'))

        view = View(timeout=None)
        view.add_item(DeleteRiddleButton(riddle_id))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# --- Delete Button ---
class DeleteRiddleButton(Button):
    def __init__(self, riddle_id):
        super().__init__(label="Delete Riddle", style=discord.ButtonStyle.danger, emoji="🗑️")
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        riddle_data = riddle_cache.pop(self.riddle_id, None)

        if not riddle_data:
            await interaction.response.send_message("Riddle already deleted or not found.", ephemeral=True)
            return

        # 🔥 Aus der Datei löschen
        if os.path.exists(RIDDLE_FILE):
            with open(RIDDLE_FILE, "r", encoding="utf-8") as f:
                all_riddles = json.load(f)

            if self.riddle_id in all_riddles:
                del all_riddles[self.riddle_id]

            with open(RIDDLE_FILE, "w", encoding="utf-8") as f:
                json.dump(all_riddles, f, indent=4, ensure_ascii=False)

        # 🔒 Rätsel als geschlossen markieren (ohne Gewinner)
        await close_riddle_no_winner(interaction.client, self.riddle_id)

        await interaction.response.send_message(f"Riddle {self.riddle_id} was successfully deleted and marked as closed.", ephemeral=True)


# --- Slash Command Setup ---
class RiddleViewCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle_view", description="Admin: View and manage active riddles.")
    async def riddle_view(self, interaction: discord.Interaction):
        if not riddle_cache:
            await interaction.response.send_message("There are no active riddles.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        view = View(timeout=None)
        view.add_item(RiddleSelect(riddle_cache))

        await interaction.followup.send("Select a riddle to view its details:", view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(RiddleViewCommand(bot))
