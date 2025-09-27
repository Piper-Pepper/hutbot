# cogs/hutvote.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar

# Only this role can use the command
ALLOWED_ROLE = 1346428405368750122

# Category choices
CATEGORY_CHOICES = [
    app_commands.Choice(name="📂 Category 1", value="1416461717038170294"),
    app_commands.Choice(name="📂 Category 2", value="1415769711052062820"),
]

# Year choices: this year and last year
current_year = datetime.utcnow().year
YEAR_CHOICES = [
    app_commands.Choice(name=str(current_year), value=str(current_year)),
    app_commands.Choice(name=str(current_year - 1), value=str(current_year - 1)),
]

# Month choices
MONTH_CHOICES = [
    app_commands.Choice(name="January", value="01"),
    app_commands.Choice(name="February", value="02"),
    app_commands.Choice(name="March", value="03"),
    app_commands.Choice(name="April", value="04"),
    app_commands.Choice(name="May", value="05"),
    app_commands.Choice(name="June", value="06"),
    app_commands.Choice(name="July", value="07"),
    app_commands.Choice(name="August", value="08"),
    app_commands.Choice(name="September", value="09"),
    app_commands.Choice(name="October", value="10"),
    app_commands.Choice(name="November", value="11"),
    app_commands.Choice(name="December", value="12"),
]

# Emoji mapping: Name -> (Custom Emoji String, ID)
EMOJI_MAP = {
    "Great": ("<:01sthumb:1387086056498921614>", 1387086056498921614),
    "Funny": ("<:01smile_piper:1387083454575022213>", 1387083454575022213),
    "No way!": ("<:02No:1347536448831754383>", 1347536448831754383),
    "11": ("<:011:1346549711817146400>", 1346549711817146400),
    "Pump": ("<:011pump:1346549688836296787>", 1346549688836296787),
}
EMOJI_CHOICES = [app_commands.Choice(name=name, value=name) for name in EMOJI_MAP.keys()]


class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="hut_vote",
        description="Shows the top 3 posts for a selected emoji in a given month"
    )
    @app_commands.describe(
        year="Select year",
        month="Select month",
        category="Select category",
        emoji="Select emoji"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        category=CATEGORY_CHOICES,
        emoji=EMOJI_CHOICES
    )
    async def hut_vote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        category: app_commands.Choice[str],
        emoji: app_commands.Choice[str]
    ):
        # Permission check
        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        # Resolve selected emoji
        selected_emoji_str, selected_emoji_id = EMOJI_MAP[emoji.value]

        # Calculate start and end of month
        try:
            start_dt = datetime(int(year.value), int(month.value), 1, tzinfo=timezone.utc)
            last_day = calendar.monthrange(int(year.value), int(month.value))[1]
            end_dt = datetime(int(year.value), int(month.value), last_day, 23, 59, 59, tzinfo=timezone.utc)
        except ValueError:
            await interaction.response.send_message("❌ Invalid year or month.", ephemeral=True)
            return

        guild = interaction.guild
        category_obj = guild.get_channel(int(category.value))
        if not category_obj or not isinstance(category_obj, discord.CategoryChannel):
            await interaction.response.send_message("❌ Invalid category.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # Collect messages with this emoji
        matched_msgs = []

        for channel in category_obj.channels:
            if not isinstance(channel, discord.TextChannel):
                continue

            perms = channel.permissions_for(guild.me)
            if not perms.view_channel or not perms.read_message_history:
                continue

            try:
                async for msg in channel.history(after=start_dt, before=end_dt, limit=None):
                    for reaction in msg.reactions:
                        emoji_id = getattr(reaction.emoji, "id", None)
                        if emoji_id == selected_emoji_id:
                            matched_msgs.append((reaction.count, msg))
            except discord.Forbidden:
                continue
            except Exception:
                continue

        if not matched_msgs:
            await interaction.followup.send(f"No posts found with {selected_emoji_str} in {calendar.month_name[int(month.value)]} {year.value}.")
            return

        # Sort top3 with tiebreaker: sum of other reactions
        def sort_key(item):
            count_selected, msg = item
            other_count = sum(r.count for r in msg.reactions if getattr(r.emoji, "id", None) != selected_emoji_id)
            return (count_selected, other_count, msg.created_at)

        top3 = sorted(matched_msgs, key=sort_key, reverse=True)[:3]

        # Overview embed (emoji in value)
        embed = discord.Embed(
            title=f"Top 3 posts for {selected_emoji_str} in {calendar.month_name[int(month.value)]} {year.value} ({category_obj.name})",
            color=discord.Color.blurple()
        )
        lines = []
        for i, (count, msg) in enumerate(top3, start=1):
            date_str = msg.created_at.strftime("%Y-%m-%d")
            other_count = sum(r.count for r in msg.reactions if getattr(r.emoji, "id", None) != selected_emoji_id)
            lines.append(f"{i}. {selected_emoji_str} — **{count}x** in #{msg.channel.name} ({date_str}) — [Post]({msg.jump_url}) (and {other_count} other reactions)")
        embed.add_field(name="Top 3 Posts", value="\n".join(lines), inline=False)
        await interaction.followup.send(embed=embed)

        # Image embeds with emoji and tiebreaker
        for count, msg in top3:
            img_url = None
            if msg.attachments:
                img_url = msg.attachments[0].url
            elif msg.embeds:
                if msg.embeds[0].image:
                    img_url = msg.embeds[0].image.url
                elif msg.embeds[0].thumbnail:
                    img_url = msg.embeds[0].thumbnail.url

            if img_url:
                other_count = sum(r.count for r in msg.reactions if getattr(r.emoji, "id", None) != selected_emoji_id)
                img_embed = discord.Embed(
                    description=f"{selected_emoji_str} — **{count}x** in #{msg.channel.name} — [Post]({msg.jump_url}) (and {other_count} other reactions)",
                    color=discord.Color.green()
                )
                img_embed.set_image(url=img_url)
                await interaction.followup.send(embed=img_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
