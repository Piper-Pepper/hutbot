import discord
from discord import TextChannel
from datetime import datetime, timedelta
import json
from discord.ui import View, Button, Modal, TextInput, Select

RIDDLES_FILE = "riddles.json"
USER_STATS_FILE = "user_stats.json"
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_SOLUTION_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"

def load_json(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

class SubmitSolutionView(View):
    def __init__(self, riddle_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id

    @discord.ui.button(label="💡 Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution_button")
    async def submit_solution_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SolutionModal(self.riddle_id))

class SolutionModal(Modal, title="💡 Submit Your Solution"):
    solution_input = TextInput(label="Your Solution", style=discord.TextStyle.paragraph, placeholder="Type your solution here...")

    def __init__(self, riddle_id):
        super().__init__()
        self.riddle_id = riddle_id

    async def on_submit(self, interaction: discord.Interaction):
        riddles = load_json(RIDDLES_FILE)
        riddle = riddles.get(self.riddle_id)

        if not riddle:
            await interaction.response.send_message("❌ This riddle is already closed.", ephemeral=True)
            return

        # Send DM to creator with solution and post log message
        creator = await interaction.client.fetch_user(riddle['creator_id'])
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)

        embed = discord.Embed(
            title="📝 New Solution Submitted!",
            description=(
                f"**Riddle:** {riddle['text']}\n\n"
                f"**Submitted Solution:** {self.solution_input.value}"
            ),
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"From: {interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

        # Create Creator DM view with accept/reject buttons
        view_creator_dm = CreatorDMView(self.riddle_id, submitter_id=interaction.user.id, submitted_solution=self.solution_input.value)
        dm_message = await creator.send(embed=embed, view=view_creator_dm)

        # Create log channel view with accept/reject buttons
        view_log = ModerationView(self.riddle_id, submitter_id=interaction.user.id, submitted_solution=self.solution_input.value)
        log_message = await log_channel.send(embed=embed, view=view_log)

        # Store message IDs for reference
        riddles[self.riddle_id]['creator_dm_message_id'] = dm_message.id
        riddles[self.riddle_id]['log_message_id'] = log_message.id
        save_json(RIDDLES_FILE, riddles)

        # Update user stats (submitted count)
        user_stats = load_json(USER_STATS_FILE)
        user_id_str = str(interaction.user.id)
        user_stats.setdefault(user_id_str, {"solved": 0, "submitted": 0})
        user_stats[user_id_str]["submitted"] += 1
        save_json(USER_STATS_FILE, user_stats)

        await interaction.response.send_message("✅ Solution submitted! The riddle creator has been notified.", ephemeral=True)

class CreatorDMView(View):
    def __init__(self, riddle_id, submitter_id=None, submitted_solution=None):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.submitter_id = submitter_id
        self.submitted_solution = submitted_solution

    @discord.ui.button(label="✅ Accept Solution", style=discord.ButtonStyle.success, custom_id="accept_solution_dm")
    async def accept_solution(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        except Exception:
            pass
        await interaction.response.send_message("❌ Solution rejected and submitter notified.", ephemeral=True)

class ModerationView(View):
    def __init__(self, riddle_id, submitter_id=None, submitted_solution=None):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.submitter_id = submitter_id
        self.submitted_solution = submitted_solution

    @discord.ui.button(label="✅ Accept Solution", style=discord.ButtonStyle.success, custom_id="accept_solution_log")
    async def accept_solution_log(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        except Exception:
            pass
        await interaction.response.send_message("❌ Solution rejected and submitter notified.", ephemeral=True)


# === NEU: Riddle List View mit Select Menu für offene Rätsel ===

class RiddleListView(View):
    def __init__(self, riddles_dict):
        super().__init__(timeout=None)
        self.riddles = riddles_dict

        # Build options for select menu: label=ID + creator + date, value=riddle_id
        options = []
        for riddle_id, riddle in self.riddles.items():
            created = datetime.fromisoformat(riddle['created_at']).strftime("%Y-%m-%d")
            creator = f"<@{riddle['creator_id']}>"
            label = f"ID {riddle_id} | Created: {created} | By: {creator}"
            options.append(discord.SelectOption(label=label, value=riddle_id))

        self.select = Select(
            placeholder="Select a riddle to view",
            options=options,
            custom_id="riddle_list_select"
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        riddle_id = self.select.values[0]
        riddle = self.riddles.get(riddle_id)

        if not riddle:
            await interaction.response.send_message("❌ Riddle not found or already closed.", ephemeral=True)
            return

        # Show original riddle embed with full text & solution (only to requesting user)
        embed = discord.Embed(
            title=f"Goon Hut Riddle (Created: {datetime.fromisoformat(riddle['created_at']).strftime('%Y-%m-%d %H:%M UTC')})",
            description=riddle['text'].replace("\\n", "\n"),
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Solution", value=riddle['solution'], inline=False)
        if riddle.get("award"):
            embed.add_field(name="Award", value=riddle["award"], inline=False)
        embed.set_footer(text=f"Created by <@{riddle['creator_id']}> | Closes at {datetime.fromisoformat(riddle['close_at']).strftime('%Y-%m-%d %H:%M UTC')}")

     
        options_view = RiddleOptionsView(riddle_id)
        await interaction.response.send_message(embed=embed, view=options_view, ephemeral=True)


class RiddleOptionsView(View):
    def __init__(self, riddle_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id

    @discord.ui.button(label="🏁 Close with Winner", style=discord.ButtonStyle.success, custom_id="close_with_winner")
    async def close_with_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseRiddleModal(self.riddle_id, close_with_winner=True))

    @discord.ui.button(label="❌ Close without Winner", style=discord.ButtonStyle.danger, custom_id="close_without_winner")
    async def close_without_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseRiddleModal(self.riddle_id, close_with_winner=False))
