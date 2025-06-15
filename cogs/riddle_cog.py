# riddle_cog.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timedelta
from .riddle_views import SubmitSolutionView
from .riddle_utils import load_riddles, save_riddles, generate_riddle_id

RIDDLE_DB_PATH = 'riddles.json'
USER_STATS_PATH = 'user_stats.json'

RIDDLE_GROUP_ID = 1380610400416043089
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"

class Riddle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.riddles = load_riddles(RIDDLE_DB_PATH)
        self.user_stats = load_riddles(USER_STATS_PATH)

    async def setup_persistent_views(self):
        for riddle_id, data in self.riddles.items():
            view = SubmitSolutionView(riddle_id=riddle_id, bot=self.bot)
            self.bot.add_view(view)

    @app_commands.command(name="riddle_add", description="Add a new riddle (Admins only)")
    @app_commands.describe(
        text="Riddle text",
        solution="Solution to the riddle",
        channel_name="Channel to post the riddle",
        image_url="Optional image URL",
        mention_group1="Optional mention 1",
        mention_group2="Optional mention 2",
        solution_image="Optional image for the solution reveal",
        length="Duration in days",
        award="Optional award text or emoji"
    )
    async def riddle_add(self, interaction: discord.Interaction, text: str, solution: str, channel_name: str,
                         image_url: str = None, mention_group1: discord.Role = None,
                         mention_group2: discord.Role = None, solution_image: str = None,
                         length: int = 1, award: str = None):

        # Permissions
        if RIDDLE_GROUP_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("You do not have permission to add riddles.", ephemeral=True)
            return

        # Find channel
        channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
        if not channel:
            await interaction.response.send_message(f"Channel '{channel_name}' not found.", ephemeral=True)
            return

        riddle_id = generate_riddle_id()
        end_time = datetime.utcnow() + timedelta(days=length)
        image_url = image_url or DEFAULT_IMAGE_URL

        embed = discord.Embed(
            title=f"Goon Hut Riddle (Created: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})",
            description=text.replace("\\n", "\n"),
            color=discord.Color.blurple()
        )
        embed.set_image(url=image_url)
        embed.set_footer(text=f"By {interaction.user.display_name} | Ends in {length} day(s) | {interaction.guild.name}",
                         icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else None)

        mention_string = f"<@&{RIDDLE_GROUP_ID}>"
        if mention_group1:
            mention_string += f" <@&{mention_group1.id}>"
        if mention_group2:
            mention_string += f" <@&{mention_group2.id}>"

        view = SubmitSolutionView(riddle_id=riddle_id, bot=self.bot)
        msg = await channel.send(content=mention_string, embed=embed, view=view)

        self.riddles[riddle_id] = {
            "text": text,
            "solution": solution.lower(),
            "channel_id": channel.id,
            "message_id": msg.id,
            "author_id": interaction.user.id,
            "image_url": image_url,
            "solution_image": solution_image,
            "created_at": datetime.utcnow().isoformat(),
            "end_time": end_time.isoformat(),
            "award": award,
            "mentions": [mention_group1.id if mention_group1 else None,
                         mention_group2.id if mention_group2 else None],
            "active": True
        }

        save_riddles(RIDDLE_DB_PATH, self.riddles)
        self.user_stats[str(interaction.user.id)] = self.user_stats.get(str(interaction.user.id), {"submitted": 0, "solved": 0})
        self.user_stats[str(interaction.user.id)]["submitted"] += 1
        save_riddles(USER_STATS_PATH, self.user_stats)

        await interaction.response.send_message(f"Riddle posted successfully with ID `{riddle_id}`.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Riddle(bot))
    await bot.wait_until_ready()
    await bot.get_cog("Riddle").setup_persistent_views()
