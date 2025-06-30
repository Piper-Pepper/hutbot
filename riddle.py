# 📁 riddle.py
import discord, os
from discord.ext import commands
from discord.ui import View
from riddle_embeds import format_riddle_embed, format_wrong_solution_embed, format_win_embed
from jsonbin_client import JsonBinClient

class SubmitView(View):
    def __init__(self, riddle_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id

    @discord.ui.button(label="Submit Solution", custom_id="riddle:submit", style=discord.ButtonStyle.primary)
    async def submit_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SolutionModal(self.riddle_id))

class SolutionModal(discord.ui.Modal, title="Submit Your Solution"):
    def __init__(self, riddle_id):
        super().__init__()
        self.riddle_id = riddle_id
        self.answer = discord.ui.TextInput(label="Your answer", style=discord.TextStyle.long)
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        jsonbin = JsonBinClient(os.getenv("RIDDLE_BIN_ID"), os.getenv("JSONBIN_API_KEY"))
        data = await jsonbin.get()

        riddle = next((r for r in data if r["riddle_id"] == self.riddle_id), None)
        if not riddle:
            await interaction.followup.send("Riddle not found.", ephemeral=True)
            return

        riddle["submitted"] = self.answer.value
        if riddle["submitted"].strip().lower() == riddle["solution"].strip().lower():
            riddle["winner"] = str(interaction.user.id)
            await interaction.followup.send("✅ Correct! You solved the riddle!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Wrong answer.", ephemeral=True)

        await jsonbin.set(data)

class RiddleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.setup_views())

    async def setup_views(self):
        await self.bot.wait_until_ready()
        jsonbin = JsonBinClient(os.getenv("RIDDLE_BIN_ID"), os.getenv("JSONBIN_API_KEY"))
        data = await jsonbin.get()
        for rd in data:
            if not rd.get("winner"):
                self.bot.add_view(SubmitView(rd["riddle_id"]))

async def setup(bot):
    await bot.add_cog(RiddleCog(bot))
