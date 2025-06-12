import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import uuid
from datetime import datetime, timedelta
import asyncio

# === CONFIG ===
RIDDLE_DATA_FILE = "riddles.json"
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"
MOD_ROLE_ID = 1380610400416043089
SECONDARY_SOLUTION_CHANNEL_ID = 1381754826710585527
MENTION_ROLE_ID = 1380610400416043089

# === JSON HELPERS ===
def load_riddles():
    if not os.path.exists(RIDDLE_DATA_FILE):
        return {}
    with open(RIDDLE_DATA_FILE, "r") as f:
        return json.load(f)

def save_riddles(data):
    with open(RIDDLE_DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# === CLASSES ===
class RiddleButton(discord.ui.View):
    def __init__(self, riddle_id, bot):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.bot = bot

    @discord.ui.button(label="Submit Solution", style=discord.ButtonStyle.primary, emoji="🧠", custom_id="submit_solution")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddles = load_riddles()
        riddle = riddles.get(self.riddle_id)
        if not riddle:
            return await interaction.response.send_message("This riddle no longer exists.", ephemeral=True)

        await interaction.response.send_modal(RiddleModal(self.riddle_id, self.bot))


class RiddleModal(discord.ui.Modal, title="Submit Your Solution"):
    solution = discord.ui.TextInput(label="Your Solution", placeholder="Type your solution here...", style=discord.TextStyle.paragraph)

    def __init__(self, riddle_id, bot):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        riddles = load_riddles()
        riddle = riddles.get(self.riddle_id)
        if not riddle:
            return await interaction.response.send_message("This riddle no longer exists.", ephemeral=True)

        author = self.bot.get_user(riddle["author_id"])
        embed = discord.Embed(title=f"Riddle #{self.riddle_id} Submission", color=discord.Color.green())
        embed.add_field(name="Riddle Text", value=riddle["text"], inline=False)
        embed.add_field(name="Submitted Solution", value=self.solution.value, inline=False)
        embed.set_footer(text=f"From: {interaction.user} ({interaction.user.id})")
        embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else discord.Embed.Empty)

        view = JudgeView(self.riddle_id, self.solution.value, interaction.user, self.bot)
        await author.send(embed=embed, view=view)
        await self.bot.get_channel(SECONDARY_SOLUTION_CHANNEL_ID).send(embed=embed, view=view)

        await interaction.response.send_message("Your solution has been sent to the puzzle creator!", ephemeral=True)


class JudgeView(discord.ui.View):
    def __init__(self, riddle_id, user_solution, submitter, bot):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.solution = user_solution
        self.submitter = submitter
        self.bot = bot

    @discord.ui.button(emoji="👍", style=discord.ButtonStyle.success, custom_id="approve_solution")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddles = load_riddles()
        riddle = riddles.pop(self.riddle_id, None)
        if not riddle:
            return await interaction.response.send_message("This riddle no longer exists.", ephemeral=True)

        embed = discord.Embed(title=f"✅ Riddle Solved!", description=riddle["text"], color=discord.Color.gold())
        embed.set_image(url=riddle.get("solution_image") or DEFAULT_IMAGE_URL)
        embed.add_field(name="Correct Answer", value=riddle["solution"], inline=False)
        embed.add_field(name="Winning Guess", value=self.solution, inline=False)
        embed.set_footer(text=f"Winner: {self.submitter.display_name}")
        embed.set_thumbnail(url=self.submitter.avatar.url if self.submitter.avatar else discord.Embed.Empty)

        channel = self.bot.get_channel(riddle["channel_id"])
        await channel.send(embed=embed)
        await self.bot.get_channel(SECONDARY_SOLUTION_CHANNEL_ID).send(embed=embed)

        try:
            await self.submitter.send("🎉 Your solution was correct! You've solved the riddle!")
        except:
            pass

        # Edit original message if available
        try:
            msg = await channel.fetch_message(riddle["message_id"])
            await msg.edit(view=None)
        except:
            pass

        save_riddles(riddles)
        await interaction.response.send_message("Marked as correct and solution published.", ephemeral=True)

    @discord.ui.button(emoji="👎", style=discord.ButtonStyle.danger, custom_id="deny_solution")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.submitter.send("❌ Sorry, your solution was not correct.")
        except:
            pass
        await interaction.message.delete()
        await interaction.response.send_message("Submission rejected and message deleted.", ephemeral=True)


