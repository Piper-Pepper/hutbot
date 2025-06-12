import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import uuid
from datetime import datetime, timedelta

# --- Konstante IDs und Bilder ---
ALLOWED_ROLE_ID = 1380610400416043089
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"
RIDDLES_FILE = "riddles.json"

# --- Hilfsfunktionen ---
def load_riddles():
    if not os.path.exists(RIDDLES_FILE):
        return {}
    with open(RIDDLES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_riddles(riddles):
    with open(RIDDLES_FILE, "w", encoding="utf-8") as f:
        json.dump(riddles, f, indent=4)

def format_newlines(text):
    return text.replace("\\n", "\n")

# --- Hauptklasse ---
class RiddleSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.riddles = load_riddles()
        self.check_riddles.start()

    def cog_unload(self):
        self.check_riddles.cancel()

    @tasks.loop(minutes=1)
    async def check_riddles(self):
        now = datetime.utcnow().timestamp()
        to_close = [rid for rid, data in self.riddles.items() if now >= data["end_time"]]
        for rid in to_close:
            await self.close_riddle(rid)

    async def close_riddle(self, riddle_id, winner=None):
        data = self.riddles.get(riddle_id)
        if not data:
            return

        channel = self.bot.get_channel(data["channel_id"])
        mentions = f"<@&{ALLOWED_ROLE_ID}>"
        if data.get("mention1"):
            mentions += f" <@{data['mention1']}>"
        if data.get("mention2"):
            mentions += f" <@{data['mention2']}>"

        image_url = data.get("solution_image") or DEFAULT_IMAGE_URL

        embed = discord.Embed(
            title="Riddle Solution",
            description=f"**Riddle:**\n{data['riddle']}\n\n**Solution:**",
            color=discord.Color.green()
        )
        embed.set_image(url=image_url)
        if winner:
            user = await self.bot.fetch_user(winner)
            embed.add_field(name="Winner", value=f"{user.mention}", inline=False)
            embed.set_thumbnail(url=user.display_avatar.url)
        else:
            embed.add_field(name="Winner", value="No winner", inline=False)

        await channel.send(content=mentions, embed=embed)

        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        await log_channel.send(embed=embed)

        # Lösche Riddle aus JSON
        del self.riddles[riddle_id]
        save_riddles(self.riddles)

    # --- /riddle add ---
    @app_commands.command(name="riddle_add", description="Create a new riddle.")
    @app_commands.describe(
        riddle="The riddle text",
        solution="The correct answer",
        channel="Channel to post the riddle in",
        image_url="Optional image URL",
        mention_group1="Optional mention (role or user)",
        mention_group2="Optional mention (role or user)",
        solution_image="Image to show with the solution",
        length="How long the riddle is open (in days)"
    )
    async def riddle_add(self, interaction: discord.Interaction,
                         riddle: str,
                         solution: str,
                         channel: discord.TextChannel,
                         image_url: str = None,
                         mention_group1: discord.User = None,
                         mention_group2: discord.User = None,
                         solution_image: str = None,
                         length: int = 1):

        if not any(role.id == ALLOWED_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        riddle_id = str(uuid.uuid4())[:8]
        now = datetime.utcnow()
        end_time = now + timedelta(days=length)

        formatted_riddle = format_newlines(riddle)

        embed = discord.Embed(
            title=f"Riddle of the Day ({now.strftime('%Y-%m-%d %H:%M UTC')})",
            description=formatted_riddle,
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"This riddle closes at {end_time.strftime('%Y-%m-%d %H:%M UTC')}")
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_image(url=image_url or DEFAULT_IMAGE_URL)

        view = RiddleView(riddle_id, interaction.user.id)
        mention_text = f"<@&{ALLOWED_ROLE_ID}>"
        if mention_group1:
            mention_text += f" <@{mention_group1.id}>"
        if mention_group2:
            mention_text += f" <@{mention_group2.id}>"

        msg = await channel.send(content=mention_text, embed=embed, view=view)

        # Speichern
        self.riddles[riddle_id] = {
            "riddle": formatted_riddle,
            "solution": solution.lower(),
            "author_id": interaction.user.id,
            "channel_id": channel.id,
            "message_id": msg.id,
            "image_url": image_url,
            "mention1": mention_group1.id if mention_group1 else None,
            "mention2": mention_group2.id if mention_group2 else None,
            "solution_image": solution_image,
            "end_time": end_time.timestamp()
        }
        save_riddles(self.riddles)

        await interaction.response.send_message(f"Riddle `{riddle_id}` created successfully!", ephemeral=True)

    # --- /riddle list ---
    @app_commands.command(name="riddle_list", description="Manage all open riddles.")
    async def riddle_list(self, interaction: discord.Interaction):
        if not self.riddles:
            await interaction.response.send_message("There are no open riddles.", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=f"{rid} by {self.bot.get_user(data['author_id'])}", value=rid)
            for rid, data in self.riddles.items()
        ]

        view = RiddleListView(self, interaction.user.id, options)
        await interaction.response.send_message("Choose a riddle to manage:", view=view, ephemeral=True)

# --- View für Submit ---
class RiddleView(discord.ui.View):
    def __init__(self, riddle_id, author_id):
        super().__init__(timeout=None)
        self.add_item(SubmitSolutionButton(riddle_id, author_id))

class SubmitSolutionButton(discord.ui.Button):
    def __init__(self, riddle_id, author_id):
        super().__init__(label="Submit Solution", style=discord.ButtonStyle.primary, emoji="🧠")
        self.riddle_id = riddle_id
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SolutionModal(self.riddle_id, self.author_id, interaction.user))

