# riddle_views.py
import discord
from discord.ui import View, Button, Modal, TextInput
from discord import Interaction
from .riddle_utils import load_riddles, save_riddles
import json

RIDDLE_DB_PATH = 'data/riddles.json'
USER_STATS_PATH = 'data/user_stats.json'
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"

class SubmitSolutionModal(Modal):
    def __init__(self, riddle_id: str, bot):
        super().__init__(title="Submit Your Riddle Solution")
        self.bot = bot
        self.riddle_id = riddle_id
        self.answer = TextInput(label="Your Answer", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.answer)

    async def on_submit(self, interaction: Interaction):
        riddles = load_riddles(RIDDLE_DB_PATH)
        riddle = riddles.get(self.riddle_id)

        if not riddle or not riddle.get("active"):
            await interaction.response.send_message("This riddle is already closed.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Submission for Riddle ID: {self.riddle_id}",
                              description=riddle["text"], color=discord.Color.orange())
        embed.add_field(name="Submitted Solution", value=self.answer.value)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar.url)

        # DM riddle creator
        author = await self.bot.fetch_user(riddle["author_id"])
        if author:
            try:
                view = SolutionReviewButtons(riddle_id=self.riddle_id, submitter_id=interaction.user.id,
                                             submitted_solution=self.answer.value, bot=self.bot)
                await author.send(embed=embed, view=view)
            except discord.Forbidden:
                pass

        # Log in designated channel
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            view = SolutionReviewButtons(riddle_id=self.riddle_id, submitter_id=interaction.user.id,
                                         submitted_solution=self.answer.value, bot=self.bot)
            await log_channel.send(embed=embed, view=view)

        await interaction.response.send_message("Your solution was submitted!", ephemeral=True)

class SubmitSolutionView(View):
    def __init__(self, riddle_id: str, bot):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.bot = bot
        self.add_item(SubmitSolutionButton(riddle_id))

class SubmitSolutionButton(Button):
    def __init__(self, riddle_id):
        super().__init__(label="Submit Solution", style=discord.ButtonStyle.primary,
                         custom_id=f"submit_{riddle_id}")
        self.riddle_id = riddle_id

    async def callback(self, interaction: Interaction):
        modal = SubmitSolutionModal(riddle_id=self.riddle_id, bot=interaction.client)
        await interaction.response.send_modal(modal)

class SolutionReviewButtons(View):
    def __init__(self, riddle_id: str, submitter_id: int, submitted_solution: str, bot):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.submitter_id = submitter_id
        self.submitted_solution = submitted_solution
        self.bot = bot
        self.add_item(Button(emoji="✅", style=discord.ButtonStyle.success,
                             custom_id=f"approve_{riddle_id}"))
        self.add_item(Button(emoji="❌", style=discord.ButtonStyle.danger,
                             custom_id=f"reject_{riddle_id}"))

    async def interaction_check(self, interaction: Interaction) -> bool:
        return True  # Permissions handled in riddle_cog

    async def on_timeout(self):
        pass  # Persisted views never timeout

    async def interaction(self, interaction: Interaction):
        riddles = load_riddles(RIDDLE_DB_PATH)
        riddle = riddles.get(self.riddle_id)

        if not riddle or not riddle.get("active"):
            await interaction.response.send_message("The riddle is already closed.", ephemeral=True)
            return

        custom_id = interaction.data["custom_id"]

        if custom_id.startswith("approve_"):
            await self.close_riddle(interaction, riddle, approved=True)
        elif custom_id.startswith("reject_"):
            await interaction.message.delete()
            try:
                submitter = await self.bot.fetch_user(self.submitter_id)
                await submitter.send("Sorry, your solution was not correct!")
            except discord.Forbidden:
                pass

    async def close_riddle(self, interaction: Interaction, riddle: dict, approved: bool):
        channel = self.bot.get_channel(riddle["channel_id"])
        original_message = await channel.fetch_message(riddle["message_id"])

        # Update embed to mark as closed
        embed = original_message.embeds[0]
        embed.color = discord.Color.red()
        embed.title += " [CLOSED]"
        new_view = View()  # Empty view to remove old buttons
        await original_message.edit(embed=embed, view=new_view)

        riddles = load_riddles(RIDDLE_DB_PATH)
        user_stats = load_riddles(USER_STATS_PATH)
        riddle_data = riddles.pop(self.riddle_id, None)
        save_riddles(RIDDLE_DB_PATH, riddles)

        if approved:
            winner = await self.bot.fetch_user(self.submitter_id)
            user_stats[str(winner.id)] = user_stats.get(str(winner.id), {"submitted": 0, "solved": 0})
            user_stats[str(winner.id)]["solved"] += 1
            save_riddles(USER_STATS_PATH, user_stats)
        else:
            winner = None

        sol_embed = discord.Embed(
            title="Riddle Solution Revealed",
            description=f"**Riddle:** {riddle['text']}\n**Answer:** {riddle['solution']}\n",
            color=discord.Color.green()
        )
        sol_embed.set_image(url=riddle["solution_image"] or DEFAULT_IMAGE_URL)
        sol_embed.set_footer(text=f"Winner: {winner.display_name if winner else 'No winner'}",
                             icon_url=winner.avatar.url if winner else None)

        mentions = f"<@&{RIDDLE_GROUP_ID}>"
        for m in riddle["mentions"]:
            if m:
                mentions += f" <@&{m}>"

        await channel.send(content=mentions, embed=sol_embed)
        await interaction.response.send_message("Riddle closed.", ephemeral=True)