class Riddle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_expired_riddles.start()

    def cog_unload(self):
        self.check_expired_riddles.cancel()

    @tasks.loop(minutes=1)
    async def check_expired_riddles(self):
        riddles = load_riddles()
        now = datetime.utcnow()
        expired = []

        for rid, data in riddles.items():
            expiry = datetime.fromisoformat(data["expires_at"])
            if now >= expiry:
                expired.append(rid)

        for rid in expired:
            riddle = riddles.pop(rid)
            embed = discord.Embed(title="🕒 Riddle Closed (Expired)", description=riddle["text"], color=discord.Color.red())
            embed.set_image(url=riddle.get("solution_image") or DEFAULT_IMAGE_URL)
            embed.add_field(name="Answer", value=riddle["solution"], inline=False)
            embed.add_field(name="Result", value="No winner", inline=False)
            channel = self.bot.get_channel(riddle["channel_id"])
            await channel.send(embed=embed)
            try:
                msg = await channel.fetch_message(riddle["message_id"])
                await msg.edit(view=None)
            except:
                pass
            await self.bot.get_channel(SECONDARY_SOLUTION_CHANNEL_ID).send(embed=embed)

        save_riddles(riddles)

    @app_commands.command(name="riddle_add", description="Create a new riddle.")
    @app_commands.describe(text="The riddle text", solution="Correct answer", channel="Channel to post", image_url="Optional image", solution_image="Optional solution image", mention_group1="Optional mention 1", mention_group2="Optional mention 2", length="Optional length (days)")
    async def add_riddle(self, interaction: discord.Interaction, text: str, solution: str, channel: discord.TextChannel, image_url: str = None, solution_image: str = None, mention_group1: discord.Role = None, mention_group2: discord.Role = None, length: int = 1):
        if MOD_ROLE_ID not in [role.id for role in interaction.user.roles]:
            return await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)

        rid = str(uuid.uuid4())[:8]
        posted_at = datetime.utcnow()
        expires_at = posted_at + timedelta(days=length)
        embed = discord.Embed(title=f"🧩 Riddle of the Day ({posted_at.strftime('%Y-%m-%d')})", description=text, color=discord.Color.blurple())
        embed.set_image(url=image_url or DEFAULT_IMAGE_URL)
        embed.set_footer(text=f"Created by {interaction.user}", icon_url=interaction.user.avatar.url if interaction.user.avatar else discord.Embed.Empty)

        mention_text = f"<@&{MENTION_ROLE_ID}>"
        if mention_group1:
            mention_text += f" {mention_group1.mention}"
        if mention_group2:
            mention_text += f" {mention_group2.mention}"

        view = RiddleButton(riddle_id=rid, bot=self.bot)
        message = await channel.send(content=mention_text, embed=embed, view=view)

        riddles = load_riddles()
        riddles[rid] = {
            "text": text,
            "solution": solution,
            "author_id": interaction.user.id,
            "channel_id": channel.id,
            "message_id": message.id,
            "image_url": image_url,
            "solution_image": solution_image,
            "expires_at": expires_at.isoformat()
        }
        save_riddles(riddles)

        await interaction.response.send_message(f"✅ Riddle posted with ID: `{rid}`", ephemeral=True)

    @app_commands.command(name="riddle_list", description="Manage existing riddles")
    async def list_riddles(self, interaction: discord.Interaction):
        riddles = load_riddles()
        if not riddles:
            return await interaction.response.send_message("There are no active riddles.", ephemeral=True)

        options = [discord.SelectOption(label=f"Riddle {rid}", description=r["text"][:100], value=rid) for rid, r in riddles.items()]
        select = RiddleSelector(options, self.bot)
        await interaction.response.send_message("Select a riddle to manage:", view=select, ephemeral=True)


