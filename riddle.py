import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
from discord import Interaction

import aiohttp
import logging
from typing import Optional

# 🔐 JSONBin Configuration
JSONBIN_BIN_ID = "685442458a456b7966b13207"
SOLVED_BIN_ID = "686699c18960c979a5b67e34"
SOLVED_BIN_URL = f"https://api.jsonbin.io/v3/b/{SOLVED_BIN_ID}"
JSONBIN_API_KEY = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
JSONBIN_BASE_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}


from discord.ui import View, Button
from discord import Interaction

class ChampionsView(View):
    def __init__(self, entries, page=0, guild: Optional[discord.Guild] = None):
        super().__init__(timeout=60)
        self.entries = entries
        self.page = page
        self.max_page = (len(entries) - 1) // 10
        self.guild = guild

        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.max_page

    async def get_page_embed(self):
        start = self.page * 10
        end = start + 10
        page_entries = self.entries[start:end]

        embed = discord.Embed(
            title="🏆 Riddle Champions",
            description=f"Page {self.page + 1} of {self.max_page + 1}",
            color=discord.Color.gold()
        )

        if not page_entries:
            embed.description = "No data available."
            return embed

        # 👑 Top User mit Avatar
        top_user_id = page_entries[0][0]
        top_user = None

        if self.guild:
            try:
                top_user = await self.guild.fetch_member(top_user_id)
            except discord.NotFound:
                try:
                    top_user = await self.guild._state.client.fetch_user(top_user_id)  # 👈 Fallback!
                except discord.HTTPException:
                    pass

        if top_user:
            display_name = getattr(top_user, "display_name", top_user.name)
            avatar_url = top_user.display_avatar.replace(size=1024).url  # 👈 Großes Avatarbild

            embed.set_author(
                name=f"Top: {top_user.name} ({display_name})",
                icon_url=top_user.display_avatar.replace(size=128).url  # 👈 Kleiner Avatar oben
            )
            embed.set_image(url=avatar_url)  # 👑 Großes Bild unten
        else:
            embed.set_author(
                name="Top: Unknown User",
                icon_url=None
            )

        # 🧾 Einträge pro Seite
        for i, (user_id, solved) in enumerate(page_entries, start=start + 1):
            name = f"<@{user_id}>"
            display_name = f"<Unknown>"

            if self.guild:
                try:
                    member = await self.guild.fetch_member(user_id)
                except discord.NotFound:
                    try:
                        member = await self.guild._state.client.fetch_user(user_id)
                    except discord.HTTPException:
                        member = None

                if member:
                    display_name = f"{member.name} ({getattr(member, 'display_name', member.name)})"
                    name = member.mention

            embed.add_field(
                name=f"**{i}.** {display_name}",
                value=f"{name}\nSolved: {solved}",
                inline=False
            )

        # 🏰 Footer mit Gilde
        if self.guild:
            embed.set_footer(
                text=f"Guild: {self.guild.name}",
                icon_url=self.guild.icon.url if self.guild.icon else None
            )

        return embed


    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: Interaction, button: Button):
        if self.page > 0:
            self.page -= 1
            self.prev.disabled = self.page <= 0
            self.next.disabled = False
            embed = await self.get_page_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: Interaction, button: Button):
        if self.page < self.max_page:
            self.page += 1
            self.next.disabled = self.page >= self.max_page
            self.prev.disabled = False
            embed = await self.get_page_embed()
            await interaction.response.edit_message(embed=embed, view=self)



logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 📤 Modal for Riddle Editing
class RiddleEditModal(Modal, title="Edit Riddle"):
    def __init__(self, data, guild: discord.Guild):
        super().__init__()
        self.guild = guild

        self.text = TextInput(label="Text", default=data.get("text", ""), required=True, style=discord.TextStyle.paragraph)
        self.solution = TextInput(label="Solution", default=data.get("solution", ""), required=True)
        self.award = TextInput(label="Award", default=data.get("award", ""), required=False)
        self.image_url = TextInput(label="Image URL", default=data.get("image-url", ""), required=False)
        self.solution_url = TextInput(label="Solution Image URL", default=data.get("solution-url", ""), required=False)

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

        self.button_id = data.get("button-id", "")  # 👈 merken für später

    async def on_submit(self, interaction: discord.Interaction):
        updated_data = {
            "text": self.text.value,
            "solution": self.solution.value,
            "award": self.award.value,
            "image-url": self.image_url.value,
            "solution-url": self.solution_url.value,
            "button-id": self.button_id  # bleibt unverändert erhalten
        }

        logger.info(f"[Modal Submit] New Data: {updated_data}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=updated_data) as response:
                    if response.status == 200:
                        # 🎯 Footer-Antwort mit Erwähnung der Gruppe, falls gesetzt
                        group_note = f"\n🔖 Assigned Group: <@&{self.button_id}>" if self.button_id else ""
                        await interaction.response.send_message(f"✅ Riddle successfully updated!{group_note}", ephemeral=True)
                    else:
                        logger.error(f"Error saving: {response.status} – {await response.text()}")
                        await interaction.response.send_message(f"❌ Error saving: {response.status}", ephemeral=True)
            except aiohttp.ClientError as e:
                logger.exception("Network error while saving:")
                await interaction.response.send_message(f"❌ Network error: {e}", ephemeral=True)

class RiddleEditor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle", description="Load and edit the current riddle.")
    @app_commands.describe(mention="Optional group to tag (will be stored)")
    async def riddle(self, interaction: discord.Interaction, mention: Optional[discord.Role] = None):
        required_role_id = 1380610400416043089
        has_role = any(role.id == required_role_id for role in interaction.user.roles)

        if not has_role:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return

        logger.info(f"[Slash Command] /riddle by {interaction.user}")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as response:
                    if response.status != 200:
                        logger.error(f"Error loading: {response.status} – {await response.text()}")
                        await interaction.response.send_message("❌ Error loading the riddle.", ephemeral=True)
                        return

                    data = await response.json()
                    record = data.get("record", {})

                    if not record:
                        await interaction.response.send_message("❌ No riddle data found.", ephemeral=True)
                        return

                    if mention:
                        logger.info(f"Saving mention role {mention.name} ({mention.id}) as button-id")
                        record["button-id"] = str(mention.id)
                        async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=record) as update_response:
                            if update_response.status != 200:
                                await interaction.response.send_message("❌ Failed to store role mention.", ephemeral=True)
                                return

                    modal = RiddleEditModal(data=record, guild=interaction.guild)
                    await interaction.response.send_modal(modal)

            except aiohttp.ClientError as e:
                logger.exception("Network error while loading:")
                await interaction.response.send_message(f"❌ Network error while loading: {e}", ephemeral=True)


    @app_commands.command(name="riddle_champ", description="Show the top users by solved riddles.")
    @app_commands.describe(visible="Show publicly in channel or only to you (default: False)")
    async def riddle_champ(self, interaction: discord.Interaction, visible: Optional[bool] = False):
        """Show leaderboard of top users by solved riddles."""

        await interaction.response.defer(ephemeral=not visible)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(SOLVED_BIN_URL + "/latest", headers=HEADERS) as resp:
                    if resp.status != 200:
                        await interaction.followup.send("❌ Failed to load solved riddles data.", ephemeral=True)
                        return
                    data = await resp.json()
            except aiohttp.ClientError as e:
                await interaction.followup.send(f"❌ Network error: {e}", ephemeral=True)
                return

        # ⛏️ Direkt auf das JSON zugreifen, kein "record"
        raw_data = data.get("record", data)  # Falls doch mal ein record drin ist

        entries = []
        for uid, stats in raw_data.items():
            solved = stats.get("solved_riddles", 0)
            entries.append((int(uid), solved))

        entries.sort(key=lambda x: x[1], reverse=True)

        if not entries:
            await interaction.followup.send("No champions yet!", ephemeral=True)
            return

        view = ChampionsView(entries, guild=interaction.guild)
        embed = await view.get_page_embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=not visible)



# 🚀 Setup
async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))
