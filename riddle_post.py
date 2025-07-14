import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import aiohttp
from datetime import datetime
import aiohttp
import re

API_KEY = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
HEADERS = {"X-Master-Key": API_KEY}
PUT_HEADERS = {**HEADERS, "Content-Type": "application/json"}

ARCHIVE_BIN_URL = "https://api.jsonbin.io/v3/b/6869a6fa8960c979a5b7c527"
RIDDLE_BIN_URL = "https://api.jsonbin.io/v3/b/685442458a456b7966b13207"  # Rätsel-Bin
SOLVED_BIN_URL = "https://api.jsonbin.io/v3/b/686699c18960c979a5b67e34"  # Lösungen-Bin

RIDDLE_CHANNEL_ID = 1349697597232906292
VOTE_CHANNEL_ID = 1381754826710585527
RIDDLE_ROLE = 1380610400416043089
REQUIRED_ROLE_ID = 1393762463861702787  # Only this role can use the riddle commands



def truncate_text(text: str, max_length: int = 75) -> str:
    """Kürzt den Text nach max_length Zeichen und fügt '[...]' hinzu."""
    if text and len(text) > max_length:
        return text[:max_length] + "[...]"
    return text


async def callback(self, interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    # 📦 Riddle laden
    async with aiohttp.ClientSession() as session:
        async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as resp:
            riddle_wrap = await resp.json()
            riddle_data = riddle_wrap.get("record", {})

    if not riddle_data.get("text"):
        await interaction.followup.send("❌ No active riddle to close.", ephemeral=True)
        return

    # 🔖 optionale Gruppenrolle
    button_role_id = riddle_data.get("button-id")
    mentions = [f"<@&{RIDDLE_ROLE}>"]
    if button_role_id:
        mentions.append(f"<@&{button_role_id}>")
    mention_text = " ".join(mentions)

    # 🛑 Closed‑Embed bauen
    solution_url = riddle_data.get("solution-url") or \
        "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

    # Lösung aufbereiten
    raw_solution = riddle_data.get("solution", "*None*")
    clean_solution, link = extract_link(raw_solution or "")
    solution_display = clean_solution or "*None*"
    if link:
        solution_display += f"\n🔗 [🧠**MORE**]({link})"

    closed_embed = (
        discord.Embed(
            title="🔒 Riddle Closed",
            description="Sadly, nobody could solve the Riddle in time...",
            color=discord.Color.red()
        )
        .add_field(name="🧩 Riddle", value=riddle_data.get("text", "*Unknown*"), inline=False)
        .add_field(name="✅ Correct Solution", value=solution_display, inline=False)
        .add_field(name="🏆 Award", value=riddle_data.get("award", "*None*"), inline=False)
        .set_image(url=solution_url)
        .set_footer(
            text=f"Guild: {interaction.guild.name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
    )

    # 📬 Einmalig posten
    riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
    if riddle_channel:
        await riddle_channel.send(content=mention_text, embed=closed_embed)

    # 📚 Neues Archiv-Item vorbereiten
    archive_entry = {
        "text": riddle_data.get("text", "*Unknown*"),
        "solution": riddle_data.get("solution", "*None*"),
        "date": datetime.utcnow().strftime("%Y-%m-%d")
    }
# 📤 An Archiv-Bin anhängen
    async with aiohttp.ClientSession() as session:
        # Hole die aktuellen Daten aus dem Archiv
        async with session.get(ARCHIVE_BIN_URL + "/latest", headers=HEADERS) as resp:
            archive_wrap = await resp.json()
            archive_list = archive_wrap if isinstance(archive_wrap, list) else []

        # Füge den neuen Eintrag zu den bestehenden hinzu
        archive_list.append(archive_entry)

        # Speichere das Archiv mit dem neuen Eintrag zurück, ohne "record"
        async with session.put(
            ARCHIVE_BIN_URL,
            headers=PUT_HEADERS,
            json=archive_list  # Direkt die Liste ohne "record"
        ) as put_resp:
            if put_resp.status == 200 or put_resp.status == 201:
                print(f"✅ Archived riddle added: {archive_entry}")
            else:
                print(f"❌ Failed to archive riddle: {put_resp.status}")

    # 🧹 Riddle‑Bin leeren
    await self.clear_riddle_data()

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

        # --- Daten aus dem Embed ziehen ---
        riddle_text = extract_from_embed(embed.description)
        user_solution = get_field_value(embed, "🧠 User's Answer")
        correct_solution = get_field_value(embed, "✅ Correct Solution")
        submitter_id_str = get_field_value(embed, "🆔 User ID")
        submitter_id = int(submitter_id_str) if submitter_id_str and submitter_id_str.isdigit() else interaction.user.id
        submitter = await interaction.client.fetch_user(submitter_id)

        # --- Infos aus Riddle‑Bin laden ---
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                data = await response.json()
                solution_url = data.get("record", {}).get("solution-url", "")
                award = data.get("record", {}).get("award", "*None*")
                button_id = data.get("record", {}).get("button-id")

        if not solution_url or not solution_url.startswith("http"):
            solution_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

        def extract_link(text):
            match = re.search(r"(https?://\S+)", text or "")
            if match:
                link = match.group(1)
                cleaned = text.replace(link, "").strip()
                return cleaned, link
            return text, None

        # --- Lösung formatieren ---
        clean_correct_solution, correct_link = extract_link(correct_solution)
        correct_display = clean_correct_solution or "*None*"
        if correct_link:
            correct_display += f"\n🔗 [🧠**MORE**]({correct_link})"

        # --- Mentions vorbereiten ---
        mention_ids = set()
        mentions = []

        if interaction.guild:
            # Feste Mod-Rolle
            fixed_role = interaction.guild.get_role(1380610400416043089)
            if fixed_role and fixed_role.id not in mention_ids:
                mentions.append(fixed_role.mention)
                mention_ids.add(fixed_role.id)

            # Button-ID-Rolle (wenn vorhanden)
            if button_id:
                role = interaction.guild.get_role(int(button_id))
                if role and role.id not in mention_ids:
                    mentions.append(role.mention)
                    mention_ids.add(role.id)

            # RIDDLE_ROLE
            riddle_role = interaction.guild.get_role(RIDDLE_ROLE) if isinstance(RIDDLE_ROLE, int) else RIDDLE_ROLE
            if riddle_role and riddle_role.id not in mention_ids:
                mentions.append(riddle_role.mention)
                mention_ids.add(riddle_role.id)

        # Submitter (der User ist eh keine Rolle, also keine ID-Kollision)
        if submitter:
            mentions.append(submitter.mention)

        content_msg = " ".join(mentions) + "\n🎉 Cock-gratulations💋!"


        # --- Embed zusammenbauen ---
        solved_embed = discord.Embed(
            title="🎉 Riddle Solved!",
            description=f"**{submitter.mention}** solved the riddle!",
            color=discord.Color.green()
        )
        solved_embed.set_author(name=str(submitter), icon_url=submitter.display_avatar.url)
        solved_embed.add_field(name="🧩 Riddle", value=truncate_text(riddle_text) or "*Unknown*", inline=False)
        solved_embed.add_field(name="🔍 Proposed Solution", value=user_solution or "*None*", inline=False)
        solved_embed.add_field(name="✅ Correct Solution", value=correct_display, inline=False)
        solved_embed.add_field(name="🏆 Award", value=award or "*None*", inline=False)
        solved_embed.set_image(url=solution_url)
        solved_embed.set_footer(
            text=f"Guild: {interaction.guild.name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )

        # --- Ursprungsrätsel im Channel finden und aktualisieren ---
        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            try:
                async for msg in riddle_channel.history(limit=200):
                    if not msg.embeds or not msg.components:
                        continue
                    original_embed = msg.embeds[0]
                    riddle_in_msg = extract_from_embed(original_embed.description or "")
                    if riddle_in_msg.strip().lower() == riddle_text.strip().lower():
                        print(f"✏️ Found matching riddle message: {msg.id}")

                        updated_embed = original_embed.copy()
                        solved_note = (
                            f"✅ This riddle was solved by {submitter.mention} "
                            f"with the correct solution:\n{clean_correct_solution or '*None*'}**"
                        )
                        if correct_link:
                            solved_note += f"\n🔗 [🧠**MORE**]({correct_link})"

                        updated_embed.add_field(name="✅ Solved", value=solved_note, inline=False)
                        await msg.edit(embed=updated_embed, view=None)
                        print("✅ Updated original riddle message and removed buttons.")
                        break
            except Exception as e:
                print(f"⚠️ Error while updating original riddle message: {e}")

        # --- Embed mit allen Mentions im Riddle-Channel posten ---
        if riddle_channel:
            await riddle_channel.send(
                content=content_msg,
                embed=solved_embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False)
            )

        # ---------- Aufräumen ----------
        await self.update_user_riddle_count(submitter.id)
        await self.clear_riddle_data()
        
        try:
            await message.delete()  # ursprüngliche Lösungs‑Nachricht (mit Vote‑Button) löschen
        except discord.HTTPException:
            print("❌ Failed to delete the solution message.")

        await interaction.followup.send(
            "✅ Marked as solved, updated the riddle post, removed buttons, and bumped the user’s solve count!",
            ephemeral=True
        )

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
        uid = str(user_id)
        XP_AWARD = 0  # Fallback-Wert

        async with aiohttp.ClientSession() as session:
            # 🥇 0. XP-Award aus Bin laden und extrahieren
            try:
                async with session.get(RIDDLE_BIN_URL, headers=HEADERS) as award_resp:
                    if award_resp.status == 200:
                        award_data = await award_resp.json()
                        raw_award = award_data.get("record", {}).get("award", "")
                        match = re.search(r"\d+", str(raw_award))
                        if match:
                            XP_AWARD = int(match.group())
                            print(f"🎁 XP_AWARD extracted: {XP_AWARD}")
                        else:
                            print("⚠️ No number found in award field.")
                    else:
                        print(f"❌ Failed to fetch award bin: {award_resp.status}")
            except Exception as e:
                print(f"❌ Exception while fetching XP_AWARD: {e}")

            # 🧾 1. User-Daten laden
            async with session.get(f"{SOLVED_BIN_URL}/latest", headers=HEADERS) as resp:
                data = await resp.json()
                users = data.get("record", {})

            # 🧠 2. Userdaten updaten oder anlegen
            if uid in users:
                users[uid]["solved_riddles"] += 1
                users[uid]["xp"] = users[uid].get("xp", 0) + XP_AWARD
            else:
                users[uid] = {
                    "solved_riddles": 1,
                    "xp": XP_AWARD
                }

            # 💾 3. Zurückschreiben
            async with session.put(SOLVED_BIN_URL, json=users, headers=HEADERS) as put_resp:
                if put_resp.status in (200, 201):
                    print(f"✅ Updated solved_riddles and xp for user {uid}")
                else:
                    print(f"❌ Failed to update user stats: {put_resp.status}")


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
        failed_embed.add_field(name="🧩 Riddle", value=truncate_text(riddle_text) or "*Unknown*", inline=False)
        failed_embed.add_field(name="🔍 Proposed Solution", value=user_solution or "*None*", inline=False)
        failed_embed.add_field(
            name="❌ Sadly, the submitted solution was not correct.",
            value="*Better luck next time!*",
            inline=False
        )

        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            # Rolle aus Embed/JSON (button-id)
            button_role_id_str = get_field_value(embed, "🔖 Assigned Group") or ""
            try:
                button_role_id = int(button_role_id_str)
            except ValueError:
                button_role_id = None

            # Content mit Rollen-Mentions und User-Mention
            mentions = [f"<@&{RIDDLE_ROLE}>", submitter.mention]
            if button_role_id:
                mentions.append(f"<@&{button_role_id}>")
            content = submitter.mention

            await riddle_channel.send(content=content, embed=failed_embed, allowed_mentions=discord.AllowedMentions(roles=True, users=True))

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
        embed.add_field(name="🆔 User ID", value=str(interaction.user.id), inline=False)

        # Unsichtbares Feld mit button-id (versteckt, inline=True macht es klein)
        button_id = riddle.get("button-id", "")
        if button_id:
            embed.add_field(name="🔖 Assigned Group", value=button_id, inline=True)

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

    from datetime import datetime

    @app_commands.command(name="riddle_close", description="Close the current riddle and mark it as unsolved.")
    @app_commands.checks.has_role(REQUIRED_ROLE_ID)
    async def riddle_close(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # 📦 Fetch current riddle data
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                data = await response.json()
                riddle_data = data.get("record", {})

        if not riddle_data.get("text"):
            await interaction.followup.send("❌ No active riddle to close.", ephemeral=True)
            return

        # 🧩 Build the "Closed" embed
        solution_url = riddle_data.get("solution-url") or \
            "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

        closed_embed = discord.Embed(
            title="🔒 Riddle Closed",
            description="Sadly, nobody could solve the Riddle in time...",
            color=discord.Color.red()
        )
        closed_embed.add_field(name="🧩 Riddle", value=riddle_data.get("text", "*Unknown*"), inline=False)
        closed_embed.add_field(name="✅ Correct Solution", value=riddle_data.get("solution", "*None*"), inline=False)
        closed_embed.add_field(name="🏆 Award", value=riddle_data.get("award", "*None*"), inline=False)
        closed_embed.set_image(url=solution_url)
        closed_embed.set_footer(
            text=f"Guild: {interaction.guild.name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )

        # 📬 Send to riddle channel
        riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(content=f"<@&{RIDDLE_ROLE}>", embed=closed_embed)

        # 🗃️ Archive the riddle before clearing
        archive_entry = {
            "text": riddle_data.get("text", "*Unknown*"),
            "solution": riddle_data.get("solution", "*None*"),
            "date": datetime.utcnow().strftime("%Y-%m-%d")
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(ARCHIVE_BIN_URL + "/latest", headers=HEADERS) as resp:
                archive_wrap = await resp.json()
                archive_list = archive_wrap.get("record", [])
                archive_list = archive_list if isinstance(archive_list, list) else []

            archive_list.append(archive_entry)

            await session.put(
                ARCHIVE_BIN_URL,
                headers=PUT_HEADERS,
                json=archive_list
            )

        # 🧹 Clear riddle data
        await self.clear_riddle_data()
        await interaction.followup.send("✅ The riddle has been closed, archived, and marked as unsolved.", ephemeral=True)

    # Methode zum Zurücksetzen der Rätselfelder
    async def clear_riddle_data(self):
        empty = {"text": None, "solution": None, "award": None, "image-url": None, "solution-url": None, "button-id": None}
        async with aiohttp.ClientSession() as session:
            await session.put(RIDDLE_BIN_URL, json={"record": empty}, headers=HEADERS)

    @app_commands.command(name="riddle_post", description="Post the current riddle in a selected channel.")
    @app_commands.checks.has_role(REQUIRED_ROLE_ID)
    @app_commands.describe(ping_role="Optional role to ping along with the riddle group")
    async def riddle_post(
        self,
        interaction: discord.Interaction,
        ping_role: Optional[discord.Role] = None  # 👈 optionales Ping-Rollen-Feld
    ):
        await interaction.response.defer(ephemeral=True)

        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                if response.status != 200:
                    await interaction.followup.send(f"❌ Error loading riddle: {response.status}", ephemeral=True)
                    return
                riddle = (await response.json()).get("record", {})

        if not riddle.get("text") or not riddle.get("solution"):
            await interaction.followup.send("❌ There is currently no active riddle.", ephemeral=True)
            return

        # Feste Riddle Role
        content_parts = [f"<@&{RIDDLE_ROLE}>"]

        # Geheime Rolle aus JSON (button-id)
        button_role_id_str = riddle.get("button-id", "")
        if button_role_id_str:
            try:
                button_role_id = int(button_role_id_str)
                content_parts.append(f"<@&{button_role_id}>")
            except ValueError:
                pass  # Ignoriere, wenn ungültige ID

        # Optionale ping_role aus Command-Parameter
        if ping_role:
            content_parts.append(ping_role.mention)

        content = " ".join(content_parts)

        image_url = riddle.get("image-url")
        if not image_url or not image_url.startswith("http"):
            image_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

        date_str = datetime.now().strftime("%Y/%m/%d")
        embed = discord.Embed(
            title=f"🧠Ms Pepper's 𝕲𝖔𝖔𝖓 𝕳𝖚𝖙 𝕽𝖎𝖉𝖉𝖑𝖊\n{date_str}",
            description=f"{riddle.get('text', 'No text')}",
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

    @app_commands.command(name="riddle_view", description="View the current riddle and winner preview (private).")
    @app_commands.checks.has_role(REQUIRED_ROLE_ID)
    async def riddle_view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Fetch the current riddle data
        async with aiohttp.ClientSession() as session:
            async with session.get(RIDDLE_BIN_URL + "/latest", headers=HEADERS) as response:
                if response.status != 200:
                    await interaction.followup.send("❌ Couldn't fetch riddle data.", ephemeral=True)
                    return
                riddle_data = (await response.json()).get("record", {})

        if not riddle_data.get("text"):
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        # ➕ Optional: Mention Group (falls vorhanden)
        mention_group = None
        button_id = riddle_data.get("button-id")
        if button_id and interaction.guild:
            role = interaction.guild.get_role(int(button_id))
            if role:
                mention_group = role.mention
            else:
                mention_group = f"(Role ID: {button_id})"

        # Image fallback
        image_url = riddle_data.get("image-url") or "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"
        if not image_url.startswith("http"):
            image_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"

        solution_url = riddle_data.get("solution-url") or image_url
        if not solution_url.startswith("http"):
            solution_url = image_url

        # 🧠 Riddle Embed
        date_str = datetime.now().strftime("%Y/%m/%d")
        riddle_embed = discord.Embed(
            title=f"🧠 Goon Hut ℝ𝕚𝕕𝕕𝕝𝕖 𝕠𝕗 𝕥𝕙𝕖 𝔻𝕒𝕪\n{date_str}",
            description=riddle_data.get("text", "*No text*"),
            color=discord.Color.blurple()
        )
        riddle_embed.add_field(name="🏆 Award", value=riddle_data.get("award", "None"), inline=False)
        if mention_group:
            riddle_embed.add_field(name="📣 Mention Group", value=mention_group, inline=False)
        riddle_embed.set_image(url=image_url)
        riddle_embed.set_footer(
            text=f"{interaction.guild.name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )

        # 🎉 Solved Embed
        solved_embed = discord.Embed(
            title="🎉 Riddle Solved!",
            description=f"**SomeUser** solved the riddle!",  # Platzhalter
            color=discord.Color.green()
        )
        solved_embed.set_author(name="User#1234", icon_url="https://cdn.discordapp.com/embed/avatars/0.png")
        solved_embed.add_field(name="🧩 Riddle", value=riddle_data.get("text", "*Unknown*"), inline=False)
        raw_solution = riddle_data.get("solution", "*None*")
        # Suche nach dem ersten http/https-Link im Lösungstext
        match = re.search(r"(https?://\S+)", raw_solution)
        solution_link = match.group(1) if match else None

        # Lösungstext ohne den Link
        cleaned_solution = raw_solution.replace(solution_link, "").strip() if solution_link else raw_solution

        # Lösung + optionaler Link im neuen Format
        proposed_value = cleaned_solution
        if solution_link:
            proposed_value += f"\n🔗 [🧠**MORE**]({solution_link})"

        solved_embed.add_field(name="🔍 Proposed Solution", value="*Right Solution*", inline=False)
        solved_embed.add_field(name="✅ Correct Solution", value=proposed_value or "*None*", inline=False)

        solved_embed.add_field(name="🏆 Award", value=riddle_data.get("award", "*None*"), inline=False)
        if mention_group:
            solved_embed.add_field(name="📣 Mention Group", value=mention_group, inline=False)
        solved_embed.set_image(url=solution_url)
        solved_embed.set_footer(
            text=f"Guild: {interaction.guild.name}",
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )

        # 📨 Antwort
        await interaction.followup.send(
            content="🧪 Here is your private riddle preview:",
            embeds=[riddle_embed, solved_embed],
            ephemeral=True
        )

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_errors.MissingRole):
            await interaction.response.send_message(
                "🚫 You don’t have permission to use this command.",
                ephemeral=True
            )
        else:
            raise error  # Alle anderen Fehler weiterhin durchreichen (damit du echte Bugs siehst)


async def on_riddle_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingRole):
        await interaction.response.send_message(
            "🚫 You don’t have permission to use this command.",
            ephemeral=True
        )
    else:
        raise error  # Nur weiterreichen, wenn's ein anderer Fehler ist


# Utility Functions

def extract_link(text: str) -> tuple[str, Optional[str]]:
    """Extracts a link from text and returns (text without link, link or None)."""
    match = re.search(r'(https?://\S+)', text)
    if match:
        link = match.group(1)
        clean_text = text.replace(link, "").strip()
        return clean_text, link
    return text, None


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
    bot.tree.on_error = on_riddle_command_error  # <<<<< WICHTIG!
