import discord
from discord.ui import View, Button, Modal, TextInput
import json
import os
from datetime import datetime
from riddle_utils import riddle_cache, close_riddle_with_winner  # ✅ Hier die Fixes

LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"
RIDDLE_GROUP_ID = 1380610400416043089

# --- Solution Modal ---
class SolutionModal(Modal, title="Submit Your Riddle Solution"):
    def __init__(self, riddle_id: str, riddle_text: str, creator_id: int):
        super().__init__()
        self.riddle_id = riddle_id
        self.riddle_text = riddle_text
        self.creator_id = creator_id
        self.solution_input = TextInput(label="Your Answer", style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.solution_input)

    async def on_submit(self, interaction: discord.Interaction):
        submitter = interaction.user
        embed = discord.Embed(
            title=f"Solution Submission: Riddle ID {self.riddle_id}",
            description=f"**Riddle:**\n{self.riddle_text}\n\n**Submitted Solution:**\n{self.solution_input.value}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_author(name=submitter.display_name, icon_url=submitter.display_avatar.url)

        view = ApprovalButtonsView(
            riddle_id=self.riddle_id,
            submitter=submitter,
            submitted_solution=self.solution_input.value
        )

        try:
            creator = await interaction.client.fetch_user(self.creator_id)
            await creator.send(embed=embed, view=view)
        except:
            pass

        try:
            channel = interaction.client.get_channel(LOG_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed, view=view)
        except:
            pass

        await interaction.response.send_message("Your solution has been submitted!", ephemeral=True)

# --- Submit Button ---
class SubmitSolutionButton(Button):
    def __init__(self, riddle_id: str, riddle_text: str, creator_id: int):
        super().__init__(label="Submit Solution", style=discord.ButtonStyle.primary, emoji="🧠", custom_id=f"submit_{riddle_id}")
        self.riddle_id = riddle_id
        self.riddle_text = riddle_text.replace('\\n', '\n')
        self.creator_id = creator_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SolutionModal(self.riddle_id, self.riddle_text, self.creator_id))

        # 🔎 Hole den Rätsel-Channel
        riddle_channel = interaction.guild.get_channel(riddle_data["channel_id"])
        if not riddle_channel:
            await interaction.response.send_message("Riddle channel not found.", ephemeral=True)
            return

        # 🧹 Alte Ablehnungen löschen
        async for msg in riddle_channel.history(limit=100):
            if (
                msg.author == interaction.client.user and
                msg.embeds and
                msg.embeds[0].footer and
                "Sadly, the proposed solution" in msg.embeds[0].footer.text and
                msg.embeds[0].description == self.parent_view.submitted_solution
            ):
                try:
                    await msg.delete()
                except:
                    pass

        # ✉️ Neues Embed der Ablehnung
        embed = discord.Embed(
            title="❌ Incorrect Solution",
            description=self.parent_view.submitted_solution,
            color=discord.Color.red()
        )
        embed.set_footer(text="Sadly, the proposed solution was not correct...!")
        embed.set_thumbnail(url=self.parent_view.submitter.display_avatar.url)
  

        await riddle_channel.send(embed=embed)

        # 🔕 Direktnachricht
        try:
            await self.parent_view.submitter.send("❌ Sorry, your solution was not correct!")
        except:
            pass

# --- Approval View ---
class ApprovalButtonsView(View):
    def __init__(self, riddle_id, submitter, submitted_solution):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.submitter = submitter
        self.submitted_solution = submitted_solution
        self.add_item(ApproveButton(self))
        self.add_item(RejectButton(self))

class ApproveButton(Button):
    def __init__(self, parent_view):
        super().__init__(emoji="👍", style=discord.ButtonStyle.success, custom_id=f"approve_{parent_view.riddle_id}")
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if self.parent_view.riddle_id not in riddle_cache:
            await interaction.response.send_message("The riddle is already closed.", ephemeral=True)
            return

        await close_riddle_with_winner(
            interaction.client,
            self.parent_view.riddle_id,
            self.parent_view.submitter,
            self.parent_view.submitted_solution
        )

        await interaction.message.delete()

class RejectButton(Button):
    def __init__(self, parent_view):
        super().__init__(emoji="👎", style=discord.ButtonStyle.danger, custom_id=f"reject_{parent_view.riddle_id}")
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        riddle_data = riddle_cache.get(self.parent_view.riddle_id)
        if not riddle_data:
            await interaction.response.send_message("Riddle not found or already closed.", ephemeral=True)
            return

        riddle_channel = interaction.guild.get_channel(riddle_data["channel_id"])
        if not riddle_channel:
            await interaction.response.send_message("Riddle channel not found.", ephemeral=True)
            return

        async for msg in riddle_channel.history(limit=100):
            if (
                msg.author == interaction.client.user and
                msg.embeds and
                msg.embeds[0].footer and
                msg.embeds[0].footer.text == "Sadly, the proposed solution was not correct...!" and
                msg.embeds[0].description == self.parent_view.submitted_solution
            ):
                try:
                    await msg.delete()
                except:
                    pass

        embed = discord.Embed(
            title="❌ Incorrect Solution",
            description=self.parent_view.submitted_solution,
            color=discord.Color.red()
        )
        embed.set_footer(text="Sadly, the proposed solution was not correct...!")
        embed.set_thumbnail(url=self.parent_view.submitter.display_avatar.url)


        await riddle_channel.send(embed=embed)

        try:
            await self.parent_view.submitter.send("❌ Sorry, your solution was not correct!")
        except:
            pass

        await interaction.message.delete()
# --- View Setup ---
async def setup_persistent_views(bot):
    if os.path.exists("riddles.json"):
        with open("riddles.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            for riddle_id, riddle_data in data.items():
                riddle_cache[riddle_id] = riddle_data
                view = View(timeout=None)
                view.add_item(SubmitSolutionButton(
                    riddle_id=riddle_id,
                    riddle_text=riddle_data['text'],
                    creator_id=riddle_data['creator_id']
                ))
                bot.add_view(view)
