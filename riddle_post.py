import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

RIDDLE_BIN_URL = "https://api.jsonbin.io/v3/b/685442458a456b7966b13207"  # Rätsel-Bin
SOLVED_BIN_URL = "https://api.jsonbin.io/v3/b/686699c18960c979a5b67e34"  # Lösungen-Bin
API_KEY = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
HEADERS = {"X-Master-Key": API_KEY}

RIDDLE_CHANNEL_ID = 1349697597232906292
VOTE_CHANNEL_ID = 1381754826710585527

class RoleSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="None", value="none"),
            discord.SelectOption(label="Role A", value="role_a"),
            discord.SelectOption(label="Role B", value="role_b"),
            # Add other roles here as needed
        ]
        super().__init__(placeholder="Select an additional role to mention", min_values=0, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Store the selected role (none or one of the roles)
        self.view.selected_role = self.values[0] if self.values else "none"
        await interaction.response.defer(ephemeral=True)

class VoteButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton())
        self.add_item(VoteFailButton())

class VoteSuccessButton(discord.ui.Button):
    def __init__(self):
        super().__init__(emoji="👍", style=discord.ButtonStyle.success, custom_id="riddle_upvote")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if not embed:
            await interaction.followup.send("❌ Couldn't find the original riddle data.", ephemeral=True)
            return

        riddle_text = extract_from_embed(embed.description)
        user_solution = get_field_value(embed, "🧠 User's Answer")
        correct_solution = get_field_value(embed, "✅ Correct Solution")
        award = get_field_value(embed, "🏆 Award")

        # Get solution image from riddle bin
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                data = await response.json()
                solution_url = data.get("record", {}).get("solution-url", "")

        if not solution_url or not solution_url.startswith("http"):
            solution_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

        solved_embed = discord.Embed(
            title="🎉 Riddle Solved!",
            description=f"**{interaction.user.mention}** solved the riddle!",
            color=discord.Color.green()
        )
        solved_embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        solved_embed.add_field(name="🧩 Riddle", value=riddle_text or "*Unknown*", inline=False)
        solved_embed.add_field(name="🔍 Proposed Solution", value=user_solution or "*None*", inline=False)
        solved_embed.add_field(name="✅ Correct Solution", value=correct_solution or "*None*", inline=False)
        solved_embed.add_field(name="🏆 Award", value=award or "*None*", inline=False)
        solved_embed.set_image(url=solution_url)
        solved_embed.set_footer(text=f"Guild: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(content=f"<@&1380610400416043089> {interaction.user.mention}", embed=solved_embed)

        await self.clear_riddle_data()
        await self.update_user_riddle_count(interaction.user.id)

        # 🔥 Delete the original message with the buttons
        try:
            await message.delete()
        except discord.HTTPException:
            print("❌ Failed to delete the solution message.")

        await interaction.followup.send("✅ Marked as solved, riddle data cleared, and user riddle count updated!", ephemeral=True)

    async def clear_riddle_data(self):
        empty = {
            "text": None,
            "solution": None,
            "award": None,
            "image-url": None,
            "solution-url": None,
            "button-id": None
        }
        async with aiohttp.ClientSession() as session:
            await session.put(RIDDLE_BIN_URL, json={"record": empty}, headers=HEADERS)

    async def update_user_riddle_count(self, user_id: int):
        async with aiohttp.ClientSession() as session:
            async with session.get(SOLVED_BIN_URL + "/latest", headers=HEADERS) as response:
                data = await response.json()
                users = data.get("record", {})
                uid = str(user_id)
                if uid in users:
                    users[uid]["solved_riddles"] += 1
                else:
                    users[uid] = {"solved_riddles": 1}
            await session.put(SOLVED_BIN_URL, json={"record": users}, headers=HEADERS)

class VoteFailButton(discord.ui.Button):
    def __init__(self):
        super().__init__(emoji="👎", style=discord.ButtonStyle.danger, custom_id="riddle_downvote")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if not embed:
            await interaction.followup.send("❌ Couldn't find the original riddle data.", ephemeral=True)
            return

        riddle_text = extract_from_embed(embed.description)
        user_solution = get_field_value(embed, "🧠 User's Answer")
        correct_solution = get_field_value(embed, "✅ Correct Solution")

        failed_embed = discord.Embed(
            title="❌ Riddle Not Solved!",
            description=f"**{interaction.user.mention}**'s solution was incorrect.",
            color=discord.Color.red()
        )
        failed_embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        failed_embed.add_field(name="🧩 Riddle", value=riddle_text or "*Unknown*", inline=False)
        failed_embed.add_field(name="🔍 Proposed Solution", value=user_solution or "*None*", inline=False)
        failed_embed.add_field(name="❌ Sadly, the submitted solution was not correct.", value="*Better luck next time!*", inline=False)

        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(embed=failed_embed)

        # 💣 Delete the original vote message
        try:
            await message.delete()
        except discord.HTTPException:
            print("❌ Failed to delete the vote message.")

        await interaction.followup.send("❌ Marked as incorrect!", ephemeral=True)

class SubmitSolutionModal(discord.ui.Modal, title="💡 Submit Your Solution"):
    solution = discord.ui.TextInput(label="Your Answer", style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                riddle = (await response.json()).get("record", {})

        channel = interaction.client.get_channel(VOTE_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("❌ Could not find the submission channel.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📜 New Solution Submitted!",
            description=f"> **Riddle:** {riddle.get('text', 'No riddle')}",
            color=discord.Color.gold()
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🧠 User's Answer", value=self.solution.value or "*Empty*", inline=False)
        embed.add_field(name="✅ Correct Solution", value=riddle.get("solution", "*Not provided*"), inline=False)

        # Check if a role is selected and mention it
        mentioned_roles = "<@&1380610400416043089>"  # Always mention this role
        if interaction.view.selected_role == "role_a":
            mentioned_roles += " <@&ROLE_A_ID>"  # Replace with actual role ID
        elif interaction.view.selected_role == "role_b":
            mentioned_roles += " <@&ROLE_B_ID>"  # Replace with actual role ID

        await channel.send(embed=embed, content=mentioned_roles, view=VoteButtons())
        await interaction.followup.send("✅ Your answer has been submitted!", ephemeral=True)

class SubmitButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="💡 Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SubmitSolutionModal())

class SubmitButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SubmitButton())
        self.add_item(RoleSelect())  # Add the role select dropdown

class RiddleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SubmitButtonView())

    @app_commands.command(name="riddle_post", description="Post the current riddle in a selected channel.")
    async def riddle_post(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Get the current riddle data
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                if response.status != 200:
                    await interaction.followup.send(f"❌ Error loading riddle: {response.status}", ephemeral=True)
                    return
                riddle = (await response.json()).get("record", {})

        if not riddle.get("text") or not riddle.get("solution"):
            await interaction.followup.send("❌ There is currently no active riddle.", ephemeral=True)
            return

        image_url = riddle.get("image-url") or "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"
        embed = discord.Embed(
            title="Goon Hut Riddle of the Day",
            description=f">{riddle.get('text', 'No text')}",
            color=discord.Color.blurple()
        )
        embed.add_field(name="🏆 Award", value=riddle.get("award", "None"), inline=False)
        embed.set_image(url=image_url)
        embed.set_footer(text=f"{interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        # Check if a role is selected and mention it
        mentioned_roles = "<@&1380610400416043089>"  # Always mention this role
        if interaction.view.selected_role == "role_a":
            mentioned_roles += " <@&ROLE_A_ID>"  # Replace with actual role ID
        elif interaction.view.selected_role == "role_b":
            mentioned_roles += " <@&ROLE_B_ID>"  # Replace with actual role ID

        riddle_channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(content=mentioned_roles, embed=embed, view=SubmitButtonView())
            await interaction.followup.send(f"✅ Riddle posted to {riddle_channel.mention}!", ephemeral=True)

# 🚀 Setup function
async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCog(bot))
