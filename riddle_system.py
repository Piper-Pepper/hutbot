import discord
from discord.ext import commands, tasks
from discord import app_commands
import uuid
import json
from datetime import datetime, timedelta

# Constants
RIDDLE_CHANNEL_ID = 1349697597232906292
SOLUTION_CHANNEL_ID = 1349697597232906292
MODERATOR_ROLE_ID = 1380610400416043089
FIXED_MENTIONS = ["<@&1380610400416043089>"]
DEFAULT_RIDDLE_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1381679872468062339/riddle_logo.jpg"
DEFAULT_SOLUTION_IMAGE = DEFAULT_RIDDLE_IMAGE
RIDDLE_STORAGE_FILE = "active_riddles.json"

# In-memory storage
active_riddles = {}

# --------- Riddle Data Class ----------
class RiddleData:
    def __init__(self, author_id, question, solution, created_at, length, image_url, mentions, solution_img, winner_id=None, riddle_id=None):
        self.id = riddle_id or f"#{str(uuid.uuid4())[:6].upper()}"
        self.author_id = author_id
        self.question = question
        self.solution = solution
        self.created_at = created_at
        self.length = length
        self.image_url = image_url or DEFAULT_RIDDLE_IMAGE
        self.mentions = FIXED_MENTIONS + mentions
        self.solution_img = solution_img or DEFAULT_SOLUTION_IMAGE
        self.winner_id = winner_id

    def is_expired(self):
        return datetime.utcnow() > datetime.fromisoformat(self.created_at) + timedelta(days=self.length)

    def to_dict(self):
        return {
            "id": self.id,
            "author_id": self.author_id,
            "question": self.question,
            "solution": self.solution,
            "created_at": self.created_at,
            "length": self.length,
            "image_url": self.image_url,
            "mentions": self.mentions,
            "solution_img": self.solution_img,
            "winner_id": self.winner_id
        }

    @staticmethod
    def from_dict(data):
        return RiddleData(
            author_id=data["author_id"],
            question=data["question"],
            solution=data["solution"],
            created_at=data["created_at"],
            length=data["length"],
            image_url=data["image_url"],
            mentions=data["mentions"],
            solution_img=data["solution_img"],
            winner_id=data.get("winner_id"),
            riddle_id=data["id"]
        )

# --------- Storage helpers ----------
def save_riddles():
    with open(RIDDLE_STORAGE_FILE, "w") as f:
        json.dump({rid: r.to_dict() for rid, r in active_riddles.items()}, f, indent=4)

def load_riddles():
    global active_riddles
    try:
        with open(RIDDLE_STORAGE_FILE, "r") as f:
            data = json.load(f)
            active_riddles = {rid: RiddleData.from_dict(r) for rid, r in data.items()}
    except FileNotFoundError:
        active_riddles = {}

