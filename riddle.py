import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput, Select
import json
import os
import datetime
import random

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
                         solution_image: str = None, length: int = 1):

        if MENTION_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("You do not have permission to add riddles.", ephemeral=True)
            return

        riddle_id = self.generate_riddle_id()
        expiry = datetime.datetime.utcnow() + datetime.timedelta(days=length)

        riddle_data = {
            'id': riddle_id,
            'text': text,
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
            'message_id': None
        }

        embed = self.create_riddle_embed(riddle_data, interaction.guild)
        view = RiddleView(self, riddle_id)

        message = await channel.send(content=self.build_mentions(riddle_data), embed=embed, view=view)
        riddle_data['message_id'] = message.id
        self.riddles[riddle_id] = riddle_data
        self.save_riddles()

        await interaction.response.send_message(f"Riddle {riddle_id} successfully posted in {channel.mention}.", ephemeral=True)

    def build_mentions(self, riddle_data):
        mentions = f"<@&{MENTION_ROLE_ID}>"
        if riddle_data['mention_group1']:
            mentions += f" {riddle_data['mention_group1']}"
        if riddle_data['mention_group2']:
            mentions += f" {riddle_data['mention_group2']}"
        return mentions

    def create_riddle_embed(self, riddle_data, guild):
        embed = discord.Embed(
            title=f"\U0001F9E0 Riddle of the Day ({riddle_data['created_at'].split(' ')[0]})",
            description=riddle_data['text'],
            color=discord.Color.purple()
        )
        embed.set_image(url=riddle_data['image_url'])
        embed.set_author(name=riddle_data['author_name'], icon_url=riddle_data['author_avatar'])
        if guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
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
            embed.add_field(name="\U0001F3C6 Winner", value=f"{winner.mention}", inline=False)
            embed.add_field(name="Submitted Solution", value=proposed_solution or "(None)", inline=False)
            embed.set_footer(text=f"Correct Solution: {riddle['solution']}")
        else:
            embed.add_field(name="\U0001F3C6 Winner", value="No winner", inline=False)
            embed.set_footer(text=f"Solution: {riddle['solution']}")

        await channel.send(content=self.build_mentions(riddle), embed=embed)
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
        view = View(timeout=None)
        view.add_item(select)

        await interaction.response.send_message("Select a riddle to manage:", view=view, ephemeral=True)

