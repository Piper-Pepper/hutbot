import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import string
import datetime

# IDs – anpassen!
LOG_CHANNEL_ID = 1376988186865172572
ARCHIVE_CHANNEL_ID = 234567890123456789
MENTION1_ID = 1380610400416043089
MENTION2_ID = 1346428405368750122

# Speicher
active_riddles = {}

def generate_riddle_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

class SolutionModal(discord.ui.Modal, title="Submit your solution"):
    def __init__(self, riddle_id: str, riddle_data: dict):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.riddle_data = riddle_data
        self.answer = discord.ui.TextInput(label="Your answer", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        creator = self.riddle_data['creator']
        try:
            await creator.send(embed=discord.Embed(
                title=f"🧠 New Solution for Riddle {self.riddle_id}",
                description=f"**From:** {interaction.user.mention} ({interaction.user.id})\n**Answer:**\n{self.answer.value}",
                color=discord.Color.blurple()
            ).set_footer(text="Sent by the GoonHut Riddle System"))
            await interaction.response.send_message("✅ Your answer has been sent!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Could not DM the riddle creator.", ephemeral=True)

class SolutionButton(discord.ui.Button):
    def __init__(self, riddle_id: str):
        super().__init__(label="🧩 Submit Solution", style=discord.ButtonStyle.primary, custom_id=f"riddle_solution_{riddle_id}")
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        riddle_data = active_riddles.get(self.riddle_id)
        if not riddle_data:
            await interaction.response.send_message("❌ This riddle has ended.", ephemeral=True)
            return
        await interaction.response.send_modal(SolutionModal(self.riddle_id, riddle_data))

class RiddleView(discord.ui.View):
    def __init__(self, riddle_id: str):
        super().__init__(timeout=None)
        self.add_item(SolutionButton(riddle_id))

class Riddle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_expired_riddles.start()

    def cog_unload(self):
        self.check_expired_riddles.cancel()

    @tasks.loop(minutes=1)
    async def check_expired_riddles(self):
        now = datetime.datetime.utcnow()
        expired = [rid for rid, data in active_riddles.items() if data["end_time"] <= now]
        for rid in expired:
            riddle = active_riddles.pop(rid)
            embed = discord.Embed(
                title="⌛ Riddle Expired",
                description=(
                    f"**ID:** `{rid}`\n\n📅 Time is up! This riddle ended with no winner.\n\n"
                    f"**Riddle:** {riddle['text']}\n**Solution:** {riddle['solution']}"
                ),
                color=discord.Color.red()
            )
            if riddle['solution_image']:
                embed.set_image(url=riddle['solution_image'])

            await riddle["channel"].send(embed=embed)
            archive_channel = self.bot.get_channel(ARCHIVE_CHANNEL_ID)
            if archive_channel:
                await archive_channel.send(embed=embed)
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"⌛ Riddle `{rid}` expired.")

    @app_commands.command(name="riddle_add", description="Post a new riddle!")
    async def riddle_add(self, interaction: discord.Interaction,
                         riddle_text: str,
                         solution: str,
                         image_url: str = None,
                         solution_image_url: str = None,
                         mention_group: discord.Role = None,
                         duration_days: int = 1):
        riddle_id = generate_riddle_id()
        now = datetime.datetime.utcnow()
        end_time = now + datetime.timedelta(days=duration_days)

        mentions = f"<@&{MENTION1_ID}> <@&{MENTION2_ID}>"
        if mention_group:
            mentions += f" {mention_group.mention}"

        embed = discord.Embed(
            title="🧠 Riddle of the Day",
            description=(
                f"**ID:** `{riddle_id}`\n\n{riddle_text}\n\n"
                f"**By:** {interaction.user.mention}\n⏳ *Ends in {duration_days} day(s)*"
            ),
            color=discord.Color.gold(),
            timestamp=now
        )
        embed.set_footer(text="Solve it before time runs out!")
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else discord.Embed.Empty)
        if image_url:
            embed.set_image(url=image_url)

        view = RiddleView(riddle_id)
        message = await interaction.channel.send(content=mentions, embed=embed, view=view)

        active_riddles[riddle_id] = {
            "id": riddle_id,
            "creator": interaction.user,
            "text": riddle_text,
            "solution": solution,
            "solution_image": solution_image_url,
            "message": message,
            "channel": interaction.channel,
            "end_time": end_time,
        }

        await interaction.response.send_message(f"✅ Riddle `{riddle_id}` posted!", ephemeral=True)

        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"🧩 New riddle `{riddle_id}` created by {interaction.user.mention}")
    @app_commands.command(name="riddle_win", description="Mark a riddle as solved by someone.")
    async def riddle_win(self, interaction: discord.Interaction,
                         riddle_id: str,
                         winner: discord.Member):
        riddle = active_riddles.pop(riddle_id, None)
        if not riddle:
            await interaction.response.send_message("❌ Riddle ID not found or already closed.", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ Riddle Solved!",
            description=(
                f"**ID:** `{riddle_id}`\n🎉 {winner.mention} solved the riddle!\n\n"
                f"**Riddle:** {riddle['text']}\n**Solution:** {riddle['solution']}"
            ),
            color=discord.Color.green()
        )
        if riddle["solution_image"]:
            embed.set_image(url=riddle["solution_image"])

        await riddle["channel"].send(embed=embed)

        archive_channel = self.bot.get_channel(ARCHIVE_CHANNEL_ID)
        if archive_channel:
            await archive_channel.send(embed=embed)

        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"🏁 Riddle `{riddle_id}` solved by {winner.mention}")

        await interaction.response.send_message(f"✅ Marked riddle `{riddle_id}` as solved by {winner.mention}.", ephemeral=True)

    @app_commands.command(name="riddle_end", description="End a riddle without a winner.")
    async def riddle_end(self, interaction: discord.Interaction, riddle_id: str):
        riddle = active_riddles.pop(riddle_id, None)
        if not riddle:
            await interaction.response.send_message("❌ Riddle ID not found or already closed.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🛑 Riddle Ended",
            description=(
                f"**ID:** `{riddle_id}`\n\nThis riddle has been closed manually.\n\n"
                f"**Riddle:** {riddle['text']}\n**Solution:** {riddle['solution']}"
            ),
            color=discord.Color.red()
        )
        if riddle["solution_image"]:
            embed.set_image(url=riddle["solution_image"])

        await riddle["channel"].send(embed=embed)

        archive_channel = self.bot.get_channel(ARCHIVE_CHANNEL_ID)
        if archive_channel:
            await archive_channel.send(embed=embed)

        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"🛑 Riddle `{riddle_id}` ended by {interaction.user.mention}")

        await interaction.response.send_message(f"✅ Riddle `{riddle_id}` has been closed.", ephemeral=True)

    @app_commands.command(name="riddle_list", description="List all active riddles.")
    async def riddle_list(self, interaction: discord.Interaction):
        if not active_riddles:
            await interaction.response.send_message("📭 There are currently no active riddles.", ephemeral=True)
            return

        embed = discord.Embed(title="📋 Active Riddles", color=discord.Color.blurple())
        for rid, data in active_riddles.items():
            remaining = data["end_time"] - datetime.datetime.utcnow()
            hours = int(remaining.total_seconds() // 3600)
            embed.add_field(
                name=f"🧩 ID: `{rid}`",
                value=f"**By:** {data['creator'].mention}\n**Ends in:** {hours}h\n**Text:** {data['text'][:80]}...",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    cog = Riddle(bot)
    await bot.add_cog(cog)

    # Buttons persist across restarts
    for riddle_id in active_riddles:
        bot.add_view(RiddleView(riddle_id))