class RiddleSelector(discord.ui.View):
    def __init__(self, options, bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(RiddleDropdown(options, bot))


class RiddleDropdown(discord.ui.Select):
    def __init__(self, options, bot):
        super().__init__(placeholder="Choose a riddle...", min_values=1, max_values=1, options=options)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        rid = self.values[0]
        view = RiddleManageOptions(rid, self.bot)
        await interaction.response.send_message(f"Managing Riddle {rid}", view=view, ephemeral=True)


class RiddleManageOptions(discord.ui.View):
    def __init__(self, riddle_id, bot):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.bot = bot

    @discord.ui.button(label="Close with winner", style=discord.ButtonStyle.success)
    async def close_with_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please enter the user ID or mention of the winner:", ephemeral=True)
        msg = await self.bot.wait_for("message", check=lambda m: m.author == interaction.user and m.channel == interaction.channel)
        try:
            winner = await self.bot.fetch_user(int(msg.content.strip("<@!>")))
        except:
            return await interaction.followup.send("Invalid user.", ephemeral=True)

        riddles = load_riddles()
        riddle = riddles.pop(self.riddle_id, None)
        if not riddle:
            return await interaction.followup.send("Riddle not found.", ephemeral=True)

        embed = discord.Embed(title="🏁 Riddle Closed", description=riddle["text"], color=discord.Color.orange())
        embed.set_image(url=riddle.get("solution_image") or DEFAULT_IMAGE_URL)
        embed.add_field(name="Answer", value=riddle["solution"], inline=False)
        embed.add_field(name="Winner", value=winner.mention, inline=False)
        embed.set_thumbnail(url=winner.avatar.url if winner.avatar else discord.Embed.Empty)

        channel = self.bot.get_channel(riddle["channel_id"])
        await channel.send(embed=embed)
        await self.bot.get_channel(SECONDARY_SOLUTION_CHANNEL_ID).send(embed=embed)

        try:
            msg = await channel.fetch_message(riddle["message_id"])
            await msg.edit(view=None)
        except:
            pass

        save_riddles(riddles)

    @discord.ui.button(label="Close without winner", style=discord.ButtonStyle.secondary)
    async def close_no_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddles = load_riddles()
        riddle = riddles.pop(self.riddle_id, None)
        if not riddle:
            return await interaction.response.send_message("Riddle not found.", ephemeral=True)

        embed = discord.Embed(title="📪 Riddle Closed", description=riddle["text"], color=discord.Color.red())
        embed.set_image(url=riddle.get("solution_image") or DEFAULT_IMAGE_URL)
        embed.add_field(name="Answer", value=riddle["solution"], inline=False)
        embed.add_field(name="Winner", value="No winner", inline=False)

        channel = self.bot.get_channel(riddle["channel_id"])
        await channel.send(embed=embed)
        await self.bot.get_channel(SECONDARY_SOLUTION_CHANNEL_ID).send(embed=embed)

        try:
            msg = await channel.fetch_message(riddle["message_id"])
            await msg.edit(view=None)
        except:
            pass

        save_riddles(riddles)
        await interaction.response.send_message("Riddle closed without a winner.", ephemeral=True)

    @discord.ui.button(label="Delete Riddle", style=discord.ButtonStyle.danger)
    async def delete_riddle(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddles = load_riddles()
        riddle = riddles.pop(self.riddle_id, None)
        if not riddle:
            return await interaction.response.send_message("Riddle not found.", ephemeral=True)

        channel = self.bot.get_channel(riddle["channel_id"])
        try:
            msg = await channel.fetch_message(riddle["message_id"])
            await msg.delete()
        except:
            pass

        save_riddles(riddles)
        await interaction.response.send_message("Riddle deleted.", ephemeral=True)

# === SETUP ===
async def setup(bot):
    await bot.add_cog(Riddle(bot))
