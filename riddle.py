import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import uuid
import os
from datetime import datetime, timedelta

# Constants
RIDDLE_ROLE_ID = 1380610400416043089
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"
RIDDLE_DATA_FILE = "riddles.json"
GUILD_ID = 1346389858062434354

# Utility functions
def load_riddles():
    if not os.path.exists(RIDDLE_DATA_FILE):
        return {}
    with open(RIDDLE_DATA_FILE, "r") as f:
        return json.load(f)

def save_riddles(riddles):
    with open(RIDDLE_DATA_FILE, "w") as f:
        json.dump(riddles, f, indent=4)

def parse_mentions(mention_ids):
    return " ".join(f"<@{mid}>" for mid in mention_ids if mid)

def format_text(text):
    return text.replace("\\n", "\n")

class RiddleView(discord.ui.View):
    def __init__(self, riddle_id, author_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.author_id = author_id

    @discord.ui.button(label="Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution")
    async def submit_solution(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RiddleSolutionModal(self.riddle_id, self.author_id))

class RiddleSolutionModal(discord.ui.Modal, title="Submit your solution"):
    solution = discord.ui.TextInput(label="Your answer:", style=discord.TextStyle.paragraph)

    def __init__(self, riddle_id, author_id):
        super().__init__()
        self.riddle_id = riddle_id
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        riddles = load_riddles()
        riddle = riddles.get(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("This riddle no longer exists.", ephemeral=True)
            return

        author = await interaction.client.fetch_user(self.author_id)
        sender = interaction.user

        embed = discord.Embed(title=f"Solution for Riddle #{self.riddle_id}", color=discord.Color.blurple())
        embed.add_field(name="Riddle", value=format_text(riddle['text']), inline=False)
        embed.add_field(name="Proposed Solution", value=format_text(self.solution.value), inline=False)
        embed.set_author(name=sender.display_name, icon_url=sender.display_avatar.url)

        view = RiddleDecisionView(self.riddle_id, sender.id)

        try:
            await author.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.response.send_message("Could not DM the riddle author.", ephemeral=True)
            return

        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(embed=embed, view=view)

        await interaction.response.send_message("✅ Solution sent to the riddle author.", ephemeral=True)

class RiddleDecisionView(discord.ui.View):
    def __init__(self, riddle_id, solver_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.solver_id = solver_id

    @discord.ui.button(emoji="👍", style=discord.ButtonStyle.success, custom_id="approve_solution")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await close_riddle(interaction.client, self.riddle_id, winner_id=self.solver_id)
        await interaction.message.delete()

    @discord.ui.button(emoji="👎", style=discord.ButtonStyle.danger, custom_id="reject_solution")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        solver = await interaction.client.fetch_user(self.solver_id)
        try:
            await solver.send("❌ Sorry, your solution was not correct!")
        except discord.Forbidden:
            pass
        await interaction.message.delete()

async def close_riddle(bot, riddle_id, winner_id=None):
    riddles = load_riddles()
    riddle = riddles.get(riddle_id)
    if not riddle:
        return

    channel = bot.get_channel(riddle['channel_id'])
    mentions = [f"<@&{RIDDLE_ROLE_ID}>"] + [f"<@{mid}>" for mid in riddle.get('mention_groups', []) if mid]

    embed = discord.Embed(title="Riddle Solved!", color=discord.Color.green())
    embed.set_image(url=riddle.get('solution_image', DEFAULT_IMAGE))
    embed.add_field(name="Riddle", value=format_text(riddle['text']), inline=False)

    if winner_id:
        winner = await bot.fetch_user(winner_id)
        embed.add_field(name="Winner", value=f"{winner.mention}", inline=False)
        embed.set_thumbnail(url=winner.display_avatar.url)
    else:
        embed.add_field(name="Winner", value="No winner", inline=False)

    if channel:
        await channel.send(" ".join(mentions), embed=embed)

    # Cleanup
    riddles.pop(riddle_id)
    save_riddles(riddles)

class RiddleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.restore_riddles())

    async def restore_riddles(self):
        await self.bot.wait_until_ready()
        riddles = load_riddles()
        for riddle_id, riddle in riddles.items():
            view = RiddleView(riddle_id, riddle['author_id'])
            self.bot.add_view(view)
            end_time = datetime.fromisoformat(riddle['end_time'])
            if end_time > datetime.utcnow():
                self.bot.loop.create_task(self.schedule_riddle_close(riddle_id, end_time))
            else:
                await close_riddle(self.bot, riddle_id)

    async def schedule_riddle_close(self, riddle_id, end_time):
        now = datetime.utcnow()
        delay = (end_time - now).total_seconds()
        await discord.utils.sleep_until(end_time)
        await close_riddle(self.bot, riddle_id)

    @app_commands.command(name="riddle_add", description="Add a new riddle")
    async def riddle_add(self, interaction: discord.Interaction,
                         text: str,
                         solution: str,
                         channel: discord.TextChannel,
                         image_url: str = None,
                         mention_group1: discord.User = None,
                         mention_group2: discord.User = None,
                         solution_image: str = None,
                         length: int = 1):

        if RIDDLE_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ You are not allowed to use this command.", ephemeral=True)
            return

        await interaction.response.defer()

        riddle_id = str(uuid.uuid4())[:8]
        end_time = datetime.utcnow() + timedelta(days=length)
        mention_groups = list(filter(None, [(mention_group1.id if mention_group1 else None), (mention_group2.id if mention_group2 else None)]))

        riddle_data = {
            "text": text,
            "solution": solution.lower(),
            "author_id": interaction.user.id,
            "channel_id": channel.id,
            "image_url": image_url or DEFAULT_IMAGE,
            "solution_image": solution_image or DEFAULT_IMAGE,
            "mention_groups": mention_groups,
            "created_at": datetime.utcnow().isoformat(),
            "end_time": end_time.isoformat()
        }

        riddles = load_riddles()
        riddles[riddle_id] = riddle_data
        save_riddles(riddles)

        embed = discord.Embed(title=f"Riddle of the Day ({datetime.utcnow().strftime('%Y-%m-%d')})",
                              description=format_text(text), color=discord.Color.purple())
        embed.set_image(url=image_url or DEFAULT_IMAGE)
        embed.set_footer(text=f"Closes: {end_time.strftime('%Y-%m-%d %H:%M UTC')}")
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

        view = RiddleView(riddle_id, interaction.user.id)
        self.bot.add_view(view)

        await channel.send(f"<@&{RIDDLE_ROLE_ID}> {parse_mentions(mention_groups)}", embed=embed, view=view)

        self.bot.loop.create_task(self.schedule_riddle_close(riddle_id, end_time))

        await interaction.followup.send(f"✅ Riddle #{riddle_id} posted in {channel.mention}.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCog(bot))