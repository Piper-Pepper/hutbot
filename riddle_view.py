# riddle_view.py

import discord
import json
import os
from discord.ext import commands

RIDDLE_LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"

# Utility to load JSON data
def load_json(path):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({}, f)
    with open(path, "r") as f:
        return json.load(f)

# Utility to write JSON data
def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

# Modal for solution submission
class SolutionModal(discord.ui.Modal, title="Submit Your Riddle Solution"):
    def __init__(self, riddle_id, riddle_text, creator_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.riddle_text = riddle_text
        self.creator_id = creator_id

        self.answer_input = discord.ui.TextInput(
            label="Your solution:",
            placeholder="Type your answer here...",
            max_length=400,
            required=True
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Solution submitted!", ephemeral=True)

        submitter = interaction.user
        creator = await interaction.client.fetch_user(self.creator_id)

        # Embed to send to the creator and mod log
        embed = discord.Embed(
            title=f"Solution for Riddle #{self.riddle_id}",
            description=f"**Submitted by:** {submitter.mention}\n\n**Riddle Text:**\n{self.riddle_text}\n\n**Answer:**\n{self.answer_input.value}",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=submitter.display_avatar.url)

        view = JudgeButtons(self.riddle_id, submitter.id)

        try:
            await creator.send(embed=embed, view=view)
        except discord.Forbidden:
            pass  # Creator's DMs are closed

        log_channel = interaction.client.get_channel(RIDDLE_LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(embed=embed, view=view)


# Button view for judging a solution (DM + log)
class JudgeButtons(discord.ui.View):
    def __init__(self, riddle_id, submitter_id):
        super().__init__(timeout=None)
        self.riddle_id = str(riddle_id)
        self.submitter_id = submitter_id

    @discord.ui.button(label="✅ Accept Solution", style=discord.ButtonStyle.success, custom_id="judge_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddles = load_json("riddles.json")
        riddle = riddles.get(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("The riddle is already closed.", ephemeral=True)
            return

        riddle["winner_id"] = self.submitter_id
        await end_riddle(interaction.client, self.riddle_id)
        await interaction.response.send_message("Riddle has been closed with a winner!", ephemeral=True)

    @discord.ui.button(label="❌ Reject Solution", style=discord.ButtonStyle.danger, custom_id="judge_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddles = load_json("riddles.json")
        if self.riddle_id not in riddles:
            await interaction.response.send_message("The riddle is already closed.", ephemeral=True)
            return

        submitter = await interaction.client.fetch_user(self.submitter_id)
        try:
            await submitter.send("❌ Sorry, your solution was not correct!")
        except discord.Forbidden:
            pass  # Submitter's DMs are closed

        await interaction.message.delete()
        await interaction.response.send_message("Rejected the solution and removed the message.", ephemeral=True)


# Button view for main riddle message
class RiddleSubmitView(discord.ui.View):
    def __init__(self, riddle_id, riddle_text, creator_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.riddle_text = riddle_text
        self.creator_id = creator_id

    @discord.ui.button(label="Submit Solution", style=discord.ButtonStyle.primary, emoji="🧠", custom_id="submit_solution_button")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SolutionModal(self.riddle_id, self.riddle_text, self.creator_id))


# Close riddle and send solution embed
async def end_riddle(bot, riddle_id: str):
    riddles = load_json("riddles.json")
    riddle = riddles.get(riddle_id)
    if not riddle:
        return

    try:
        channel = bot.get_channel(int(riddle["channel_id"]))
        if not channel:
            channel = await bot.fetch_channel(int(riddle["channel_id"]))
        message = await channel.fetch_message(int(riddle["message_id"]))
        await message.edit(view=None)
    except Exception:
        pass

    winner_id = riddle.get("winner_id")
    winner = await bot.fetch_user(winner_id) if winner_id else None
    solution_image = riddle.get("solution_image") or DEFAULT_IMAGE_URL

    embed = discord.Embed(
        title=f"🧩 Riddle Closed: #{riddle_id}",
        description=f"**Riddle:**\n{riddle['text']}\n\n**Solution:** {riddle['solution']}",
        color=discord.Color.green()
    )
    embed.set_image(url=solution_image)
    if winner:
        embed.add_field(name="✅ Winner", value=f"{winner.mention}", inline=False)
        embed.set_thumbnail(url=winner.display_avatar.url)
    else:
        embed.add_field(name="❌ No Winner", value="Better luck next time!", inline=False)

    if riddle.get("award"):
        embed.add_field(name="Award", value=riddle["award"], inline=False)

    embed.set_footer(text="Riddle closed")

    await channel.send(content=riddle["mention_text"], embed=embed)

    # Delete judge messages if any
    log_channel = bot.get_channel(RIDDLE_LOG_CHANNEL_ID)
    if log_channel:
        async for msg in log_channel.history(limit=100):
            if msg.embeds and f"Riddle #{riddle_id}" in msg.embeds[0].title:
                await msg.delete()

    # Clean up
    riddles.pop(riddle_id)
    write_json("riddles.json", riddles)
