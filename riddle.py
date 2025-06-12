import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import datetime
import uuid

RIDDLE_CHANNEL_ID = 1346843244067160074
LOG_CHANNEL_ID = 1346843244067160074
REVIEW_CHANNEL_ID = 1381754826710585527
RIDDLE_ROLE_ID = 1380610400416043089

RIDDLE_FILE = "riddles.json"

DEFAULT_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1381375333491675217/idcard_small.png"

def load_riddles():
    if not os.path.exists(RIDDLE_FILE):
        return {}
    with open(RIDDLE_FILE, "r") as f:
        return json.load(f)

def save_riddles(riddles):
    with open(RIDDLE_FILE, "w") as f:
        json.dump(riddles, f, indent=2)

class SolutionDMView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success, custom_id="solution_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_footer(text="✅ Accepted", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        await interaction.message.edit(embed=embed, view=None)

        try:
            user_id = int(embed.fields[1].value.strip("<@!>"))
            user = interaction.client.get_user(user_id)
            if user:
                await user.send("🎉 Your solution was accepted! You will be contacted if you win.")
        except:
            pass

        await interaction.response.send_message("Accepted and noted.", ephemeral=True)

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger, custom_id="solution_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.set_footer(text="❌ Rejected", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        await interaction.message.edit(embed=embed, view=None)

        try:
            user_id = int(embed.fields[1].value.strip("<@!>"))
            user = interaction.client.get_user(user_id)
            if user:
                await user.send("😢 Your solution was rejected. Better luck next time!")
        except:
            pass

        await interaction.response.send_message("Rejected and noted.", ephemeral=True)

class SolutionChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success, custom_id="solution_channel_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_footer(text="✅ Accepted", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        await interaction.message.edit(embed=embed, view=None)

        try:
            user_id = int(embed.fields[1].value.strip("<@!>"))
            user = interaction.guild.get_member(user_id)
            if user:
                await user.send("🎉 Your solution was accepted! You will be contacted if you win.")
        except:
            pass

        await interaction.response.send_message("Accepted and noted.", ephemeral=True)

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger, custom_id="solution_channel_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.set_footer(text="❌ Rejected", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        await interaction.message.edit(embed=embed, view=None)

        try:
            user_id = int(embed.fields[1].value.strip("<@!>"))
            user = interaction.guild.get_member(user_id)
            if user:
                await user.send("😢 Your solution was rejected. Better luck next time!")
        except:
            pass

        await interaction.response.send_message("Rejected and noted.", ephemeral=True)

class SolutionModal(discord.ui.Modal, title="Submit your solution"):
    answer = discord.ui.TextInput(label="Your solution", style=discord.TextStyle.paragraph, required=True)

    def __init__(self, riddle_id, riddle_text, author_id):
        super().__init__()
        self.riddle_id = riddle_id
        self.riddle_text = riddle_text
        self.author_id = author_id

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"🧩 Solution for Riddle `{self.riddle_id}`",
            description=self.answer.value,
            color=discord.Color.orange(),
            timestamp=interaction.created_at
        )
        embed.add_field(name="Riddle", value=self.riddle_text, inline=False)
        embed.add_field(name="From", value=interaction.user.mention, inline=False)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar)

        author = interaction.client.get_user(self.author_id)
        if author:
            await author.send(embed=embed, view=SolutionDMView())

        review_channel = interaction.client.get_channel(REVIEW_CHANNEL_ID)
        if review_channel:
            copied_embed = embed.copy()
            await review_channel.send(embed=copied_embed, view=SolutionChannelView())

        await interaction.response.send_message("✅ Your solution has been sent!", ephemeral=True)

class RiddleView(discord.ui.View):
    def __init__(self, riddle_id, text, author_id):
        super().__init__(timeout=None)
        self.riddle_id = riddle_id
        self.text = text
        self.author_id = author_id

    @discord.ui.button(label="Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SolutionModal(self.riddle_id, self.text, self.author_id))

# (Der restliche Code bleibt wie von dir geliefert mit riddle_win, riddle_list, etc.)

async def setup(bot: commands.Bot):
    await bot.add_cog(Riddle(bot))
