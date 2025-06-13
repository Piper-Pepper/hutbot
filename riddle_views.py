import discord
from discord.ui import View, Button, Modal, TextInput, Select
from discord.utils import find


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
        solution_text = self.solution_input.value.replace('\\n', '\n')
        riddle = self.cog.riddles[self.riddle_id]
        channel_id = riddle['channel_id']
        author = await self.cog.bot.fetch_user(riddle['author_id'])

        embed = discord.Embed(
            title=f"\U0001F4DD Solution Proposal for Riddle {self.riddle_id}",
            description=riddle['text'],
            color=discord.Color.blue()
        )
        embed.add_field(name="Proposed Solution", value=solution_text, inline=False)
        embed.add_field(name="Correct Solution", value=riddle['solution'], inline=False)
        embed.set_footer(text=f"From: {interaction.user.display_name}", icon_url=interaction.user.avatar.url)

        # Gleiche View-Instanz für beide Nachrichten
        view = SolutionDecisionView(self.cog, self.riddle_id, interaction.user, solution_text)

        # DM an den Rätsel-Autor
        dm_message = await author.send(embed=embed, view=view)
        view.messages.append(dm_message)

        # Log in den festgelegten Channel posten
        log_channel = self.cog.bot.get_channel(1381754826710585527)
        log_message = await log_channel.send(embed=embed, view=view)
        view.messages.append(log_message)

        await interaction.response.send_message("Your solution has been submitted.", ephemeral=True)



class SolutionDecisionView(View):
    def __init__(self, cog, riddle_id, solver, solution_text):
        super().__init__(timeout=None)
        self.cog = cog
        self.riddle_id = riddle_id
        self.solver = solver
        self.solution_text = solution_text
        self.messages = []  # Nachrichten, die synchronisiert werden

    async def disable_all_messages(self, status_text):
        for message in self.messages:
            await message.edit(content=status_text, view=None)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_riddle(self.riddle_id, winner=self.solver, proposed_solution=self.solution_text)
        await self.disable_all_messages("✅ Solution accepted.")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        riddle = self.cog.riddles[self.riddle_id]
        solver = self.solver  # User, der die Lösung eingereicht hat
        
        embed = discord.Embed(
            title="Riddle Solution Rejected",
            description=f"**{solver.display_name}**",
            color=discord.Color.red()
        )
        embed.set_thumbnail(url=solver.avatar.url if solver.avatar else None)
        embed.add_field(name="Riddle Text", value=riddle['text'], inline=False)
        embed.add_field(name="Suggested Solution", value=self.solution_text, inline=False)
        embed.add_field(name="Result", value="The answer was not correct.", inline=False)
        
        channel = self.cog.bot.get_channel(riddle['channel_id'])
        if channel:
            await channel.send(content=solver.mention, embed=embed)
        
        await self.disable_all_messages("❌ Solution rejected.")




class RiddleSelect(Select):
    def __init__(self, cog, options):
        super().__init__(placeholder="Select a riddle", min_values=1, max_values=1, options=options)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        riddle_id = self.values[0]
        riddle = self.cog.riddles[riddle_id]
        view = ManageRiddleView(self.cog, riddle_id)
        await interaction.response.send_message(f"Managing riddle {riddle_id}.", view=view, ephemeral=True)


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


class WinnerModal(Modal):
    def __init__(self, cog, riddle_id):
        super().__init__(title="Select Winner")
        self.cog = cog
        self.riddle_id = riddle_id
        self.member_input = TextInput(
            label="Enter the winner (Name or ID)",
            placeholder="e.g. @User or ID",
            style=discord.TextStyle.short,
            required=True,
            max_length=100
        )
        self.add_item(self.member_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("No server context.", ephemeral=True)
            return

        input_str = self.member_input.value.strip()

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
                member = find(lambda m: m.display_name.lower() == input_str.lower() or m.name.lower() == input_str.lower(), guild.members)

        if not member:
            await interaction.response.send_message("Member not found. Please try again.", ephemeral=True)
            return

        await self.cog.close_riddle(self.riddle_id, winner=member)
        await interaction.response.send_message(f"Riddle {self.riddle_id} closed with winner {member.mention}.", ephemeral=True)
