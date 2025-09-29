# hutvote.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar
import json
import os
import logging

log = logging.getLogger(__name__)

# === Konfiguration ===
ALLOWED_ROLE = 1346428405368750122   # ID deiner Mod/Admin-Rolle
BOT_ID = 1379906834588106883         # ID des Bots selbst
POSTED_FILE = "hutvote_posted.json"
TEST_GUILD_ID = 123456789012345678   # <--- HIER deine Testserver-ID eintragen

# === Choices für Dropdowns ===
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

RANK_CHOICES = [
    app_commands.Choice(name="5", value="5"),
    app_commands.Choice(name="10", value="10"),
    app_commands.Choice(name="20", value="20"),
]


# === JSON Helpers ===
def load_posted():
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            log.warning("⚠️ Fehler beim Laden von %s: %s", POSTED_FILE, e)
    return {}


def save_posted(data):
    try:
        with open(POSTED_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.error("❌ Fehler beim Speichern von %s: %s", POSTED_FILE, e)


# === Cog ===
class HutVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.posted = load_posted()

    @app_commands.command(
        name="hut_vote",
        description="Shows top posts by reactions for a category/month/year"
    )
    @app_commands.describe(
        year="Select year",
        month="Select month",
        category="Select category",
        ranks="Number of posts to rank",
        open="Post public (updates existing posts) or ephemeral"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        category=CATEGORY_CHOICES,
        ranks=RANK_CHOICES
    )
    async def hut_vote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        category: app_commands.Choice[str],
        ranks: app_commands.Choice[str] = app_commands.Choice(name="5", value="5"),
        open: bool = False
    ):
        """Slash-Command: zeigt Top-Bilder im Monat nach Reaktionen."""

        # --- Permission Check ---
        if not isinstance(interaction.user, discord.Member) or \
           ALLOWED_ROLE not in [r.id for r in interaction.user.roles]:
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return

        guild = interaction.guild
        category_obj = guild.get_channel(int(category.value))
        if not category_obj or not isinstance(category_obj, discord.CategoryChannel):
            await interaction.response.send_message("❌ Invalid category.", ephemeral=True)
            return

        # --- Prepare timeframe ---
        await interaction.response.defer(thinking=True, ephemeral=not open)
        start_dt = datetime(int(year.value), int(month.value), 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(int(year.value), int(month.value))[1]
        end_dt = datetime(int(year.value), int(month.value), last_day, 23, 59, 59, tzinfo=timezone.utc)

        # --- Collect messages ---
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
                    if msg.author.id != BOT_ID:
                        continue
                    matched_msgs.append(msg)
            except Exception as e:
                log.warning("⚠️ Fehler beim Lesen von %s: %s", channel, e)
                continue

        if not matched_msgs:
            await interaction.followup.send(
                f"No posts found in {calendar.month_name[int(month.value)]} {year.value}.",
                ephemeral=not open
            )
            return

        # --- Ranking ---
        rank_count = int(ranks.value)

        def sort_key(msg: discord.Message):
            sorted_reacts = sorted(msg.reactions, key=lambda r: r.count, reverse=True)
            top_sum = sum(r.count for r in sorted_reacts[:5])
            extra_sum = sum(r.count for r in sorted_reacts[5:])
            return (top_sum, extra_sum, msg.created_at)

        top_msgs = sorted(matched_msgs, key=sort_key, reverse=True)[:rank_count]

        # --- Init storage key ---
        key = f"{guild.id}-{category.value}-{year.value}-{month.value}"
        if key not in self.posted:
            self.posted[key] = {}

        # --- Create embeds ---
        for msg in top_msgs:
            sorted_reacts = sorted(msg.reactions, key=lambda r: r.count, reverse=True)
            reaction_lines = [
                f"{str(r.emoji) if isinstance(r.emoji, (discord.Emoji, str)) else r.emoji} {r.count}"
                for r in sorted_reacts[:5]
            ]
            reaction_line = "\n".join(reaction_lines)
            extra_reactions = sum(r.count for r in sorted_reacts[5:])
            extra_text = f"\n({extra_reactions} additional reactions)" if extra_reactions else ""

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

            if not img_url:
                continue

            embed = discord.Embed(
                title=title,
                description=f"{reaction_line}\n[Post]({msg.jump_url}){extra_text}",
                color=discord.Color.green()
            )
            embed.set_image(url=img_url)

            msg_key = str(msg.id)
            if open and msg_key in self.posted[key]:
                channel_id, sent_msg_id = self.posted[key][msg_key]
                channel_obj = guild.get_channel(channel_id)
                if channel_obj:
                    try:
                        sent_msg = await channel_obj.fetch_message(sent_msg_id)
                        await sent_msg.edit(embed=embed)
                        continue
                    except Exception:
                        pass  # Falls gelöscht → neu posten

            sent = await interaction.followup.send(embed=embed, ephemeral=not open)
            if open:
                self.posted[key][msg_key] = (interaction.channel_id, sent.id)
                save_posted(self.posted)

        # --- Extra Post: Top1 Creator ---
        top1_msg = top_msgs[0]
        top1_creator = top1_msg.mentions[0] if top1_msg.mentions else top1_msg.author
        await interaction.followup.send(
            f"In {calendar.month_name[int(month.value)]}/{year.value}, "
            f"the user {top1_creator.mention} has created the image with most total votes in the {category_obj.name}!",
            ephemeral=not open
        )


# === Setup ===
async def setup(bot: commands.Bot):
    cog = HutVote(bot)
    await bot.add_cog(cog)
    bot.tree.add_command(cog.hut_vote, guild=discord.Object(TEST_GUILD_ID))  # sofort sichtbar in Test-Server
