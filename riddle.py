import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput, Select
import json
import os
from typing import Optional, List
from datetime import datetime, timedelta


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCog(bot))


RIDDLE_ROLE_ID = 1380610400416043089
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"
DATA_FILE = "riddles.json"

def load_riddles():
    if not os.path.isfile(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump({}, f)
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_riddles(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def format_multiline(text: str):
    return text.replace("\\n", "\n")

class RiddleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.riddles = load_riddles()
        self.check_expired_riddles.start()

    def cog_unload(self):
        self.check_expired_riddles.cancel()

    def get_riddle(self, riddle_id):
        return self.riddles.get(riddle_id)

    def delete_riddle(self, riddle_id):
        if riddle_id in self.riddles:
            del self.riddles[riddle_id]
            save_riddles(self.riddles)

    def generate_riddle_id(self):
        return str(int(datetime.now().timestamp()))

    @tasks.loop(minutes=1)
    async def check_expired_riddles(self):
        now = datetime.utcnow()
        expired = [rid for rid, data in self.riddles.items() if datetime.fromisoformat(data["expires_at"]) <= now]
        for rid in expired:
            await self.close_riddle(rid, None, expired=True)

    @check_expired_riddles.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
    @app_commands.command(name="add", description="Add a new riddle")
    async def add_riddle(
        self,
        interaction: discord.Interaction,
        text: str,
        solution: str,
        channel_name: str,
        image_url: Optional[str] = None,
        mention_group1: Optional[discord.Role] = None,
        mention_group2: Optional[discord.Role] = None,
        solution_image: Optional[str] = None,
        length: Optional[int] = 1,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if RIDDLE_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.followup.send("You don't have permission to use this command.")
            return

        channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
        if not channel:
            await interaction.followup.send(f"Channel `{channel_name}` not found.")
            return

        riddle_id = self.generate_riddle_id()
        expires_at = datetime.utcnow() + timedelta(days=length or 1)

        mentions = f"<@&{RIDDLE_ROLE_ID}>"
        if mention_group1:
            mentions += f" {mention_group1.mention}"
        if mention_group2:
            mentions += f" {mention_group2.mention}"

        embed = discord.Embed(
            title=f"🧩 Riddle of the Day (created {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})",
            description=format_multiline(text),
            color=discord.Color.purple(),
        )
        embed.set_image(url=image_url or DEFAULT_IMAGE)
        embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url)
        embed.set_footer(text=f"Guild: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.add_field(name="Creator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Expires", value=expires_at.strftime('%Y-%m-%d %H:%M UTC'), inline=True)

        view = RiddleView(self, riddle_id)

        message = await channel.send(content=mentions, embed=embed, view=view)

        self.riddles[riddle_id] = {
            "creator_id": interaction.user.id,
            "text": text,
            "solution": solution.lower().strip(),
            "image_url": image_url,
            "solution_image": solution_image,
            "channel_id": channel.id,
            "message_id": message.id,
            "mentions": [RIDDLE_ROLE_ID] + ([mention_group1.id] if mention_group1 else []) + ([mention_group2.id] if mention_group2 else []),
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at.isoformat(),
            "submitted": [],
        }

        save_riddles(self.riddles)

        await interaction.followup.send(f"Riddle posted successfully to {channel.mention} with ID `{riddle_id}`.")
class SubmitButton(discord.ui.Button):
    def __init__(self, cog: RiddleCog, riddle_id: str):
        super().__init__(label="Submit Solution", style=discord.ButtonStyle.primary, emoji="📝", custom_id=f"submit:{riddle_id}")
        self.cog = cog
        self.riddle_id = riddle_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SolutionModal(self.cog, self.riddle_id, interaction.user))

class SolutionModal(discord.ui.Modal, title="Submit Your Riddle Solution"):
    def __init__(self, cog: RiddleCog, riddle_id: str, submitter: discord.User):
        super().__init__(timeout=None)
        self.cog = cog
        self.riddle_id = riddle_id
        self.submitter = submitter

        self.solution = discord.ui.TextInput(
            label="Your solution",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.solution)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        riddle = self.cog.riddles.get(self.riddle_id)
        if not riddle:
            await interaction.followup.send("This riddle has already been closed.")
            return

        creator = interaction.client.get_user(riddle["creator_id"])
        if not creator:
            await interaction.followup.send("Could not find the riddle creator.")
            return

        embed = discord.Embed(
            title=f"💡 New Riddle Submission – ID `{self.riddle_id}`",
            description=format_multiline(riddle["text"]),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Submitted Solution", value=self.solution.value.strip(), inline=False)
        embed.add_field(name="Submitter", value=self.submitter.mention, inline=True)
        embed.set_thumbnail(url=self.submitter.avatar.url if self.submitter.avatar else self.submitter.default_avatar.url)
        embed.set_footer(text=f"Submitted on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

        view = ReviewButtons(self.cog, self.riddle_id, self.solution.value.strip(), self.submitter)

        # DM to creator
        try:
            dm = await creator.send(embed=embed, view=view)
            view.message = dm
        except discord.Forbidden:
            await interaction.followup.send("Couldn't send DM to the riddle creator.")
            return

        # Log to channel
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_msg = await log_channel.send(embed=embed, view=view)
            view.message = log_msg

        await interaction.followup.send("✅ Your solution has been submitted!")

class ReviewButtons(discord.ui.View):
    def __init__(self, cog: RiddleCog, riddle_id: str, submission: str, submitter: discord.User):
        super().__init__(timeout=None)
        self.cog = cog
        self.riddle_id = riddle_id
        self.submission = submission
        self.submitter = submitter
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="Accept ✅", style=discord.ButtonStyle.success, custom_id="review_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not self.cog.riddles.get(self.riddle_id):
            await interaction.followup.send("This riddle is already closed.", ephemeral=True)
            return
        await self.cog.close_riddle(self.riddle_id, winner=self.submitter, submitted_solution=self.submission)
        try:
            await interaction.user.send("✅ You accepted a solution. Riddle closed.")
        except:
            pass

    @discord.ui.button(label="Reject ❌", style=discord.ButtonStyle.danger, custom_id="review_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not self.cog.riddles.get(self.riddle_id):
            await interaction.followup.send("This riddle is already closed.", ephemeral=True)
            return
        try:
            await self.submitter.send("❌ Sorry, your solution was not correct!")
        except:
            pass
        if self.message:
            try:
                await self.message.delete()
            except:
                pass
    async def close_riddle(self, riddle_id: str, winner: Optional[discord.User] = None, submitted_solution: Optional[str] = None):
        riddle = self.riddles.get(riddle_id)
        if not riddle:
            return

        guild = self.bot.get_guild(riddle["guild_id"])
        channel = guild.get_channel(riddle["channel_id"]) if guild else None
        submitter_name = winner.name if winner else "No winner"
        submitter_mention = winner.mention if winner else "No winner"
        thumb_url = winner.avatar.url if winner and winner.avatar else ""

        mention_text = f"<@&{MENTION_ROLE_ID}>"
        if riddle.get("mention_group1"):
            mention_text += f" {riddle['mention_group1']}"
        if riddle.get("mention_group2"):
            mention_text += f" {riddle['mention_group2']}"

        embed = discord.Embed(
            title="✅ Riddle Closed",
            description=format_multiline(riddle["text"]),
            color=discord.Color.green()
        )
        embed.set_author(name="Riddle of the Day", icon_url=guild.icon.url if guild and guild.icon else None)
        embed.add_field(name="Correct Solution", value=riddle["solution"], inline=False)
        embed.add_field(name="Submitted Solution", value=submitted_solution if submitted_solution else "None", inline=False)
        embed.add_field(name="Winner", value=submitter_mention, inline=False)
        if thumb_url:
            embed.set_thumbnail(url=thumb_url)
        if riddle.get("solution_image"):
            embed.set_image(url=riddle["solution_image"])
        else:
            embed.set_image(url=DEFAULT_IMAGE)
        embed.set_footer(text="This riddle is now closed.")

        # Post result
        if channel:
            await channel.send(content=mention_text, embed=embed)

        # Cleanup all active messages and buttons
        for msg_id in riddle.get("active_messages", []):
            for chan in guild.text_channels:
                try:
                    msg = await chan.fetch_message(msg_id)
                    await msg.delete()
                except:
                    continue

        # Delete entry
        del self.riddles[riddle_id]
        self.save_riddles()

    @tasks.loop(minutes=1)
    async def check_expired_riddles(self):
        now = datetime.utcnow()
        to_close = [rid for rid, data in self.riddles.items() if now >= datetime.fromisoformat(data["expires_at"])]
        for rid in to_close:
            await self.close_riddle(rid)

    @check_expired_riddles.before_loop
    async def before_expiry_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.check_expired_riddles.is_running():
            self.check_expired_riddles.start()
    @app_commands.command(name="list", description="Manage all open riddles.")
    async def riddle_list(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not self.riddles:
            await interaction.followup.send("There are no active riddles.")
            return

        options = [
            discord.SelectOption(
                label=f"{rid} | {data['creator_name']}",
                description=f"Created: {data['created_at']}",
                value=rid
            )
            for rid, data in self.riddles.items()
        ]
        select = discord.ui.Select(placeholder="Select a riddle to manage", options=options, custom_id="manage_select")

        async def select_callback(inter: discord.Interaction):
            await self.show_riddle_actions(inter, select.values[0])

        select.callback = select_callback
        view = discord.ui.View()
        view.add_item(select)
        await interaction.followup.send("Choose a riddle to manage:", view=view, ephemeral=True)

    async def show_riddle_actions(self, interaction: discord.Interaction, riddle_id: str):
        riddle = self.riddles.get(riddle_id)
        if not riddle:
            await interaction.response.send_message("This riddle no longer exists.", ephemeral=True)
            return

        async def close_with_winner(inter: discord.Interaction):
            await inter.response.send_modal(WinnerModal(self, riddle_id))

        async def close_without_winner(inter: discord.Interaction):
            await self.close_riddle(riddle_id)
            await inter.response.send_message(f"Riddle {riddle_id} closed with no winner.", ephemeral=True)

        async def delete_riddle(inter: discord.Interaction):
            for msg_id in riddle.get("active_messages", []):
                for chan in interaction.guild.text_channels:
                    try:
                        msg = await chan.fetch_message(msg_id)
                        await msg.delete()
                    except:
                        continue
            del self.riddles[riddle_id]
            self.save_riddles()
            await inter.response.send_message(f"Riddle {riddle_id} deleted.", ephemeral=True)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Close with Winner", style=discord.ButtonStyle.green, custom_id=f"cw_{riddle_id}"))
        view.add_item(discord.ui.Button(label="Close without Winner", style=discord.ButtonStyle.blurple, custom_id=f"cwo_{riddle_id}"))
        view.add_item(discord.ui.Button(label="Delete Riddle", style=discord.ButtonStyle.red, custom_id=f"del_{riddle_id}"))

        async def interaction_handler(inter: discord.Interaction):
            cid = inter.data["custom_id"]
            if cid.startswith("cw_"):
                await close_with_winner(inter)
            elif cid.startswith("cwo_"):
                await close_without_winner(inter)
            elif cid.startswith("del_"):
                await delete_riddle(inter)

        view.on_timeout = None
        for item in view.children:
            item.callback = interaction_handler

        await interaction.response.send_message(f"What do you want to do with riddle `{riddle_id}`?", view=view, ephemeral=True)

class WinnerModal(discord.ui.Modal, title="Set Winner"):
    def __init__(self, cog, riddle_id):
        super().__init__()
        self.cog = cog
        self.riddle_id = riddle_id
        self.add_item(discord.ui.TextInput(label="Winner ID or name", placeholder="Enter winner's name or user ID", required=True))

    async def on_submit(self, interaction: discord.Interaction):
        winner_input = self.children[0].value.strip()
        winner = await self.find_user(interaction.guild, winner_input)
        if winner:
            await self.cog.close_riddle(self.riddle_id, winner=winner)
            await interaction.response.send_message(f"Riddle {self.riddle_id} closed. Winner: {winner.mention}", ephemeral=True)
        else:
            await interaction.response.send_message("User not found.", ephemeral=True)

    async def find_user(self, guild, query):
        if query.isdigit():
            return guild.get_member(int(query))
        else:
            for member in guild.members:
                if query.lower() in member.name.lower():
                    return member
        return None
    
