import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timedelta
from riddle_view import SubmitSolutionButton, setup_persistent_views
from riddle_utils import riddle_cache
from riddle_utils import close_riddle_with_winner, riddle_cache
import asyncio

RIDDLE_GROUP_ID = 1380610400416043089
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"
GUILD_ID = 1346389858062434354  # Replace with your actual guild ID

RIDDLE_PATH = "riddles.json"
USER_STATS_PATH = "user_stats.json"

COOLDOWN_SECONDS = 30  # Cooldown between riddle submissions per user

class Riddle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(setup_persistent_views(bot))
        self.cooldowns = {}  # User cooldown tracking: {user_id: timestamp}
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()

    # Helper: Save riddles to JSON
    def save_riddles(self):
        with open(RIDDLE_PATH, "w", encoding="utf-8") as f:
            json.dump(riddle_cache, f, indent=2)

    # Helper: Save user stats to JSON
    def save_user_stats(self, stats):
        with open(USER_STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

    # Helper: Load user stats from JSON
    def load_user_stats(self):
        if os.path.exists(USER_STATS_PATH):
            with open(USER_STATS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def is_on_cooldown(self, user_id):
        now = datetime.utcnow().timestamp()
        last = self.cooldowns.get(user_id, 0)
        return (now - last) < COOLDOWN_SECONDS

    def update_cooldown(self, user_id):
        self.cooldowns[user_id] = datetime.utcnow().timestamp()

    @app_commands.command(name="add", description="Add a new riddle")
    async def add_riddle(self, interaction: discord.Interaction,
                         text: str,
                         solution: str,
                         target_channel: discord.TextChannel,
                         image_url: str = None,
                         mention_group1: discord.Role = None,
                         mention_group2: discord.Role = None,
                         solution_image: str = None,
                         length: int = 1,
                         award: str = None):
        # Permissions check
        if RIDDLE_GROUP_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ You don't have permission to create this magical puzzle.", ephemeral=True)
            return

        # Cooldown check
        if self.is_on_cooldown(interaction.user.id):
            await interaction.response.send_message(f"⏳ Chill , {interaction.user.mention}! Wait {COOLDOWN_SECONDS} between the Riddles.", ephemeral=True)
            return
        self.update_cooldown(interaction.user.id)

        riddle_id = str(int(datetime.utcnow().timestamp()))
        image_url = image_url or DEFAULT_IMAGE_URL
        expires_at = (datetime.utcnow() + timedelta(days=length)).isoformat()

        mentions = [f"<@&{RIDDLE_GROUP_ID}>"]
        if mention_group1: mentions.append(mention_group1.mention)
        if mention_group2: mentions.append(mention_group2.mention)

        embed = discord.Embed(
            title=f"🧩 Goon Hut Riddle (Created: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})",
            description=text.replace("\\n", "\n"),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_image(url=image_url)
        embed.set_footer(text=f"By {interaction.user.display_name} | Ends in {length} day(s)", icon_url=interaction.guild.icon.url)
        if award:
            embed.add_field(name="🎁 Award", value=award, inline=False)

        view = discord.ui.View(timeout=None)
        view.add_item(SubmitSolutionButton(riddle_id, text, interaction.user.id))

        msg = await target_channel.send(content=' '.join(mentions), embed=embed, view=view)

        # Save riddle to cache/JSON
        riddle_cache[riddle_id] = {
            "text": text,
            "solution": solution,
            "channel_id": target_channel.id,
            "message_id": msg.id,
            "creator_id": interaction.user.id,
            "creator_name": interaction.user.display_name,
            "creator_avatar": interaction.user.display_avatar.url,
            "mention_group1": mention_group1.id if mention_group1 else None,
            "mention_group2": mention_group2.id if mention_group2 else None,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at,
            "award": award,
            "solution_image": solution_image,
            "attempts": 0,
            "failed_attempts": 0,
            "closed": False
        }

        self.save_riddles()

        # Update user stats for submitted riddles
        stats = self.load_user_stats()
        stats.setdefault(str(interaction.user.id), {"submitted": 0, "solved": 0, "attempts": 0, "failed": 0})
        stats[str(interaction.user.id)]["submitted"] += 1
        self.save_user_stats(stats)

        await interaction.response.send_message("✅ Your riddle was sent out into the gooning world!", ephemeral=True)

    @app_commands.command(name="list", description="List all open riddles.")
    async def list_riddles(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = RiddleManageView(self, riddle_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        # Filter only open riddles
        open_riddles = {rid: data for rid, data in riddle_cache.items() if not data.get("closed", False)}

        if not open_riddles:
            await interaction.followup.send("🎉 There are currently no open puzzles. Time to create new ones!", ephemeral=True)
            return

        options = []
        for rid, data in open_riddles.items():
            label = f"ID: {rid} | von {data['creator_name']} am {data['created_at'][:10]}"
            options.append(discord.SelectOption(label=label[:100], value=rid))

        class RiddleSelect(discord.ui.Select):
            def __init__(self):
                super().__init__(placeholder="Wähle ein Rätsel", options=options)

            async def callback(self, select_interaction: discord.Interaction):
                selected_rid = self.values[0]
                riddle_data = riddle_cache.get(selected_rid)
                if not riddle_data:
                    await select_interaction.response.send_message("❌ This riddle no longer exists.", ephemeral=True)
                    return

                embed = discord.Embed(
                    title=f"🧩 Goon Hut Rätsel (ID: {selected_rid})",
                    description=riddle_data["text"].replace("\\n", "\n"),
                    color=discord.Color.blurple()
                )
                embed.set_thumbnail(url=riddle_data["creator_avatar"])
                embed.add_field(name="🎯 Lösung", value=riddle_data["solution"], inline=False)
                embed.add_field(name="📅 Erstellt von", value=riddle_data['creator_name'], inline=True)
                embed.set_footer(text=f"Erstellt am {riddle_data['created_at'][:10]}")

                class WinnerModal(discord.ui.Modal, title="Close puzzles - specify the winner"):
                    winner_id = discord.ui.TextInput(label="Winner User-ID or @mention", required=True)

                    async def on_submit(inner_self, modal_interaction: discord.Interaction):
                        try:
                            winner_raw = inner_self.winner_id.value.strip("<@!>")
                            winner = modal_interaction.guild.get_member(int(winner_raw))
                            if not winner:
                                raise ValueError
                        except:
                            await modal_interaction.response.send_message("❌ False Member.", ephemeral=True)
                            return

                        await self.close_riddle(selected_rid, winner=winner)
                        await modal_interaction.response.send_message(f"✅ Riddle {selected_rid} closed with a winner {winner.mention}", ephemeral=True)

                class RiddleManageView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)

                    @discord.ui.button(label="✅ Close with Winner", style=discord.ButtonStyle.green)
                    async def close_with_winner(self, button: discord.ui.Button, button_interaction: discord.Interaction):
                        await button_interaction.response.send_modal(WinnerModal())

                    @discord.ui.button(label="🔒 Close without winner", style=discord.ButtonStyle.blurple)
                    async def close_without_winner(self, button: discord.ui.Button, button_interaction: discord.Interaction):
                        await self.close_riddle(selected_rid)
                        await button_interaction.response.send_message(f"Riddle {selected_rid} closed without winner.", ephemeral=True)

                    @discord.ui.button(label="❌ Löschen", style=discord.ButtonStyle.danger)
                    async def delete_riddle(self, button: discord.ui.Button, button_interaction: discord.Interaction):
                        await self.delete_riddle(selected_rid)
                        await button_interaction.response.send_message(f"Riddle {selected_rid} deleted.", ephemeral=True)

                    async def close_riddle(self_inner, rid, winner=None):
                        await self.cog.close_riddle(rid, winner)

                    async def delete_riddle(self_inner, rid):
                        await self.cog.delete_riddle(rid)

                view = RiddleManageView()
                await select_interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        await interaction.followup.send("Choose a riddle to edit:", view=discord.ui.View(timeout=60).add_item(RiddleSelect()), ephemeral=True)

    @app_commands.command(name="leaderboard", description="Zeige die Top Riddle-Champions")
    async def leaderboard(self, interaction: discord.Interaction):
        stats = self.load_user_stats()
        if not stats:
            await interaction.response.send_message("Nobody has solved my riddle so far. Become the first!", ephemeral=True)
            return

        # Sortiere nach gelöste Rätsel absteigend
        sorted_stats = sorted(stats.items(), key=lambda x: x[1].get("solved", 0), reverse=True)[:10]

        embed = discord.Embed(title="🏆 Riddle Leaderboard", color=discord.Color.gold())
        for i, (user_id, data) in enumerate(sorted_stats, 1):
            member = self.bot.get_user(int(user_id))
            name = member.display_name if member else f"User {user_id}"
            embed.add_field(name=f"{i}. {name}", value=f"GeSolvedlöst: {data.get('solved', 0)} | Submitted: {data.get('submitted', 0)}", inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="history", description="Zeige deine gelösten Rätsel")
    async def history(self, interaction: discord.Interaction):
        stats = self.load_user_stats()
        user_data = stats.get(str(interaction.user.id))
        if not user_data or user_data.get("solved", 0) == 0:
            await interaction.response.send_message("You haven't solved any puzzles yet. Time to activate your gray cells! 🧠", ephemeral=True)
            return

        solved_count = user_data.get("solved", 0)
        submitted = user_data.get("submitted", 0)

        embed = discord.Embed(
            title=f"🧠 Riddle History of {interaction.user.display_name}",
            description=f"you have solved {solved_count} riddles and submitted {submitted} riddles.",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tasks.loop(minutes=60)
    async def reminder_loop(self):
        # Sende Erinnerungen für Rätsel, die bald ablaufen (innerhalb 24h)
        now = datetime.utcnow()
        soon = now + timedelta(hours=24)
        for rid, data in list(riddle_cache.items()):
            if data.get("closed", False):
                continue
            expires_at = datetime.fromisoformat(data["expires_at"])
            if now < expires_at <= soon:
                channel = self.bot.get_channel(data["channel_id"])
                if channel:
                    try:
                        await channel.send(f"⏰ Hey! The riddle `{rid}` by {data['creator_name']} is running out soon... 🤔")
                    except Exception:
                        pass

    @reminder_loop.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    # Die Funktion zum Schließen eines Rätsels mit Gewinner
async def close_riddle(self, rid, winner=None):
    riddle = riddle_cache.get(rid)
    if not riddle or riddle.get("closed", False):
        return
    riddle["closed"] = True
    riddle["closed_at"] = datetime.utcnow().isoformat()
    riddle_cache[rid] = riddle
    self.save_riddles()

    channel = self.bot.get_channel(riddle["channel_id"])

    if winner:
        stats = self.load_user_stats()
        stats.setdefault(str(winner.id), {"submitted": 0, "solved": 0, "attempts": 0, "failed": 0})
        stats[str(winner.id)]["solved"] += 1
        self.save_user_stats(stats)

        if channel:
            await channel.send(f"🎉 Das Rätsel `{rid}` wurde von {winner.mention} gelöst! Glückwunsch! 🎊")

    log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"Riddle `{rid}` geschlossenclosed. Winner: {winner.mention if winner else 'Nobody'}.")

    if winner and channel:
        embed = discord.Embed(
            title="🎉 Rätsel gelöst!",
            description=f"{winner.mention} has solved the Riddle!",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=winner.display_avatar.url)
        embed.add_field(name="🧠 Submitted solution", value=riddle.get("solution", "No solution specified."), inline=False)
        embed.add_field(name="✅ Official solution", value=riddle.get("solution", "No solution."), inline=False)

        award_text = riddle.get("award")
        if award_text:
            embed.add_field(name="🏅 Award", value=award_text, inline=False)

        embed.set_image(url=riddle.get("solution_image") or DEFAULT_IMAGE_URL)

        mentions = [f"<@&{RIDDLE_GROUP_ID}>"]
        mention_group1 = riddle.get("mention_group1")
        mention_group2 = riddle.get("mention_group2")
        if mention_group1:
            mentions.append(f"<@&{mention_group1}>")
        if mention_group2:
            mentions.append(f"<@&{mention_group2}>")

        await channel.send(content=' '.join(mentions), embed=embed)



    # Löschen eines Rätsels komplett
    async def delete_riddle(self, rid):
        if rid in riddle_cache:
            del riddle_cache[rid]
            self.save_riddles()
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"Rätsel `{rid}` wurde gelöscht.")

    # Diese Methode wird aufgerufen, wenn ein User eine Lösung einreicht
    async def process_solution(self, user, rid, submitted_solution):
        riddle = riddle_cache.get(rid)
        if not riddle or riddle.get("closed", False):
            return False, "This riddle is already closed."

        # Cooldown für Lösungseinreichung
        if self.is_on_cooldown(user.id):
            return False, f"⏳ Wait {COOLDOWN_SECONDS} seconds between your solutions."

        self.update_cooldown(user.id)

        riddle["attempts"] = riddle.get("attempts", 0) + 1

        # Vergleich der Lösung
        if submitted_solution.lower().strip() == riddle["solution"].lower().strip():
            # Riddle solved
            await self.close_riddle(rid, winner=user)

            # Update stats
            stats = self.load_user_stats()
            stats.setdefault(str(user.id), {"submitted": 0, "solved": 0, "attempts": 0, "failed": 0})
            stats[str(user.id)]["solved"] += 1
            stats[str(user.id)]["attempts"] += 1
            self.save_user_stats(stats)

            return True, "🎉 Correct! You solved the riddle! Congratulations! 🎉"
        else:
            riddle["failed_attempts"] = riddle.get("failed_attempts", 0) + 1

            stats = self.load_user_stats()
            stats.setdefault(str(user.id), {"submitted": 0, "solved": 0, "attempts": 0, "failed": 0})
            stats[str(user.id)]["attempts"] += 1
            stats[str(user.id)]["failed"] += 1
            self.save_user_stats(stats)

            self.save_riddles()
            return False, "❌ Incorrect! Try again, the spirit of the puzzle world watches you ... 👻"

async def setup(bot):
    await bot.add_cog(Riddle(bot))