# --------- Bot Commands ----------
async def setup_riddle_commands(bot: commands.Bot):
    load_riddles()
    for riddle in active_riddles.values():
        bot.add_view(RiddleSolveView(riddle_id=riddle.id, riddle_text=riddle.question, author_id=riddle.author_id))

    @bot.tree.command(name="riddle_add", description="Add a new riddle of the day.")
    @app_commands.checks.has_role(MODERATOR_ROLE_ID)
    @app_commands.describe(
        text="The riddle text",
        solution="The correct answer",
        image_url="Optional image URL for the riddle",
        mention_group1="Optional mention group 1",
        mention_group2="Optional mention group 2",
        solution_image_url="Image URL to show with solution",
        length="How many days the riddle should be active (default 1)"
    )
    async def riddle_add(interaction: discord.Interaction, 
                         text: str, 
                         solution: str, 
                         image_url: str = None,
                         mention_group1: discord.Role = None,
                         mention_group2: discord.Role = None,
                         solution_image_url: str = None,
                         length: int = 1):
        await interaction.response.defer()

        mentions = [f"<@&{mention_group1.id}>" if mention_group1 else "",
                    f"<@&{mention_group2.id}>" if mention_group2 else ""]
        mentions = [m for m in mentions if m]

        riddle = RiddleData(
            author_id=interaction.user.id,
            question=text,
            solution=solution,
            created_at=datetime.utcnow().isoformat(),
            length=length,
            image_url=image_url,
            mentions=mentions,
            solution_img=solution_image_url
        )

        active_riddles[riddle.id] = riddle
        save_riddles()

        embed = discord.Embed(
            title=f"🧩 Riddle of the Day (Posted: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})",
            description=f"> {text}\n\n**Created by:** {interaction.user.mention}",
            color=discord.Color.blurple()
        )
        embed.set_image(url=riddle.image_url)
        embed.set_thumbnail(url=interaction.user.avatar.url)
        embed.set_footer(text=f"Riddle ID: {riddle.id} • Think wisely...")

        view = RiddleSolveView(riddle_id=riddle.id, riddle_text=text, author_id=interaction.user.id)
        bot.add_view(view)

        channel = await bot.fetch_channel(RIDDLE_CHANNEL_ID)
        await channel.send(" ".join(riddle.mentions), embed=embed, view=view)
        await interaction.followup.send(f"✅ Riddle `{riddle.id}` posted successfully.")

    @bot.tree.command(name="riddle_win", description="Mark a riddle as solved (with or without a winner).")
    @app_commands.checks.has_role(MODERATOR_ROLE_ID)
    async def riddle_win(interaction: discord.Interaction, riddle_id: str, winner: discord.Member = None):
        riddle = active_riddles.get(riddle_id)
        if not riddle:
            if not active_riddles:
                await interaction.response.send_message("❌ There are no open riddles.", ephemeral=True)
            else:
                ids = ", ".join(active_riddles.keys())
                await interaction.response.send_message(f"❌ Invalid ID. Open riddles: {ids}", ephemeral=True)
            return

        if winner:
            riddle.winner_id = winner.id

        await post_solution(bot, riddle)
        del active_riddles[riddle_id]
        save_riddles()
        if winner:
            await interaction.response.send_message(f"🎉 Riddle `{riddle_id}` closed with winner {winner.mention}.")
        else:
            await interaction.response.send_message(f"🛑 Riddle `{riddle_id}` ended manually.")

    @bot.tree.command(name="riddle_list", description="List all currently active riddles.")
    async def riddle_list(interaction: discord.Interaction):
        if not active_riddles:
            await interaction.response.send_message("📭 There are no open riddles.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📋 Active Riddles",
            color=discord.Color.orange()
        )
        for rid, r in active_riddles.items():
            remaining = (datetime.fromisoformat(r.created_at) + timedelta(days=r.length)) - datetime.utcnow()
            embed.add_field(
                name=rid,
                value=f"Ends in: {remaining.days}d {remaining.seconds//3600}h\nBy: <@{r.author_id}>",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tasks.loop(minutes=5)
    async def check_expired_riddles():
        to_remove = [rid for rid, r in active_riddles.items() if r.is_expired()]
        for rid in to_remove:
            await post_solution(bot, active_riddles[rid])
            del active_riddles[rid]
        if to_remove:
            save_riddles()

    check_expired_riddles.start()

    @riddle_add.error
    @riddle_win.error
    async def on_permission_error(interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingRole):
            await interaction.response.send_message("🚫 You don't have permission to use this command.", ephemeral=True)

# --------- Views ----------
class RiddleSolveView(discord.ui.View):
    def __init__(self, riddle_id, riddle_text, author_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.riddle_text = riddle_text
        self.author_id = author_id

    @discord.ui.button(label="🧠 Submit Solution", style=discord.ButtonStyle.primary, custom_id="riddle_submit_button")
    async def solution_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RiddleSolutionModal(self.riddle_id, self.riddle_text, self.author_id)
        await interaction.response.send_modal(modal)

class RiddleSolutionModal(discord.ui.Modal, title="💡 Submit Your Riddle Answer"):
    answer = discord.ui.TextInput(label="Your Answer", placeholder="Type your solution...", max_length=200)

    def __init__(self, riddle_id, riddle_text, author_id):
        super().__init__()
        self.riddle_id = riddle_id
        self.riddle_text = riddle_text
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        author_user = await interaction.client.fetch_user(self.author_id)
        try:
            await author_user.send(
                embed=discord.Embed(
                    title=f"📩 New Riddle Answer from {interaction.user}",
                    description=f"**Riddle ID:** {self.riddle_id}\n\n> {self.riddle_text}\n\n**Answer:** {self.answer.value}",
                    color=discord.Color.gold()
                ).set_thumbnail(url=interaction.user.avatar.url)
            )
        except:
            pass
        await interaction.response.send_message("✅ Your answer has been submitted!", ephemeral=True)

# --------- Solution Post ----------
async def post_solution(bot, riddle: RiddleData):
    embed = discord.Embed(
        title="✅ Riddle Solved!",
        description=f"**Riddle:**\n> {riddle.question}\n\n**Solution:** `{riddle.solution}`",
        color=discord.Color.green()
    )
    embed.set_image(url=riddle.solution_img)
    if riddle.winner_id:
        winner = await bot.fetch_user(riddle.winner_id)
        embed.add_field(name="🏆 Winner", value=winner.mention, inline=False)
        embed.set_thumbnail(url=winner.avatar.url)
    embed.set_footer(text=f"Riddle ID: {riddle.id} • Well played.")
    channel = await bot.fetch_channel(SOLUTION_CHANNEL_ID)
    await channel.send(" ".join(riddle.mentions), embed=embed)
