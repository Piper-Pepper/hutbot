# riddle.py (adapted for new views & /riddle stats)

import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
from datetime import datetime, timedelta
from riddle_views import SubmitSolutionView, CreatorDMView, ModerationView, RiddleListView, RiddleOptionsView

RIDDLES_FILE = "riddles.json"
USER_STATS_FILE = "user_stats.json"
LOG_CHANNEL_ID = 1381754826710585527
RIDDLE_ADD_PERMISSION_ROLE_ID = 1380610400416043089
DEFAULT_RIDDLE_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"

def load_json(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

class Riddle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.riddles = load_json(RIDDLES_FILE)
        self.user_stats = load_json(USER_STATS_FILE)
        self.check_riddle_timeouts.start()

    async def setup_persistent_views(self):
        for riddle_id, riddle_data in self.riddles.items():
            if not riddle_data.get("closed", False):
                self.bot.add_view(SubmitSolutionView(riddle_id))
            if riddle_data.get("creator_dm_message_id"):
                self.bot.add_view(CreatorDMView(riddle_id))
            if riddle_data.get("log_message_id"):
                self.bot.add_view(ModerationView(riddle_id))

    @commands.Cog.listener()
    async def on_ready(self):
        await self.setup_persistent_views()
        print(f"{self.__class__.__name__} ready with {len(self.riddles)} active riddles.")

    @tasks.loop(minutes=1)
    async def check_riddle_timeouts(self):
        now = datetime.utcnow()
        for riddle_id, riddle in list(self.riddles.items()):
            if not riddle.get("closed", False) and now > datetime.fromisoformat(riddle["close_at"]):
                await self.close_riddle(riddle_id)

    @app_commands.command(name="riddle_stats", description="Shows your or another user's riddle progress")
    async def riddle_stats(self, interaction: discord.Interaction, user: discord.User = None):
        target_user = user or interaction.user
        stats = self.user_stats.get(str(target_user.id), {"submitted": 0, "solved": 0})
        await interaction.response.send_message(ephemeral=True, view=StatsView(target_user, stats))

    @app_commands.command(name="riddle_add", description="Add a new riddle")
    @app_commands.checks.has_role(RIDDLE_ADD_PERMISSION_ROLE_ID)
    @app_commands.describe(channel="Channel to post the riddle embed")
    async def riddle_add(self, interaction: discord.Interaction, text: str, solution: str, channel: discord.TextChannel,
                        image_url: str = None, mention_group1: discord.Role | discord.User = None, mention_group2: discord.Role | discord.User = None,
                        solution_image: str = None, length: int = 1, award: str = None):

        riddle_id = str(int(datetime.utcnow().timestamp() * 1000))
        image_url = image_url or DEFAULT_RIDDLE_IMAGE
        solution_image = solution_image or DEFAULT_RIDDLE_IMAGE

        mentions = [f"<@&{RIDDLE_ADD_PERMISSION_ROLE_ID}>"]
        if mention_group1:
            mentions.append(mention_group1.mention)
        if mention_group2:
            mentions.append(mention_group2.mention)
        mentions_text = " ".join(mentions)

        created_at = datetime.utcnow()
        close_at = created_at + timedelta(days=length)
        riddle_id_display = f"#{riddle_id}"  # or just str(riddle_id), depending on format

        embed = discord.Embed(
            title=f"🧠 Goon Hut Riddle {riddle_id_display} (Created: {created_at.strftime('%Y-%m-%d %H:%M UTC')})",
            description=text.replace("\\n", "\n"),
            color=discord.Color.blue(),
            timestamp=created_at
        )
        avatar_url = interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
        embed.set_thumbnail(url=avatar_url)
        embed.set_image(url=image_url if image_url else DEFAULT_RIDDLE_IMAGE)
        embed.set_footer(text=f"Created by {interaction.user.display_name} | Closes in {length} day(s)")

        if award:
            embed.add_field(name="🏆 Award", value=award, inline=False)

        view = SubmitSolutionView(riddle_id)
        message = await channel.send(content=mentions_text, embed=embed, view=view)
        self.bot.add_view(view)

        self.riddles[riddle_id] = {
            "id": riddle_id,
            "text": text,
            "solution": solution.lower().strip(),
            "creator_id": interaction.user.id,
            "channel_id": channel.id,
            "message_id": message.id,
            "created_at": created_at.isoformat(),
            "close_at": close_at.isoformat(),
            "image_url": image_url,
            "solution_image": solution_image,
            "award": award,
            "mention_group1_id": getattr(mention_group1, "id", None),
            "mention_group2_id": getattr(mention_group2, "id", None),
            "closed": False,
            "creator_dm_message_id": None,
            "log_message_id": None
        }
        save_json(RIDDLES_FILE, self.riddles)

        await interaction.response.send_message(f"✅ Riddle created with ID `{riddle_id}` and posted in {channel.mention}.", ephemeral=True)

    @app_commands.command(name="riddle_list", description="List open riddles with a select menu")
    async def riddle_list(self, interaction: discord.Interaction):
        open_riddles = {k: v for k, v in self.riddles.items() if not v.get("closed", False)}
        if not open_riddles:
            await interaction.response.send_message("No open riddles available.", ephemeral=True)
            return

        view = RiddleListView(open_riddles, self.bot)  # pass bot here
        await interaction.response.send_message("Here are the open riddles:", view=view, ephemeral=True)


    async def close_riddle(self, riddle_id: str, winner: discord.User = None, submitted_solution: str = None):
        riddle = self.riddles.get(riddle_id)
        if not riddle or riddle.get("closed", False):
            return  # Riddle does not exist or is already closed

        # Mark riddle as closed
        riddle["closed"] = True

        # If winner exists, increase stats
        if winner:
            user_id = str(winner.id)
            stats = self.user_stats.get(user_id, {"submitted": 0, "solved": 0})
            stats["solved"] += 1
            self.user_stats[user_id] = stats
            save_json(USER_STATS_FILE, self.user_stats)

        save_json(RIDDLES_FILE, self.riddles)

        # Fetch channel and send closing embed
        channel = self.bot.get_channel(riddle["channel_id"])
        if channel is None:
            print(f"Channel {riddle['channel_id']} not found.")
            return
        image_url = image_url or DEFAULT_RIDDLE_IMAGE
        embed = discord.Embed(
            title=f"🎉 Riddle {riddle_id} closed! 🎉",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        
        embed.set_image(url=image_url)
        if winner:
            embed.add_field(name="🏆 Winner", value=f"{winner.mention} (ID: {winner.id})", inline=True)
            embed.set_thumbnail(url=winner.avatar.url if winner.avatar else winner.default_avatar.url)
            embed.description = (
                f"**Riddle:**\n{riddle['text']}\n\n"
                f"**Submitted Solution:** {submitted_solution or 'No submission provided'}\n"
                f"**Preset Solution:** ||{riddle['solution']}||"
            )
            # Show solution image prominently: custom if set, else default
            embed.set_image(url=riddle.get("solution_image", DEFAULT_RIDDLE_IMAGE))
        else:
            embed.description = f"The riddle was closed without a winner.\n\n**Riddle:**\n{riddle['text']}"

        if riddle.get("award"):
            embed.add_field(name="🏆 Award", value=riddle["award"], inline=False)

        await channel.send(embed=embed)

    async def delete_riddle(self, riddle_id: str):
        # ... remains unchanged
        pass

    @riddle_add.error
    async def riddle_add_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingRole):
            await interaction.response.send_message("❌ You don't have permission to add riddles.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Riddle(bot))
    bot.add_view(RiddleOptionsView("dummy"))  # dummy ID, will not be clicked
