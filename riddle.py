import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import json
import random
import string
import os

RIDDLE_ROLE_ID = 1380610400416043089
MENTION_ROLE_1 = 1380610400416043089
MENTION_ROLE_2 = ""
RIDDLE_CHANNEL_ID = 1346843244067160074
SOLUTION_CHANNEL_ID = 1381754826710585527
LOG_CHANNEL_ID = 1346843244067160074
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1381375333491675217/idcard_small.png"
RIDDLE_FILE = "riddles.json"


def save_riddles(data):
    with open(RIDDLE_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_riddles():
    if not os.path.exists(RIDDLE_FILE):
        return {}
    with open(RIDDLE_FILE, "r") as f:
        return json.load(f)

class SolutionDMView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success, custom_id="dm_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.edit(content="✅ You accepted the solution.", view=None)

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger, custom_id="dm_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.edit(content="❌ You rejected the solution.", view=None)

class SolutionChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success, custom_id="channel_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.edit(content="✅ Accepted.", view=None)

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger, custom_id="channel_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.edit(content="❌ Rejected.", view=None)

class RiddleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💡 Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SolutionModal(riddle_id=interaction.message.embeds[0].footer.text))

class SolutionModal(discord.ui.Modal, title="Submit Your Solution"):
    answer = discord.ui.TextInput(label="Your Answer", style=discord.TextStyle.paragraph)

    def __init__(self, riddle_id):
        super().__init__()
        self.riddle_id = riddle_id

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        riddles = load_riddles()
        riddle = riddles.get(self.riddle_id)
        if not riddle:
            await interaction.response.send_message("Riddle not found.", ephemeral=True)
            return

        embed = discord.Embed(title=f"🧩 Riddle #{self.riddle_id}", color=discord.Color.orange(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Riddle", value=riddle['text'], inline=False)
        embed.add_field(name="Submitted Answer", value=self.answer.value, inline=False)
        embed.add_field(name="From", value=f"{interaction.user.mention} ({interaction.user})", inline=False)

        view_dm = SolutionDMView()
        view_channel = SolutionChannelView()

        # Send DM to riddle creator
        author = await bot.fetch_user(riddle['author_id'])
        await author.send("🧠 New solution received!", embed=embed, view=view_dm)

        # Also post to solution channel
        channel = bot.get_channel(SOLUTION_CHANNEL_ID)
        await channel.send("🧠 New solution received!", embed=embed, view=view_channel)

        await interaction.response.send_message("✅ Your solution was sent!", ephemeral=True)

class Riddle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.riddles = load_riddles()
        self.expire_riddles.start()

    def generate_id(self):
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    @tasks.loop(minutes=1)
    async def expire_riddles(self):
        now = datetime.datetime.utcnow()
        expired = []
        for riddle_id, data in list(self.riddles.items()):
            if now >= datetime.datetime.fromisoformat(data['end_time']):
                expired.append((riddle_id, data))

        for riddle_id, data in expired:
            try:
                channel = self.bot.get_channel(data['channel_id'])
                msg = await channel.fetch_message(data['message_id'])
                await msg.delete()
            except:
                pass
            del self.riddles[riddle_id]
        if expired:
            save_riddles(self.riddles)

    @app_commands.command(name="riddle_add", description="Create a new riddle")
    @app_commands.checks.has_role(RIDDLE_ROLE_ID)
    async def riddle_add(self, interaction: discord.Interaction, text: str, solution: str, 
                         image_url: str = None, solution_image: str = None, 
                         days: int = 1):
        riddle_id = self.generate_id()
        end_time = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat()

        embed = discord.Embed(title="🌟 Riddle of the Day", description=text, color=discord.Color.gold(), timestamp=interaction.created_at)
        embed.set_footer(text=riddle_id)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar)
        embed.set_image(url=image_url or DEFAULT_IMAGE_URL)

        mentions = f"<@&{MENTION_ROLE_1}> <@&{MENTION_ROLE_2}>"

        view = RiddleView()
        channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
        message = await channel.send(content=mentions, embed=embed, view=view)

        self.riddles[riddle_id] = {
            "text": text,
            "solution": solution,
            "image_url": image_url,
            "solution_image": solution_image,
            "author_id": interaction.user.id,
            "channel_id": channel.id,
            "message_id": message.id,
            "end_time": end_time
        }
        save_riddles(self.riddles)

        await interaction.response.send_message(f"✅ Riddle `{riddle_id}` posted!", ephemeral=True)

    @app_commands.command(name="riddle_win", description="Close a riddle and announce the winner")
    @app_commands.checks.has_role(RIDDLE_ROLE_ID)
    @app_commands.describe(riddle_id="ID of the riddle", winner="Winner of the riddle")
    async def riddle_win(self, interaction: discord.Interaction, riddle_id: str, winner: discord.Member):
        riddle = self.riddles.get(riddle_id)
        if not riddle:
            await interaction.response.send_message("Riddle not found.", ephemeral=True)
            return

        channel = self.bot.get_channel(riddle["channel_id"])
        try:
            msg = await channel.fetch_message(riddle["message_id"])
            await msg.delete()
        except:
            pass

        embed = discord.Embed(
            title="🏆 Riddle Solved!",
            color=discord.Color.green(),
            timestamp=interaction.created_at
        )

        if riddle.get("solution_image"):
            embed.set_image(url=riddle["solution_image"])
        elif riddle.get("image_url"):
            embed.set_image(url=riddle["image_url"])

        embed.set_author(name=winner.display_name, icon_url=winner.display_avatar)
        embed.add_field(name="🧠 Riddle", value=riddle["text"], inline=False)
        embed.add_field(name="✅ Correct Answer", value=riddle["solution"], inline=False)
        embed.add_field(name="🏅 Winner", value=winner.mention, inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon)

        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(embed=embed)

        del self.riddles[riddle_id]
        save_riddles(self.riddles)

        await interaction.response.send_message(f"Riddle `{riddle_id}` marked as solved and announced.", ephemeral=True)

    @app_commands.command(name="riddle_list", description="List all active riddles")
    @app_commands.checks.has_role(RIDDLE_ROLE_ID)
    async def riddle_list(self, interaction: discord.Interaction):
        if not self.riddles:
            await interaction.response.send_message("There are no active riddles.", ephemeral=True)
            return

        embed = discord.Embed(title="📜 Active Riddles", color=discord.Color.teal())
        for riddle_id, data in self.riddles.items():
            remaining = datetime.datetime.fromisoformat(data['end_time']) - datetime.datetime.utcnow()
            mins = int(remaining.total_seconds() / 60)
        embed.add_field(
            name=f"🧩 ID: {riddle_id}",
            value=(
                f"Author: <@{data['author_id']}>\n"
                f"Ends in: {mins} mins\n"
                f"Text: {data['text'][:50]}..."
            ),
            inline=False
        )


        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Riddle(bot))