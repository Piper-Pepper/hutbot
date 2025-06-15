import discord
import json
from datetime import datetime
from discord import TextChannel

RIDDLES_FILE = "riddles.json"
USER_STATS_FILE = "user_stats.json"
LOG_CHANNEL_ID = 1381754826710585527

def load_json(filename):
    with open(filename, 'r') as f:
        return json.load(f)

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

class SubmitSolutionView(discord.ui.View):
    def __init__(self, riddle_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id

    @discord.ui.button(label="💡 Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution")
    async def submit_solution(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SolutionModal(self.riddle_id))


class SolutionModal(discord.ui.Modal, title="💡 Submit Your Solution"):
    solution_input = discord.ui.TextInput(label="Your Solution", style=discord.TextStyle.paragraph)

    def __init__(self, riddle_id):
        super().__init__()
        self.riddle_id = riddle_id

    async def on_submit(self, interaction: discord.Interaction):
        riddles = load_json(RIDDLES_FILE)
        riddle = riddles.get(self.riddle_id)

        if not riddle:
            await interaction.response.send_message("❌ This riddle is already closed.", ephemeral=True)
            return

        creator = await interaction.client.fetch_user(riddle['creator_id'])
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)

        embed = discord.Embed(
            title="📝 New Solution Submitted!",
            description=f"💡 **Riddle:** {riddle['text']}\n\n🧩 **Submitted Solution:** {self.solution_input.value}",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"From: {interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

        view = CreatorDMView(self.riddle_id, submitter_id=interaction.user.id, submitted_solution=self.solution_input.value)
        dm_message = await creator.send(embed=embed, view=view)
        log_message = await log_channel.send(embed=embed, view=ModerationView(self.riddle_id, submitter_id=interaction.user.id, submitted_solution=self.solution_input.value))

        riddles[self.riddle_id]['creator_dm_message_id'] = dm_message.id
        riddles[self.riddle_id]['log_message_id'] = log_message.id
        save_json(RIDDLES_FILE, riddles)

        user_stats = load_json(USER_STATS_FILE)
        user_stats[str(interaction.user.id)] = user_stats.get(str(interaction.user.id), {"solved": 0, "submitted": 0})
        user_stats[str(interaction.user.id)]['submitted'] += 1
        save_json(USER_STATS_FILE, user_stats)

        await interaction.response.send_message("✅ Solution submitted! The riddle creator has been notified.", ephemeral=True)


class CreatorDMView(discord.ui.View):
    def __init__(self, riddle_id, submitter_id=None, submitted_solution=None):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.submitter_id = submitter_id
        self.submitted_solution = submitted_solution

    @discord.ui.button(label="✅ Accept Solution", style=discord.ButtonStyle.success, custom_id="accept_solution_dm")
    async def accept_solution(self, interaction: discord.Interaction, button: discord.ui.Button):
        from .riddle import Riddle
        cog = interaction.client.get_cog('Riddle')
        if not cog or self.riddle_id not in cog.riddles:
            await interaction.response.send_message("❌ The riddle is already closed.", ephemeral=True)
            return

        winner = await interaction.client.fetch_user(self.submitter_id)
        await cog.close_riddle(self.riddle_id, winner=winner, submitted_solution=self.submitted_solution)
        await interaction.response.send_message("✅ Riddle closed successfully.", ephemeral=True)

    @discord.ui.button(label="❌ Reject Solution", style=discord.ButtonStyle.danger, custom_id="reject_solution_dm")
    async def reject_solution(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            user = await interaction.client.fetch_user(self.submitter_id)
            await user.send("❌ Your solution was not correct. Better luck next time!")
        except:
            pass

        await interaction.response.send_message("❌ Solution rejected and submitter notified.", ephemeral=True)


class ModerationView(discord.ui.View):
    def __init__(self, riddle_id, submitter_id=None, submitted_solution=None):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.submitter_id = submitter_id
        self.submitted_solution = submitted_solution

    @discord.ui.button(label="✅ Accept Solution", style=discord.ButtonStyle.success, custom_id="accept_solution_log")
    async def accept_solution_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        from .riddle import Riddle
        cog = interaction.client.get_cog('Riddle')
        if not cog or self.riddle_id not in cog.riddles:
            await interaction.response.send_message("❌ The riddle is already closed.", ephemeral=True)
            return

        winner = await interaction.client.fetch_user(self.submitter_id)
        await cog.close_riddle(self.riddle_id, winner=winner, submitted_solution=self.submitted_solution)
        await interaction.response.send_message("✅ Riddle closed successfully.", ephemeral=True)

    @discord.ui.button(label="❌ Reject Solution", style=discord.ButtonStyle.danger, custom_id="reject_solution_log")
    async def reject_solution_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            user = await interaction.client.fetch_user(self.submitter_id)
            await user.send("❌ Your solution was not correct. Better luck next time!")
        except:
            pass

        await interaction.response.send_message("❌ Solution rejected and submitter notified.", ephemeral=True)
