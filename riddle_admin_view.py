# riddle_admin_view.py
import discord
from discord.ext import commands
from discord.ui import View, Select, Button
import json
import os
from riddle_utils import riddle_cache, close_riddle_without_winner
from discord import app_commands
from discord.ui import View, Select, Button

class RiddleSelect(discord.ui.Select):
    def __init__(self, riddles):
        options = []
        for riddle_id, data in riddles.items():
            options.append(discord.SelectOption(label=f"ID: {riddle_id}", description=data['text'][:50], value=riddle_id))
        
        super().__init__(placeholder="Select a riddle to view...", min_values=1, max_values=1, options=options)
        self.riddles = riddles

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        riddle = self.riddles[selected_id]

        embed = discord.Embed(
            title=f"Riddle ID: {selected_id}",
            description=riddle['text'],
            color=discord.Color.orange()
        )
        embed.add_field(name="Creator ID", value=str(riddle['creator_id']), inline=False)
        embed.add_field(name="Solution", value=riddle['solution'], inline=False)
        embed.add_field(name="Channel ID", value=str(riddle['channel_id']), inline=False)
        embed.set_image(url=riddle.get('image_url', ''))
        embed.set_footer(text="You can delete this riddle using the button below.")

        view = RiddleActionView(selected_id)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class RiddleActionView(View):
    def __init__(self, riddle_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.add_item(DeleteRiddleButton(riddle_id))


class DeleteRiddleButton(Button):
    def __init__(self, riddle_id):
        super().__init__(label="🗑️ Delete Riddle", style=discord.ButtonStyle.danger)
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        if self.riddle_id not in riddle_cache:
            await interaction.response.send_message("This riddle was already deleted or closed.", ephemeral=True)
            return

        # Schließe das Rätsel ohne Gewinner
        await close_riddle_without_winner(interaction.client, self.riddle_id)

        # Entferne aus Cache und JSON
        riddle_cache.pop(self.riddle_id, None)

        if os.path.exists("riddles.json"):
            with open("riddles.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            if self.riddle_id in data:
                del data[self.riddle_id]
                with open("riddles.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)

        await interaction.response.send_message(f"✅ Riddle `{self.riddle_id}` was deleted and closed.", ephemeral=True)



class RiddleViewCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle_view", description="Admin: View and manage active riddles.")
    async def riddle_view(self, interaction: discord.Interaction):
        if not riddle_cache:
            await interaction.response.send_message("There are no active riddles.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)  # ⬅️ Wichtig! Verhindert "app not responding"

        view = View(timeout=None)
        view.add_item(RiddleSelect(riddle_cache))

        await interaction.followup.send("Select a riddle to view its details:", view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(RiddleViewCommand(bot))

