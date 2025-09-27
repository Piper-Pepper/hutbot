# cogs/hutvote_debug.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar

ALLOWED_ROLE = 1346428405368750122

CATEGORY_CHOICES = [
    app_commands.Choice(name="📂 Category 1", value="1416461717038170294"),
    app_commands.Choice(name="📂 Category 2", value="1415769711052062820"),
]

current_year = datetime.utcnow().year
YEAR_CHOICES = [
    app_commands.Choice(name=str(current_year), value=str(current_year)),
    app_commands.Choice(name=str(current_year - 1), value=str(current_year - 1)),
]

MONTH_CHOICES = [
    app_commands.Choice(name=calendar.month_name[i], value=str(i).zfill(2)) for i in range(1, 13)
]

# Custom emoji IDs
REACTIONS = [
    1387086056498921614,  # main 1
    1387083454575022213,  # main 2
    1347536448831754383,  # main 3
    1346549711817146400,  # main 4
    1346549688836296787   # tiebreaker
]

MAIN_REACTIONS = REACTIONS[:4]
TIEBREAK_REACTION = REACTIONS[4]

class HutVoteDebug(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="hut_vote_debug",
        description="Shows top 5 posts with debug logs"
    )
    @app_commands.describe(
        year="Select year",
        month="Select month",
        category="Select category"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        category=CATEGORY_CHOICES
    )
    async def hut_vote_debug(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        category: app_commands.Choice[str]
    ):
        if not any(r.id == ALLOWED_ROLE for r in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return

        guild = interaction.guild
        category_obj = guild.get_channel(int(category.value))
        if not category_obj or not isinstance(category_obj, discord.CategoryChannel):
            await interaction.response.send_message("❌ Invalid category.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        start_dt = datetime(int(year.value), int(month.value), 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(int(year.value), int(month.value))[1]
        end_dt = datetime(int(year.value), int(month.value), last_day, 23, 59, 59, tzinfo=timezone.utc)

        matched_msgs = []

        for channel in category_obj.channels:
            if not isinstance(channel, discord.TextChannel):
                continue

            overwrites = channel.overwrites_for(guild.default_role)
            if overwrites.view_channel is False:
                print(f"Skipping {channel.name}: @everyone cannot view")
                continue

            perms = channel.permissions_for(guild.me)
            if not perms.view_channel or not perms.read_message_history:
                print(f"Skipping {channel.name}: Bot cannot view or read messages")
                continue

            print(f"Scanning channel: {channel.name}")

            try:
                async for msg in channel.history(after=start_dt, before=end_dt, limit=None):
                    counts = {}
                    has_main = False
                    for r_id in REACTIONS:
                        for reaction in msg.reactions:
                            if getattr(reaction.emoji, "id", None) == r_id:
                                counts[r_id] = reaction.count
                                if r_id in MAIN_REACTIONS and reaction.count > 0:
                                    has_main = True
                                break
                        else:
                            counts[r_id] = 0

                    print(f"Message {msg.id} in {channel.name}: counts={counts}, has_main={has_main}")

                    if has_main:
                        matched_msgs.append((counts, msg))
            except Exception as e:
                print(f"Error reading channel {channel.name}: {e}")

        if not matched_msgs:
            await interaction.followup.send(f"No posts found in {calendar.month_name[int(month.value)]} {year.value}.")
            return

        def sort_key(item):
            counts, msg = item
            main_sum = sum(counts[r_id] for r_id in MAIN_REACTIONS)
            tie = counts[TIEBREAK_REACTION]
            return (main_sum, tie, msg.created_at)

        top5 = sorted(matched_msgs, key=sort_key, reverse=True)[:5]

        posted_message_ids = set()

        for counts, msg in top5:
            if msg.id in posted_message_ids:
                continue
            posted_message_ids.add(msg.id)

            reaction_lines = []
            for r_id in REACTIONS:
                emoji_obj = guild.get_emoji(r_id)
                emoji_display = str(emoji_obj) if emoji_obj else f"<:{r_id}>"
                reaction_lines.append(f"{emoji_display} {counts[r_id]}")
            reaction_line = "\n".join(reaction_lines)

            creator_name = msg.mentions[0].display_name if msg.mentions else msg.author.display_name
            title = f"Image by {creator_name}"

            img_url = None
            if msg.attachments:
                img_url = msg.attachments[0].url
            elif msg.embeds:
                for e in msg.embeds:
                    if e.image:
                        img_url = e.image.url
                        break
                    elif e.thumbnail:
                        img_url = e.thumbnail.url
                        break

            embed = discord.Embed(
                title=title,
                description=f"{reaction_line}\n[Post]({msg.jump_url})",
                color=discord.Color.green()
            )
            if img_url:
                embed.set_image(url=img_url)

            await interaction.followup.send(embed=embed)

        top1_msg = top5[0][1]
        top1_creator_mention = top1_msg.mentions[0].mention if top1_msg.mentions else top1_msg.author.mention
        await interaction.followup.send(
            f"In {calendar.month_name[int(month.value)]}/{year.value}, the user {top1_creator_mention} has created the image with most total votes in the {category_obj.name}!"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HutVoteDebug(bot))