class SolutionModal(discord.ui.Modal, title="Submit Your Solution"):
    answer = discord.ui.TextInput(label="Your Answer", style=discord.TextStyle.paragraph)

    def __init__(self, riddle_id, author_id, solver):
        super().__init__()
        self.riddle_id = riddle_id
        self.author_id = author_id
        self.solver = solver

    async def on_submit(self, interaction: discord.Interaction):
        user = await interaction.client.fetch_user(self.author_id)
        embed = discord.Embed(
            title=f"Solution attempt for riddle {self.riddle_id}",
            description=f"**Riddle:**\n{interaction.client.get_cog('RiddleSystem').riddles[self.riddle_id]['riddle']}\n\n**Attempted Answer:**\n{self.answer.value}",
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"From: {self.solver.display_name}")
        embed.set_thumbnail(url=self.solver.display_avatar.url)

        view = ReviewSolutionView(self.riddle_id, self.solver.id)

        await user.send(embed=embed, view=view)
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        await log_channel.send(embed=embed, view=view)
        await interaction.response.send_message("Your solution was submitted!", ephemeral=True)

class ReviewSolutionView(discord.ui.View):
    def __init__(self, riddle_id, solver_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.solver_id = solver_id
        self.add_item(ReviewButton("✅", True, riddle_id, solver_id))
        self.add_item(ReviewButton("❌", False, riddle_id, solver_id))

class ReviewButton(discord.ui.Button):
    def __init__(self, emoji, approve, riddle_id, solver_id):
        label = "Correct" if approve else "Incorrect"
        super().__init__(style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger, emoji=emoji)
        self.approve = approve
        self.riddle_id = riddle_id
        self.solver_id = solver_id

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RiddleSystem")
        if self.approve:
            await cog.close_riddle(self.riddle_id, winner=self.solver_id)
            await interaction.message.delete()
        else:
            try:
                solver = await interaction.client.fetch_user(self.solver_id)
                await solver.send("Sorry, your solution was not correct!")
            except:
                pass
            await interaction.message.delete()

class RiddleListView(discord.ui.View):
    def __init__(self, cog, admin_id, options):
        super().__init__(timeout=None)
        self.cog = cog
        self.admin_id = admin_id
        self.select = discord.ui.Select(placeholder="Choose a riddle", options=options)
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("You're not allowed to manage this.", ephemeral=True)
            return

        rid = self.select.values[0]
        view = RiddleManageView(self.cog, rid, self.admin_id)
        await interaction.response.send_message(f"Managing riddle {rid}:", view=view, ephemeral=True)

class RiddleManageView(discord.ui.View):
    def __init__(self, cog, riddle_id, admin_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.riddle_id = riddle_id
        self.admin_id = admin_id
        self.add_item(RiddleActionButton("Close with Winner", riddle_id, True))
        self.add_item(RiddleActionButton("Close without Winner", riddle_id, False))
        self.add_item(RiddleDeleteButton("Delete Riddle", riddle_id))

class RiddleActionButton(discord.ui.Button):
    def __init__(self, label, riddle_id, with_winner):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.riddle_id = riddle_id
        self.with_winner = with_winner

    async def callback(self, interaction: discord.Interaction):
        if self.with_winner:
            await interaction.response.send_message("Please enter the winner's @mention or user ID:", ephemeral=True)
        else:
            cog = interaction.client.get_cog("RiddleSystem")
            await cog.close_riddle(self.riddle_id, winner=None)
            await interaction.response.send_message("Riddle closed without a winner.", ephemeral=True)

class RiddleDeleteButton(discord.ui.Button):
    def __init__(self, label, riddle_id):
        super().__init__(label=label, style=discord.ButtonStyle.danger)
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RiddleSystem")
        del cog.riddles[self.riddle_id]
        save_riddles(cog.riddles)
        await interaction.response.send_message("Riddle deleted.", ephemeral=True)

# --- Setup ---
async def setup(bot):
    await bot.add_cog(RiddleSystem(bot))