import discord
from discord.ext import commands
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
        # dict with riddle_id -> riddle data
        self.user_stats = load_json(USER_STATS_FILE)

    async def setup_persistent_views(self):
        # Register Views for all active riddles on startup for persistence
        for riddle_id, riddle_data in self.riddles.items():
            self.bot.add_view(SubmitSolutionView(riddle_id))
            if riddle_data.get("creator_dm_message_id"):
                self.bot.add_view(CreatorDMView(riddle_id))
            if riddle_data.get("log_message_id"):
                self.bot.add_view(ModerationView(riddle_id))

    @commands.Cog.listener()
    async def on_ready(self):
        # Called once when bot is ready
        await self.setup_persistent_views()
        print(f"{self.__class__.__name__} ready with {len(self.riddles)} active riddles.")

    @app_commands.command(name="riddle_add", description="Add a new riddle")
    @app_commands.checks.has_role(RIDDLE_ADD_PERMISSION_ROLE_ID)
    @app_commands.describe(
        channel="Channel to post the riddle embed",  # Channel-Typ = Auswahlfeld
    )
    async def riddle_add(self, interaction: discord.Interaction, text: str, solution: str, channel: discord.TextChannel,
                        image_url: str = None, mention_group1: discord.Role | discord.User = None, mention_group2: discord.Role | discord.User = None,
                        solution_image: str = None, length: int = 1, award: str = None):

        # Generate unique riddle_id
        riddle_id = str(int(datetime.utcnow().timestamp() * 1000))

        # Default images
        image_url = image_url or DEFAULT_RIDDLE_IMAGE
        solution_image = solution_image or DEFAULT_RIDDLE_IMAGE

        # Format mentions
        mentions = [f"<@&{RIDDLE_ADD_PERMISSION_ROLE_ID}>"]
        if mention_group1:
            mentions.append(mention_group1.mention)
        if mention_group2:
            mentions.append(mention_group2.mention)
        mentions_text = " ".join(mentions)

        # Calculate close time
        created_at = datetime.utcnow()
        close_at = created_at + timedelta(days=length)

        # Embed with riddle info
        embed = discord.Embed(
            title=f"Goon Hut Riddle (Created: {created_at.strftime('%Y-%m-%d %H:%M UTC')})",
            description=text.replace("\\n", "\n"),
            color=discord.Color.blue(),
            timestamp=created_at
        )
        avatar_url = interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
        embed.set_thumbnail(url=avatar_url)
        embed.set_footer(text=f"Created by {interaction.user.display_name} | Closes in {length} day(s)")

        if award:
            embed.add_field(name="Award", value=award, inline=False)

        embed.add_field(name="Mentions", value=mentions_text, inline=False)

        # Post embed with submit button
        view = SubmitSolutionView(riddle_id)
        message = await channel.send(content=mentions_text, embed=embed, view=view)

        # Save riddle data
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

        # Add view persistently for the new riddle
        self.bot.add_view(view)

        await interaction.response.send_message(f"✅ Riddle created with ID `{riddle_id}` and posted in {channel.mention}.", ephemeral=True)

    async def close_riddle(self, riddle_id: str, winner: discord.User = None, submitted_solution: str = None):
        """ Close the riddle: update embeds, post solution, update JSON """

        if riddle_id not in self.riddles:
            return  # Already closed or not exist

        riddle = self.riddles[riddle_id]
        if riddle.get("closed", False):
            return  # Already closed

        # Mark closed
        riddle["closed"] = True

        # Fetch channel & message to edit original riddle embed
        channel = self.bot.get_channel(riddle["channel_id"])
        if not channel:
            # channel deleted? just remove from db and return
            del self.riddles[riddle_id]
            save_json(RIDDLES_FILE, self.riddles)
            return

        try:
            message = await channel.fetch_message(riddle["message_id"])
        except discord.NotFound:
            message = None

        # Build solution embed to post
        created_at_dt = datetime.fromisoformat(riddle["created_at"])
        close_at_dt = datetime.utcnow()
        duration = close_at_dt - created_at_dt

        solution_image = riddle.get("solution_image") or DEFAULT_RIDDLE_IMAGE

        winner_text = f"{winner.mention}" if winner else "No winner"
        winner_avatar = winner.avatar.url if (winner and winner.avatar) else None

        embed = discord.Embed(
            title="🏆 Riddle Closed!",
            description=riddle["text"].replace("\\n", "\n"),
            color=discord.Color.green(),
            timestamp=close_at_dt
        )
        embed.add_field(name="Original Solution", value=riddle["solution"], inline=False)
        embed.add_field(name="Submitted Solution", value=submitted_solution or "N/A", inline=False)
        embed.add_field(name="Winner", value=winner_text, inline=False)
        if riddle.get("award"):
            embed.add_field(name="Award", value=riddle["award"], inline=False)
        embed.set_image(url=solution_image)
        embed.set_footer(text=f"Created by <@{riddle['creator_id']}> | Duration: {duration.days} days")
        if winner_avatar:
            embed.set_thumbnail(url=winner_avatar)

        # Edit original riddle message: mark closed, remove button, add closed note
        if message:
            closed_embed = message.embeds[0]
            closed_embed.color = discord.Color.dark_gray()
            closed_embed.title += " [CLOSED]"
            closed_embed.set_footer(text="This riddle is closed.")
            await message.edit(embed=closed_embed, view=None)

        # Post solution embed in original channel
        await channel.send(content=f"<@&{RIDDLE_ADD_PERMISSION_ROLE_ID}>", embed=embed)

        # Remove creator DM and log messages with buttons (if exist)
        creator = await self.bot.fetch_user(riddle["creator_id"])
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)

        # Remove buttons from DM message
        if riddle.get("creator_dm_message_id") and creator:
            try:
                dm_channel = await creator.create_dm()
                dm_msg = await dm_channel.fetch_message(riddle["creator_dm_message_id"])
                await dm_msg.edit(view=None)
            except Exception:
                pass

        # Remove buttons from log message
        if riddle.get("log_message_id") and log_channel:
            try:
                log_msg = await log_channel.fetch_message(riddle["log_message_id"])
                await log_msg.edit(view=None)
            except Exception:
                pass

        # Update user stats for winner (solved count)
        if winner:
            user_id_str = str(winner.id)
            self.user_stats.setdefault(user_id_str, {"solved": 0, "submitted": 0})
            self.user_stats[user_id_str]["solved"] += 1
            save_json(USER_STATS_FILE, self.user_stats)

        # Remove riddle from active riddles
        del self.riddles[riddle_id]
        save_json(RIDDLES_FILE, self.riddles)

    @riddle_add.error
    async def riddle_add_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingRole):
            await interaction.response.send_message("❌ You don't have permission to add riddles.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)
    
    @app_commands.command(name="riddle_list", description="List open riddles with a select menu")
    async def riddle_list(self, interaction: discord.Interaction):
        open_riddles = {k: v for k, v in self.riddles.items() if not v.get("closed", False)}
        if not open_riddles:
            await interaction.response.send_message("Keine offenen Rätsel vorhanden.", ephemeral=True)
            return

        view = RiddleListView(open_riddles)
        await interaction.response.send_message("Hier sind die offenen Rätsel:", view=view, ephemeral=True)
        
async def setup(bot):
    await bot.add_cog(Riddle(bot))
