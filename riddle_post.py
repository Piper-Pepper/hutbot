import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

JSONBIN_BASE_URL = "https://api.jsonbin.io/v3/b/685442458a456b7966b13207"
API_KEY = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
HEADERS = {"X-Master-Key": API_KEY}

RIDDLE_CHANNEL_ID = 1346843244067160074  # Channel where the riddle and solved embed will be posted
VOTE_CHANNEL_ID = 1381754826710585527  # Channel where the vote buttons will appear


class VoteButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton())
        self.add_item(VoteFailButton())


class VoteSuccessButton(discord.ui.Button):
    def __init__(self):
        super().__init__(emoji="👍", style=discord.ButtonStyle.success, custom_id="riddle_upvote")

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if not embed:
            await interaction.response.send_message("❌ Couldn't find the original riddle data.", ephemeral=True)
            return

        # Extract info from original embed fields
        riddle_text = extract_from_embed(embed.description)
        user_solution = get_field_value(embed, "🧠 User's Answer")
        correct_solution = get_field_value(embed, "✅ Correct Solution")
        award = get_footer_value(embed)

        # Get the channel where the riddle was posted
        channel = interaction.message.channel

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

        # Send the solved embed to the **correct channel** (1346843244067160074)
        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(embed=solved_embed)

        await interaction.response.send_message("✅ Marked as solved!", ephemeral=True)


class VoteFailButton(discord.ui.Button):
    def __init__(self):
        super().__init__(emoji="👎", style=discord.ButtonStyle.danger, custom_id="riddle_downvote")

    async def callback(self, interaction: discord.Interaction):
        message = interaction.message
        embed = message.embeds[0] if message.embeds else None
        if not embed:
            await interaction.response.send_message("❌ Couldn't find the original riddle data.", ephemeral=True)
            return

        # Extract info from original embed fields
        riddle_text = extract_from_embed(embed.description)
        user_solution = get_field_value(embed, "🧠 User's Answer")
        correct_solution = get_field_value(embed, "✅ Correct Solution")

        # Get the channel where the riddle was posted
        channel = interaction.message.channel

        # Create "Riddle Solved" embed showing the incorrect solution
        failed_embed = discord.Embed(
            title="❌ Riddle Not Solved!",
            description=f"**{interaction.user.mention}**'s solution was incorrect.",
            color=discord.Color.red()
        )
        failed_embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        failed_embed.add_field(name="🧩 Riddle", value=riddle_text or "*Unknown*", inline=False)
        failed_embed.add_field(name="🔍 Proposed Solution", value=user_solution or "*None*", inline=False)
        failed_embed.add_field(name="❌ Sadly, the submitted solution was not correct.", value="*Better luck next time!*", inline=False)

        # Send the failed embed to the **correct channel** (1346843244067160074)
        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(embed=failed_embed)

        await interaction.response.send_message("❌ Marked as incorrect!", ephemeral=True)


# Utility helpers
def get_field_value(embed: discord.Embed, field_name: str):
    for field in embed.fields:
        if field.name.strip().startswith(field_name.strip()):
            return field.value
    return None

def get_footer_value(embed: discord.Embed):
    return embed.footer.text.replace("Award: ", "") if embed.footer else ""

def extract_from_embed(desc: str):
    # Tries to extract riddle text from description line that looks like: "> **Riddle:** <text>"
    if desc and "> **Riddle:** " in desc:
        return desc.split("> **Riddle:** ")[-1]
    return desc or ""


class SubmitSolutionModal(discord.ui.Modal, title="💡 Submit Your Solution"):
    solution = discord.ui.TextInput(label="Your Answer", style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        # Fetch riddle data
        async with aiohttp.ClientSession() as session:
            async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as response:
                data = await response.json()
                riddle = data.get("record", {})

        # Submit solution to the designated channel
        channel = interaction.client.get_channel(VOTE_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❌ Could not find the submission channel.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📝 New Solution Submitted!",
            description=f"> **Riddle:** {riddle.get('text', 'No riddle')}",
            color=discord.Color.gold()
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🧠 User's Answer", value=self.solution.value or "*Empty*", inline=False)
        embed.add_field(name="✅ Correct Solution", value=riddle.get("solution", "*Not provided*"), inline=False)

        await channel.send(embed=embed, view=VoteButtons())
        await interaction.response.send_message("✅ Your answer has been submitted!", ephemeral=True)


class SubmitButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="💡 Submit Solution", style=discord.ButtonStyle.primary, custom_id="submit_solution")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SubmitSolutionModal())


class SubmitButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SubmitButton())


class RiddleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SubmitButtonView())
        bot.add_view(VoteButtons())

    @app_commands.command(name="riddle_post", description="Post the current riddle in a selected channel.")
    async def riddle_post(self, interaction: discord.Interaction):
        await interaction.response.send_message("⏳ Loading riddle...", ephemeral=True)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as response:
                    if response.status != 200:
                        await interaction.followup.send(f"❌ Error loading riddle: {response.status}", ephemeral=True)
                        return

                    data = await response.json()
                    riddle = data.get("record", {})

                    # Create the riddle embed
                    embed = discord.Embed(
                        title="🧩 Riddle Time!",
                        description=riddle.get("text", "No riddle text."),
                        color=discord.Color.blurple()
                    )
                    if riddle.get("image_url"):
                        embed.set_image(url=riddle["image_url"])
                    embed.set_footer(text=f"Award: {riddle.get('award', 'None')}")

                    # Post the riddle to the designated channel
                    riddle_channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
                    if riddle_channel:
                        await riddle_channel.send(embed=embed, view=SubmitButtonView())
                        await interaction.followup.send(f"✅ Riddle posted to {riddle_channel.mention}!", ephemeral=True)

            except aiohttp.ClientError as e:
                await interaction.followup.send(f"❌ Network error: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCog(bot))
