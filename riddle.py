import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import uuid
from datetime import datetime, timedelta

from riddle_views import SubmitSolutionView, ModerationView, CreatorDMView

RIDDLE_ROLE_ID = 1380610400416043089
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"

RIDDLES_FILE = "riddles.json"
USER_STATS_FILE = "user_stats.json"





def load_json(filename):
    if not os.path.exists(filename):
        with open(filename, 'w') as f:
            json.dump({}, f)
    with open(filename, 'r') as f:
        return json.load(f)

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

class Riddle(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.riddles = load_json(RIDDLES_FILE)
        self.user_stats = load_json(USER_STATS_FILE)
        self.setup_persistent_views()

    def setup_persistent_views(self):
        for riddle_id in self.riddles:
            self.bot.add_view(SubmitSolutionView(riddle_id))
            self.bot.add_view(ModerationView(riddle_id))
            self.bot.add_view(CreatorDMView(riddle_id))

    def save_data(self):
        save_json(RIDDLES_FILE, self.riddles)
        save_json(USER_STATS_FILE, self.user_stats)

    def convert_newlines(self, text):
        return text.replace("\\n", "\n")

    async def close_riddle(self, riddle_id, winner=None, submitted_solution=None):
        riddle = self.riddles.get(riddle_id)
        if not riddle:
            return

        channel = self.bot.get_channel(riddle['channel_id'])
        if not channel:
            return

        try:
            message = await channel.fetch_message(riddle['message_id'])
            await message.edit(view=None)

            solution_image = riddle.get('solution_image') or DEFAULT_IMAGE_URL
            creator = await self.bot.fetch_user(riddle['creator_id'])

            mentions = f"<@&{RIDDLE_ROLE_ID}>"
            if riddle.get('mention_group1'):
                mentions += f" <@&{riddle['mention_group1']}>"
            if riddle.get('mention_group2'):
                mentions += f" <@&{riddle['mention_group2']}>"

            embed = discord.Embed(
                title="🎉 Goon Hut Riddle Closed!",
                description=self.convert_newlines(riddle['text']),
                color=discord.Color.gold(),
                timestamp=datetime.utcnow()
            )
            embed.set_thumbnail(url=creator.avatar.url if creator.avatar else DEFAULT_IMAGE_URL)
            embed.set_image(url=solution_image)
            embed.add_field(name="✅ Solution:", value=riddle['solution'], inline=False)

            if winner:
                embed.add_field(name="🏆 Winner:", value=f"{winner.mention}", inline=False)
                embed.add_field(name="💬 Submitted Solution:", value=submitted_solution, inline=False)
                # Update user stats
                self.user_stats[str(winner.id)] = self.user_stats.get(str(winner.id), {"solved": 0, "submitted": 0})
                self.user_stats[str(winner.id)]['solved'] += 1

            else:
                embed.add_field(name="😢", value="No winner this time.", inline=False)

            if riddle.get('award'):
                embed.add_field(name="🎁 Award:", value=riddle['award'], inline=False)

            embed.set_footer(text=f"Created by {creator.name} | {channel.guild.name}", icon_url=channel.guild.icon.url if channel.guild.icon else None)

            await channel.send(content=mentions, embed=embed)

            # Cleanup: Remove thumbs buttons from DM and log
            if 'creator_dm_message_id' in riddle:
                try:
                    dm = await creator.fetch_message(riddle['creator_dm_message_id'])
                    await dm.edit(view=None)
                except:
                    pass

            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                try:
                    log_message = await log_channel.fetch_message(riddle['log_message_id'])
                    await log_message.edit(view=None)
                except:
                    pass

            del self.riddles[riddle_id]
            self.save_data()

        except Exception as e:
            print(f"Error closing riddle {riddle_id}: {e}")

    @app_commands.command(name="riddle_add", description="➕ Add a new riddle (Admins only)")
    async def add_riddle(self, interaction: discord.Interaction,
                         text: str,
                         solution: str,
                         channel: str,
                         image_url: str = DEFAULT_IMAGE_URL,
                         mention_group1: str = None,
                         mention_group2: str = None,
                         solution_image: str = None,
                         length: int = 1,
                         award: str = None):

        if RIDDLE_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ You don't have permission to add riddles.", ephemeral=True)
            return

        riddle_id = str(uuid.uuid4())
        target_channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
        if not target_channel:
            await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
            return

        expires_at = (datetime.utcnow() + timedelta(days=length)).isoformat()

        riddle_data = {
            "id": riddle_id,
            "text": text,
            "solution": solution,
            "creator_id": interaction.user.id,
            "channel_id": target_channel.id,
            "image_url": image_url,
            "mention_group1": mention_group1,
            "mention_group2": mention_group2,
            "solution_image": solution_image,
            "expires_at": expires_at,
            "award": award
        }

        self.riddles[riddle_id] = riddle_data
        self.save_data()

        mentions = f"<@&{RIDDLE_ROLE_ID}>"
        if mention_group1:
            mentions += f" <@&{mention_group1}>"
        if mention_group2:
            mentions += f" <@&{mention_group2}>"

        embed = discord.Embed(
            title=f"🧩 Goon Hut Riddle (Created: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')})",
            description=self.convert_newlines(text),
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else DEFAULT_IMAGE_URL)
        embed.set_image(url=image_url)
        if award:
            embed.add_field(name="🎁 Award:", value=award, inline=False)
        embed.set_footer(text=f"Created by {interaction.user.display_name} | Expires in {length} day(s)",
                         icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        view = SubmitSolutionView(riddle_id)
        riddle_message = await target_channel.send(content=mentions, embed=embed, view=view)
        self.riddles[riddle_id]['message_id'] = riddle_message.id
        self.save_data()

        await interaction.response.send_message(f"✅ Riddle added in {target_channel.mention}!", ephemeral=True)

    @app_commands.command(name="riddle_list", description="📜 List all active riddles")
    async def list_riddles(self, interaction: discord.Interaction):
        if not self.riddles:
            await interaction.response.send_message("😅 There are no active riddles.", ephemeral=True)
            return

        options = []
        for rid, data in self.riddles.items():
            creator = await self.bot.fetch_user(data['creator_id'])
            label = f"{rid[:8]} | {creator.display_name} | {data['text'][:30]}..."
            options.append(discord.SelectOption(label=label, value=rid))

        async def select_callback(select_interaction: discord.Interaction):
            selected_riddle_id = select.values[0]
            riddle = self.riddles[selected_riddle_id]
            creator = await self.bot.fetch_user(riddle['creator_id'])
            embed = discord.Embed(
                title=f"🧩 Riddle ID: {selected_riddle_id}",
                description=self.convert_newlines(riddle['text']),
                color=discord.Color.blue()
            )
            embed.set_thumbnail(url=creator.avatar.url if creator.avatar else DEFAULT_IMAGE_URL)
            embed.add_field(name="✅ Solution:", value=riddle['solution'], inline=False)
            embed.set_footer(text=f"Created by {creator.display_name}")

            view = RiddleListOptionsView(self, selected_riddle_id)

            await select_interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        select = discord.ui.Select(placeholder="Select a riddle", options=options)
        select.callback = select_callback

        view = discord.ui.View(timeout=300)
        view.add_item(select)

        await interaction.response.send_message("🗂️ Select a riddle to manage:", view=view, ephemeral=True)

class RiddleListOptionsView(discord.ui.View):
    def __init__(self, cog, riddle_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.riddle_id = riddle_id

    @discord.ui.button(label="✅ Close with Winner", style=discord.ButtonStyle.success)
    async def close_with_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please mention the winner (e.g. @User):", ephemeral=True)

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            msg = await self.cog.bot.wait_for('message', timeout=60.0, check=check)
            if not msg.mentions:
                await interaction.followup.send("❌ No user mentioned. Aborted.", ephemeral=True)
                return

            winner = msg.mentions[0]
            await self.cog.close_riddle(self.riddle_id, winner=winner, submitted_solution="Manually closed via list.")
            await interaction.followup.send(f"✅ Riddle closed with winner: {winner.mention}", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Timed out waiting for mention.", ephemeral=True)

    @discord.ui.button(label="❌ Close without Winner", style=discord.ButtonStyle.danger)
    async def close_without_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_riddle(self.riddle_id)
        await interaction.response.send_message("✅ Riddle closed without a winner.", ephemeral=True)

    @discord.ui.button(label="🗑️ Delete Riddle", style=discord.ButtonStyle.secondary)
    async def delete_riddle(self, interaction: discord.Interaction, button: discord.ui.Button):
        del self.cog.riddles[self.riddle_id]
        self.cog.save_data()
        await interaction.response.send_message("🗑️ Riddle deleted successfully.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Riddle(bot))
