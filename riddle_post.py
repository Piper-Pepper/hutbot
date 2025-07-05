import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import aiohttp
import re

RIDDLE_BIN_URL = "https://api.jsonbin.io/v3/b/685442458a456b7966b13207"  # Rätsel-Bin
SOLVED_BIN_URL = "https://api.jsonbin.io/v3/b/686699c18960c979a5b67e34"  # Lösungen-Bin
API_KEY = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
HEADERS = {"X-Master-Key": API_KEY}

RIDDLE_CHANNEL_ID = 1346843244067160074
VOTE_CHANNEL_ID = 1381754826710585527
RIDDLE_ROLE = 1380610400416043089

class RiddleCloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(emoji="🔒", style=discord.ButtonStyle.danger, custom_id="riddle_close")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Hole die Daten der aktuellen Rätselfrage
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                data = await response.json()
                riddle_data = data.get("record", {})

        # Wenn keine Rätselfrage vorhanden, breche ab
        if not riddle_data.get("text"):
            await interaction.followup.send("❌ No active riddle to close.", ephemeral=True)
            return

        # Erstelle das "Closed"-Embed mit Bild
        solution_url = riddle_data.get("solution-url", "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg")
        closed_embed = discord.Embed(
            title="🔒 Riddle Closed",
            description="Sadly, nobody could solve the Riddle in time...",
            color=discord.Color.red()
        )
        closed_embed.add_field(name="🧩 Riddle", value=riddle_data.get("text", "*Unknown*"), inline=False)
        closed_embed.add_field(name="✅ Correct Solution", value=riddle_data.get("solution", "*None*"), inline=False)
        closed_embed.add_field(name="🏆 Award", value=riddle_data.get("award", "*None*"), inline=False)
        closed_embed.set_image(url=solution_url)
        closed_embed.set_footer(text=f"Guild: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        # Sende das Embed in den Rätselkanal
        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(content="<@&1380610400416043089>", embed=closed_embed)

        # Setze alle Rätselfelder auf Null
        await self.clear_riddle_data()

        await interaction.followup.send("✅ The riddle has been closed, and all data has been cleared.", ephemeral=True)

    # Methode zum Zurücksetzen der Rätselfelder
    async def clear_riddle_data(self):
        empty = {"text": None, "solution": None, "award": None, "image-url": None, "solution-url": None, "button-id": None}
        async with aiohttp.ClientSession() as session:
            await session.put(RIDDLE_BIN_URL, json={"record": empty}, headers=HEADERS)

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
        
        # Get submitter from hidden field
        submitter_id_str = get_field_value(embed, "🆔 User ID")
        submitter_id = int(submitter_id_str) if submitter_id_str and submitter_id_str.isdigit() else interaction.user.id
        submitter = await interaction.client.fetch_user(submitter_id)

        # Get solution image from riddle bin
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                data = await response.json()
                solution_url = data.get("record", {}).get("solution-url", "")

        if not solution_url or not solution_url.startswith("http"):
            solution_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

        solved_embed = discord.Embed(
            title="🎉 Riddle Solved!",
            description=f"**{submitter.mention}** solved the riddle!",
            color=discord.Color.green()
        )
        solved_embed.set_author(name=str(submitter), icon_url=submitter.display_avatar.url)
        solved_embed.add_field(name="🧩 Riddle", value=riddle_text or "*Unknown*", inline=False)
        solved_embed.add_field(name="🔍 Proposed Solution", value=user_solution or "*None*", inline=False)
        solved_embed.add_field(name="✅ Correct Solution", value=correct_solution or "*None*", inline=False)
        solved_embed.add_field(name="🏆 Award", value=award or "*None*", inline=False)
        solved_embed.set_image(url=solution_url)
        solved_embed.set_footer(text=f"Guild: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)


        # Mention 
        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(content=f"<@&1380610400416043089> {submitter.mention}", embed=solved_embed)

        await self.clear_riddle_data()
        await self.update_user_riddle_count(submitter.id)  # Wichtig: submitter.id, nicht interaction.user.id

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

RIDDLE_ROLE = 1380610400416043089  # fest definierte Rolle

import re

import aiohttp

class VoteFailButton(discord.ui.Button):
    def __init__(self):
        super().__init__(emoji="👎", style=discord.ButtonStyle.danger, custom_id="riddle_downvote")

    def extract_role_id(self, role_str: str):
        if role_str.isdigit():
            return int(role_str)
        match = re.search(r'<@&(\d+)>', role_str)
        if match:
            return int(match.group(1))
        match = re.search(r'\((\d+)\)', role_str)
        if match:
            return int(match.group(1))
        return None

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

        # 🕵️‍♂️ Hole Einreicher-ID aus verstecktem Feld
        submitter_id_str = get_field_value(embed, "🆔 User ID")
        submitter_id = int(submitter_id_str) if submitter_id_str and submitter_id_str.isdigit() else interaction.user.id
        submitter = await interaction.client.fetch_user(submitter_id)

        # ❌ Erstelle das „Fehlgeschlagen“-Embed mit dem echten Einreicher
        failed_embed = discord.Embed(
            title="❌ Riddle Not Solved!",
            description=f"**{submitter.mention}**'s solution was incorrect.",
            color=discord.Color.red()
        )
        failed_embed.set_author(name=str(submitter), icon_url=submitter.display_avatar.url)
        failed_embed.add_field(name="🧩 Riddle", value=riddle_text or "*Unknown*", inline=False)
        failed_embed.add_field(name="🔍 Proposed Solution", value=user_solution or "*None*", inline=False)
        failed_embed.add_field(
            name="❌ Sadly, the submitted solution was not correct.",
            value="*Better luck next time!*",
            inline=False
        )

        # --- JSON Daten nochmal laden, um button-id aus "button-id" zu holen ---
        button_role_id = None
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as response:
                    if response.status == 200:
                        data = await response.json()
                        record = data.get("record", {})
                        button_role_id_str = record.get("button-id", "")
                        button_role_id = self.extract_role_id(button_role_id_str)
                    else:
                        await interaction.followup.send("❌ Could not load riddle data from JSONBin.", ephemeral=True)
                        return
            except aiohttp.ClientError as e:
                await interaction.followup.send(f"❌ Network error while loading riddle data: {e}", ephemeral=True)
                return

        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            # Pings bauen
            content_parts = [f"<@&{RIDDLE_ROLE}>", interaction.user.mention]
            if button_role_id:
                content_parts.append(f"<@&{button_role_id}>")
            content = " ".join(content_parts)

            await riddle_channel.send(
                content=content,
                embed=failed_embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True)
            )

        # 💣 Lösche Original-Vote-Message
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
            description=f"{riddle.get('text', 'No riddle')}",
            color=discord.Color.gold()
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🧠 User's Answer", value=self.solution.value or "*Empty*", inline=False)
        embed.add_field(name="✅ Correct Solution", value=riddle.get("solution", "*Not provided*"), inline=False)
        embed.add_field(name="🆔 User ID", value=str(interaction.user.id), inline=False)  # NEU!

        await channel.send(embed=embed, view=VoteButtons())
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

class RiddleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SubmitButtonView())
        bot.add_view(VoteButtons())

    @app_commands.command(name="riddle_close", description="Close the current riddle and mark it as unsolved.")
    async def riddle_close(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Hole die Daten des aktuellen Rätsels
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                data = await response.json()
                riddle_data = data.get("record", {})

        # Wenn kein Rätsel existiert, breche ab
        if not riddle_data.get("text"):
            await interaction.followup.send("❌ No active riddle to close.", ephemeral=True)
            return
        # Hole das Bild aus den Riddle-Daten oder setze das Standardbild, falls nicht vorhanden
        solution_url = riddle_data.get("solution-url")
        # Falls keine solution-url vorhanden ist oder ungültig, setze das Standardbild
        if not solution_url or not solution_url.startswith("http"):
            solution_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

        # Erstelle das "Closed"-Embed mit Bild
        closed_embed = discord.Embed(
            title="🔒 Riddle Closed",
            description="Sadly, nobody could solve the Riddle in time...",
            color=discord.Color.red()
        )
        closed_embed.add_field(name="🧩 Riddle", value=riddle_data.get("text", "*Unknown*"), inline=False)
        closed_embed.add_field(name="✅ Correct Solution", value=riddle_data.get("solution", "*None*"), inline=False)
        closed_embed.add_field(name="🏆 Award", value=riddle_data.get("award", "*None*"), inline=False)
        closed_embed.set_image(url=solution_url)
        closed_embed.set_footer(text=f"Guild: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        # Sende das Embed in den Rätselkanal
        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(content="<@&1380610400416043089>", embed=closed_embed)

        # Sende das Embed in den Rätselkanal
        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(content="<@&1380610400416043089>", embed=closed_embed)

        # Setze alle Rätseldaten auf Null
        await self.clear_riddle_data()

        await interaction.followup.send("✅ The riddle has been closed, and all data has been cleared.", ephemeral=True)

    # Methode zum Zurücksetzen der Rätselfelder
    async def clear_riddle_data(self):
        empty = {"text": None, "solution": None, "award": None, "image-url": None, "solution-url": None, "button-id": None}
        async with aiohttp.ClientSession() as session:
            await session.put(RIDDLE_BIN_URL, json={"record": empty}, headers=HEADERS)

    @app_commands.command(
        name="riddle_post",
        description="Post the current riddle in a selected channel."
    )
    @app_commands.describe(
        ping_role="Optional role to ping along with the riddle group"
    )
    async def riddle_post(
        self,
        interaction: discord.Interaction,
        ping_role: Optional[discord.Role] = None  # 👈 neues optionales Feld
    ):

        await interaction.response.defer(ephemeral=True)
        # Standard‑Ping für die Riddle‑Gruppe
        content = "<@&1380610400416043089>"

        # Optional zusätzliche Rolle anhängen
        if ping_role:
            content += f" {ping_role.mention}"

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

        riddle_channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            allowed_mentions = discord.AllowedMentions(roles=True, users=False, everyone=False)
            await riddle_channel.send(
                content=content,
                embed=embed,
                view=SubmitButtonView(),
                allowed_mentions=allowed_mentions
            )

            await interaction.followup.send(f"✅ Riddle posted to {riddle_channel.mention}!", ephemeral=True)

# Utility Functions
def get_field_value(embed: discord.Embed, field_name: str):
    for field in embed.fields:
        if field.name.strip().startswith(field_name.strip()):
            return field.value
    return None

def extract_from_embed(desc: str):
    if desc and "> **Riddle:** " in desc:
        return desc.split("> **Riddle:** ")[-1]
    return desc or ""

async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCog(bot))
