# cogs/hutvote.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar

# Only this role can use the command
ALLOWED_ROLE = 1346428405368750122

# Category choices (only these two selectable)
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
        emoji="Select the emoji"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        category=CATEGORY_CHOICES
    )
    async def hut_vote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        category: app_commands.Choice[str],
        emoji: discord.Emoji
    ):
        # Permission check
        member_roles = getattr(interaction.user, "roles", [])
        if not any(r.id == ALLOWED_ROLE for r in member_roles):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

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

            overwrites = channel.overwrites_for(guild.default_role)
            if overwrites.view_channel is False:
                continue

            perms = channel.permissions_for(guild.me)
            if not perms.view_channel or not perms.read_message_history:
                continue

            try:
                async for msg in channel.history(after=start_dt, before=end_dt, limit=None):
                    for reaction in msg.reactions:
                        if getattr(reaction.emoji, "id", None) == emoji.id:
                            matched_msgs.append((reaction.count, msg))
            except discord.Forbidden:
                continue
            except Exception:
                continue

        if not matched_msgs:
            await interaction.followup.send(f"No posts found with {emoji} in {calendar.month_name[int(month.value)]} {year.value}.")
            return

        # Top 3
        top3 = sorted(matched_msgs, key=lambda x: (x[0], x[1].created_at), reverse=True)[:3]

        # Overview embed
        embed = discord.Embed(
            title=f"Top 3 posts for {emoji} in {calendar.month_name[int(month.value)]} {year.value} ({category_obj.name})",
            color=discord.Color.blurple()
        )
        lines = []
        for i, (count, msg) in enumerate(top3, start=1):
            date_str = msg.created_at.strftime("%Y-%m-%d")
            lines.append(f"{i}. **{count}x** in #{msg.channel.name} ({date_str}) — [Post]({msg.jump_url})")
        embed.add_field(name=str(emoji), value="\n".join(lines), inline=False)
        await interaction.followup.send(embed=embed)

        # Image embeds
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
                img_embed = discord.Embed(
                    description=f"{emoji} — **{count}x** in #{msg.channel.name} — [Post]({msg.jump_url})",
                    color=discord.Color.green()
                )
                img_embed.set_image(url=img_url)
                await interaction.followup.send(embed=img_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVote(bot))
