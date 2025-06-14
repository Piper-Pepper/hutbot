# riddle_views.py (Teil 1)

import discord
from discord import TextChannel
from discord.ui import View, Button, Modal, TextInput, Select
from datetime import datetime, timedelta
import json
from utils import load_json, save_json, RIDDLES_FILE, USER_STATS_FILE, LOG_CHANNEL_ID, DEFAULT_SOLUTION_IMAGE


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

    @discord.ui.button(label="\U0001F4A1 Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution_button")
    async def submit_solution_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SolutionModal(self.riddle_id))


class SolutionModal(Modal, title="\U0001F4A1 Submit Your Solution"):
    solution_input = TextInput(label="Your Solution", style=discord.TextStyle.paragraph, placeholder="Type your solution here...")

    def __init__(self, riddle_id):
        super().__init__()
        self.riddle_id = riddle_id

    async def on_submit(self, interaction: discord.Interaction):
        riddles = load_json(RIDDLES_FILE)
        riddle = riddles.get(self.riddle_id)

        if not riddle:
            await interaction.response.send_message("\u274C This riddle is already closed.", ephemeral=True)
            return

        solution_text = self.solution_input.value.replace("\\n", "\n")
        creator = await interaction.client.fetch_user(riddle['creator_id'])
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)

        embed = discord.Embed(
            title="\U0001F4DD New Solution Submitted!",
            description=f"**Riddle:** {riddle['text'].replace('\\n', '\n')}\n\n**Submitted Solution:** {solution_text}",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"From: {interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

        view_creator_dm = CreatorDMView(self.riddle_id, submitter_id=interaction.user.id, submitted_solution=solution_text)
        view_log = ModerationView(self.riddle_id, submitter_id=interaction.user.id, submitted_solution=solution_text)

        dm_message = await creator.send(embed=embed, view=view_creator_dm)
        log_message = await log_channel.send(embed=embed, view=view_log)

        riddle.setdefault('submissions', []).append({
            "user_id": interaction.user.id,
            "solution": solution_text,
            "log_msg_id": log_message.id
        })

        riddles[self.riddle_id]['creator_dm_message_id'] = dm_message.id
        riddles[self.riddle_id]['log_message_id'] = log_message.id
        save_json(RIDDLES_FILE, riddles)

        user_stats = load_json(USER_STATS_FILE)
        uid = str(interaction.user.id)
        user_stats.setdefault(uid, {"solved": 0, "submitted": 0})
        user_stats[uid]["submitted"] += 1
        save_json(USER_STATS_FILE, user_stats)

        await interaction.response.send_message("\u2705 Solution submitted! The riddle creator has been notified.", ephemeral=True)
# riddle_views.py (Teil 2)


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
        riddles = load_json(RIDDLES_FILE)
        riddle = riddles.get(self.riddle_id)
        if riddle:
            riddle['submissions'] = [s for s in riddle.get('submissions', []) if s['user_id'] != self.submitter_id]
            save_json(RIDDLES_FILE, riddles)
        user = await interaction.client.fetch_user(self.submitter_id)
        try:
            await user.send("❌ Your solution was not correct. Better luck next time!")
        except:
            pass
        channel = interaction.channel
        embed = discord.Embed(
            title="❌ Incorrect Solution",
            description=f"The submitted solution `{self.submitted_solution}` was not correct.",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=user.display_name, icon_url=user.avatar.url if user.avatar else None)
        await channel.send(embed=embed)
        await interaction.response.send_message("❌ Solution rejected and submitter notified.", ephemeral=True)


class ModerationView(CreatorDMView):
    def __init__(self, riddle_id, submitter_id=None, submitted_solution=None):
        super().__init__(riddle_id, submitter_id, submitted_solution)
        self.children[0].custom_id = "accept_solution_log"
        self.children[1].custom_id = "reject_solution_log"


class RiddleListView(View):
    def __init__(self, riddles_dict):
        super().__init__(timeout=None)
        self.riddles = riddles_dict
        options = []
        for rid, data in self.riddles.items():
            created = datetime.fromisoformat(data['created_at']).strftime("%Y-%m-%d")
            creator = f"<@{data['creator_id']}>"
            options.append(discord.SelectOption(
                label=f"ID {rid} | {created} | By {creator}",
                value=rid
            ))
        select = Select(placeholder="Choose a riddle", options=options, custom_id="riddle_list_select")
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        rid = self.children[0].values[0]
        riddles = load_json(RIDDLES_FILE)
        riddle = riddles.get(rid)
        if not riddle:
            await interaction.response.send_message("❌ Riddle not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🧠 Goon Hut Riddle",
            description=riddle['text'].replace("\\n", "\n"),
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Solution", value=riddle['solution'], inline=False)
        if riddle.get("award"):
            embed.add_field(name="Award", value=riddle['award'], inline=False)
        embed.set_image(url=riddle.get('image', DEFAULT_SOLUTION_IMAGE))
        embed.set_footer(text=f"By <@{riddle['creator_id']}> | Closes at {riddle['close_at']}")

        await interaction.response.send_message(embed=embed, view=RiddleOptionsView(rid), ephemeral=True)


class RiddleOptionsView(View):
    def __init__(self, rid):
        super().__init__(timeout=None)
        self.riddle_id = rid

    @discord.ui.button(label="🏁 Close with Winner", style=discord.ButtonStyle.success, custom_id="close_with_winner")
    async def close_with_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseRiddleModal(self.riddle_id, close_with_winner=True))

    @discord.ui.button(label="❌ Close without Winner", style=discord.ButtonStyle.danger, custom_id="close_without_winner")
    async def close_without_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseRiddleModal(self.riddle_id, close_with_winner=False))

    @discord.ui.button(label="🗑️ Delete Riddle", style=discord.ButtonStyle.secondary, custom_id="delete_riddle")
    async def delete_riddle(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddles = load_json(RIDDLES_FILE)
        riddle = riddles.get(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("❌ Riddle not found.", ephemeral=True)
            return

        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        # Delete related log message
        try:
            if 'log_message_id' in riddle:
                msg = await log_channel.fetch_message(riddle['log_message_id'])
                await msg.delete()
        except:
            pass

        for sub in riddle.get("submissions", []):
            try:
                msg = await log_channel.fetch_message(sub['log_msg_id'])
                await msg.delete()
            except:
                pass

        del riddles[self.riddle_id]
        save_json(RIDDLES_FILE, riddles)
        await interaction.response.send_message("🗑️ Riddle and all related messages deleted.", ephemeral=True)