class PersistentRiddleView(View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Submit Solution", style=discord.ButtonStyle.primary, emoji="\U0001F522", custom_id="persistent_submit")
    async def submit_solution(self, interaction: discord.Interaction, button: discord.ui.Button):
        message_id = interaction.message.id
        riddle_id, riddle = self.cog.get_riddle_by_message(message_id)
        if not riddle:
            await interaction.response.send_message("This riddle is no longer active.", ephemeral=True)
            return

        modal = SolutionModal(self.cog, riddle_id)
        await interaction.response.send_modal(modal)

class RiddleView(View):
    def __init__(self, cog, riddle_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(PersistentRiddleView(cog).children[0])

class SolutionModal(Modal):
    def __init__(self, cog, riddle_id):
        super().__init__(title="Submit Solution")
        self.cog = cog
        self.riddle_id = riddle_id
        self.solution_input = TextInput(label="Your Solution", style=discord.TextStyle.paragraph)
        self.add_item(self.solution_input)

    async def on_submit(self, interaction: discord.Interaction):
        riddle = self.cog.riddles[self.riddle_id]
        author = await self.cog.bot.fetch_user(riddle['author_id'])

        embed = discord.Embed(
            title=f"\U0001F4DD Solution Proposal for Riddle {self.riddle_id}",
            description=riddle['text'],
            color=discord.Color.blue()
        )
        embed.add_field(name="Proposed Solution", value=self.solution_input.value, inline=False)
        embed.add_field(name="Correct Solution", value=riddle['solution'], inline=False)
        embed.set_footer(text=f"From: {interaction.user.display_name}", icon_url=interaction.user.avatar.url)

        view = SolutionDecisionView(self.cog, self.riddle_id, interaction.user, self.solution_input.value)
        await author.send(embed=embed, view=view)
        await interaction.response.send_message("Your solution has been submitted.", ephemeral=True)

class SolutionDecisionView(View):
    def __init__(self, cog, riddle_id, solver, solution_text):
        super().__init__(timeout=None)
        self.cog = cog
        self.riddle_id = riddle_id
        self.solver = solver
        self.solution_text = solution_text

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_riddle(self.riddle_id, winner=self.solver, proposed_solution=self.solution_text)
        await interaction.message.delete()

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()

class RiddleSelect(Select):
    def __init__(self, cog, options):
        super().__init__(placeholder="Select a riddle", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        riddle_id = self.values[0]
        riddle = self.cog.riddles[riddle_id]
        view = ManageRiddleView(self.cog, riddle_id)
        await interaction.response.send_message(f"Managing riddle {riddle_id}.", view=view, ephemeral=True)

class WinnerSelect(Select):
    def __init__(self, cog, riddle_id, guild):
        options = [
            discord.SelectOption(label=member.display_name, value=str(member.id))
            for member in guild.members[:25]
        ]
        super().__init__(placeholder="Select the winner", min_values=1, max_values=1, options=options)
        self.cog = cog
        self.riddle_id = riddle_id
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        winner_id = int(self.values[0])
        winner = self.guild.get_member(winner_id)
        if winner is None:
            await interaction.response.send_message("Winner not found.", ephemeral=True)
            return
        await self.cog.close_riddle(self.riddle_id, winner=winner)
        await interaction.response.send_message(f"Riddle {self.riddle_id} closed with winner {winner.mention}.", ephemeral=True)
        self.view.stop()

class ManageRiddleView(View):
    def __init__(self, cog, riddle_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.riddle_id = riddle_id

    @discord.ui.button(label="Close with Winner", style=discord.ButtonStyle.success)
    async def close_with_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WinnerModal(self.cog, self.riddle_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Close without Winner", style=discord.ButtonStyle.secondary)
    async def close_without_winner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_riddle(self.riddle_id)
        await interaction.response.send_message(f"Riddle {self.riddle_id} closed without a winner.", ephemeral=True)

    @discord.ui.button(label="Delete Riddle", style=discord.ButtonStyle.danger)
    async def delete_riddle(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddle = self.cog.riddles.get(self.riddle_id)
        if riddle:
            channel = self.cog.bot.get_channel(riddle['channel_id'])
            try:
                message = await channel.fetch_message(riddle['message_id'])
                await message.delete()
            except:
                pass
            del self.cog.riddles[self.riddle_id]
            self.cog.save_riddles()
            await interaction.response.send_message(f"Riddle {self.riddle_id} has been deleted.", ephemeral=True)
from discord.ui import Modal, TextInput

class WinnerModal(Modal):
    def __init__(self, cog, riddle_id):
        super().__init__(title="Gewinner auswählen")
        self.cog = cog
        self.riddle_id = riddle_id
        self.member_input = TextInput(
            label="Gib den Gewinner ein (Name oder ID)",
            placeholder="z.B. @Benutzer oder ID",
            style=discord.TextStyle.short,
            required=True,
            max_length=100
        )
        self.add_item(self.member_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Kein Server Kontext.", ephemeral=True)
            return

        input_str = self.member_input.value.strip()

        # Versuch Member anhand Mention, ID oder Name zu finden
        member = None
        if input_str.startswith("<@") and input_str.endswith(">"):
            member_id = input_str.replace("<@!", "").replace("<@", "").replace(">", "")
            member = guild.get_member(int(member_id))
        else:
            try:
                member = guild.get_member(int(input_str))
            except:
                pass
            if not member:
                member = discord.utils.find(
                    lambda m: m.display_name.lower() == input_str.lower() or m.name.lower() == input_str.lower(),
                    guild.members
                )

        if not member:
            await interaction.response.send_message("Mitglied nicht gefunden. Bitte versuche es erneut.", ephemeral=True)
            return

        await self.cog.close_riddle(self.riddle_id, winner=member)
        await interaction.response.send_message(f"Rätsel {self.riddle_id} wurde mit Gewinner {member.mention} geschlossen.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(RiddleCog(bot))
