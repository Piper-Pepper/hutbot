# 📁 riddle_commands.py
import discord
from discord import app_commands
from discord.ext import commands
import uuid
import os
from jsonbin_client import JsonBinClient

class RiddleCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.jsonbin = JsonBinClient(os.getenv("RIDDLE_BIN_ID"), os.getenv("JSONBIN_API_KEY"))

    @app_commands.command(name="riddle_add", description="Add a new riddle")
    async def riddle_add(self, interaction: discord.Interaction, text: str, solution: str, image_url: str = None, mentions: str = None, solution_url: str = None, award: str = None):
        await interaction.response.defer(ephemeral=True)
        data = await self.jsonbin.get()

        riddle_id = str(uuid.uuid4())[:8]
        author = interaction.user

        entry = {
            "text": text,
            "solution": solution,
            "image_url": image_url,
            "solution_url": solution_url,
            "mentions": mentions,
            "award": award,
            "riddle_id": riddle_id,
            "button_id": f"riddle:submit:{riddle_id}",
            "ersteller": str(author.id),
            "winner": None
        }

        data.append(entry)
        await self.jsonbin.set(data)

        await interaction.followup.send(f"✅ Riddle created with ID `{riddle_id}`.", ephemeral=True)

    @app_commands.command(name="riddle_list", description="List existing riddles")
    async def riddle_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = await self.jsonbin.get()
        if not data:
            await interaction.followup.send("No riddles found.", ephemeral=True)
            return

        content = ""
        for i, r in enumerate(data[:20]):
            content += f"`{r['riddle_id']}` | {r['text'][:10]}... | by <@{r['ersteller']}>\n"
        await interaction.followup.send(f"**Riddle List:**\n{content}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(RiddleCommands(bot))

