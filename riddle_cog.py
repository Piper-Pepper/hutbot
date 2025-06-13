import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import datetime
import random

from riddle_views import PersistentRiddleView, RiddleView, RiddleSelect

RIDDLE_FILE = 'riddles.json'
GUILD_LOGO_URL = 'https://cdn.discordapp.com/icons/{guild_id}/{icon}.png'
DEFAULT_IMAGE_URL = 'https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg'
MENTION_ROLE_ID = 1380610400416043089

class RiddleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.riddles = {}
        self.load_riddles()
        self.bot.add_view(PersistentRiddleView(self))
        self.check_expiry.start()

    def load_riddles(self):
        if os.path.exists(RIDDLE_FILE):
            with open(RIDDLE_FILE, 'r') as f:
                self.riddles = json.load(f)

    def save_riddles(self):
        with open(RIDDLE_FILE, 'w') as f:
            json.dump(self.riddles, f, indent=4, default=str)

    def generate_riddle_id(self):
        return str(random.randint(1000, 9999))

    def get_riddle_by_message(self, message_id):
        for rid, data in self.riddles.items():
            if data['message_id'] == message_id:
                return rid, data
        return None, None

    @commands.Cog.listener()
    async def on_ready(self):
        print("RiddleCog loaded and ready.")

    @app_commands.command(name="riddle_add", description="Add a new riddle.")
    async def riddle_add(self, interaction: discord.Interaction, text: str, solution: str, channel: discord.TextChannel,
                         image_url: str = None, mention_group1: str = None, mention_group2: str = None,
                         solution_image: str = None, length: int = 1, award: str = None):

        if MENTION_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("You do not have permission to add riddles.", ephemeral=True)
            return

        riddle_id = self.generate_riddle_id()
        expiry = datetime.datetime.utcnow() + datetime.timedelta(days=length)

        riddle_data = {
            'id': riddle_id,
            'text': text.replace('\\n', '\n'),
            'solution': solution,
            'channel_id': channel.id,
            'author_id': interaction.user.id,
            'author_name': interaction.user.display_name,
            'author_avatar': str(interaction.user.avatar),
            'image_url': image_url if image_url else DEFAULT_IMAGE_URL,
            'mention_group1': mention_group1,
            'mention_group2': mention_group2,
            'solution_image': solution_image if solution_image else DEFAULT_IMAGE_URL,
            'created_at': str(datetime.datetime.utcnow()),
            'expiry': str(expiry),
            'message_id': None,
            'award': award
        }

        embed = self.create_riddle_embed(riddle_data, interaction.guild)
        view = RiddleView(self, riddle_id)
        message = await channel.send(content=self.build_mentions(riddle_data), embed=embed, view=view)
        riddle_data['message_id'] = message.id
        self.riddles[riddle_id] = riddle_data
        self.save_riddles()

        await interaction.response.send_message(f"Riddle {riddle_id} successfully posted in {channel.mention}.", ephemeral=True)

    def build_mentions(self, riddle_data, winner=None):
        mentions = f"<@&{MENTION_ROLE_ID}>"
        if riddle_data['mention_group1']:
            mentions += f" {riddle_data['mention_group1']}"
        if riddle_data['mention_group2']:
            mentions += f" {riddle_data['mention_group2']}"
        if winner:
            mentions += f" {winner.mention}"
        return mentions

    def create_riddle_embed(self, riddle_data, guild):
        embed = discord.Embed(
            title=f"\U0001F9E0 Riddle of the Day ({riddle_data['created_at'].split(' ')[0]})",
            description=riddle_data['text'],
            color=discord.Color.purple()
        )
        embed.set_image(url=riddle_data['image_url'])
        embed.set_thumbnail(url=riddle_data['author_avatar'])

 
        if riddle_data.get('award'):
            embed.add_field(name="🎗️Award:", value=riddle_data['award'], inline=False)


        if guild.icon:
            embed.set_footer(text=f"{guild.name} | Riddle ID: {riddle_data['id']}", icon_url=guild.icon.url)
        else:
            embed.set_footer(text=f"Riddle ID: {riddle_data['id']}")
        return embed

    @tasks.loop(minutes=1)
    async def check_expiry(self):
        now = datetime.datetime.utcnow()
        to_close = []
        for riddle_id, riddle_data in self.riddles.items():
            if datetime.datetime.fromisoformat(riddle_data['expiry']) < now:
                to_close.append(riddle_id)

        for riddle_id in to_close:
            await self.close_riddle(riddle_id)

    async def close_riddle(self, riddle_id, winner=None, proposed_solution=None):
        riddle = self.riddles.get(riddle_id)
        if not riddle:
            return

        channel = self.bot.get_channel(riddle['channel_id'])
        message = await channel.fetch_message(riddle['message_id'])

        embed = discord.Embed(
            title="\U0000274C Riddle Closed",
            description=riddle['text'],
            color=discord.Color.red()
        )
        embed.set_image(url=riddle['solution_image'])

        if winner:
            embed.add_field(name="\U0001F3C6 Winner", value=f"{winner.display_name}", inline=False)
            embed.add_field(name="Submitted Solution", value=proposed_solution or "(None)", inline=False)
            embed.add_field(name="Correct Solution", value=riddle['solution'], inline=False)
            
            embed.set_thumbnail(url=winner.display_avatar.url)  # Sicherer Avatar-Link
            if riddle.get('award'):  # riddle statt riddle_data
                embed.add_field(name="🎗️Award:", value=riddle['award'], inline=False)
            
        else:
            embed.add_field(name="\U0001F3C6 Winner", value="No winner", inline=False)
            embed.add_field(name="Correct Solution", value=riddle['solution'], inline=False)



        await channel.send(content=self.build_mentions(riddle, winner=winner), embed=embed)
        await message.edit(view=None)

        del self.riddles[riddle_id]
        self.save_riddles()

    @app_commands.command(name="riddle_list", description="List all active riddles.")
    async def riddle_list(self, interaction: discord.Interaction):
        if MENTION_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("You do not have permission to manage riddles.", ephemeral=True)
            return

        if not self.riddles:
            await interaction.response.send_message("There are no active riddles.", ephemeral=True)
            return

        options = [discord.SelectOption(label=f"{rid} - {data['created_at'].split(' ')[0]}", value=rid) for rid, data in self.riddles.items()]
        select = RiddleSelect(self, options)
        view = discord.ui.View(timeout=None)
        view.add_item(select)

        await interaction.response.send_message("Select a riddle to manage:", view=view, ephemeral=True)
        
    @commands.Cog.listener()
    async def on_ready(self):
        print("✅ Riddle loaded and ready.")

async def setup(bot):
    await bot.add_cog(RiddleCog(bot))
