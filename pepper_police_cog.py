# pepper_police_cog.py
import logging
from typing import Optional

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# =====================
# CONFIG
# =====================
CONTACT_CHANNEL_ID = 1382079493711200549   # hier steht der "Contact Staff" Button
STAFF_CHANNEL_ID   = 1390430555124007145   # hier landen die Tickets

CONTACT_MARKER = "PEPPER_POLICE_CONTACT_PANEL"
TICKET_MARKER  = "PEPPER_POLICE_TICKET"

CONTACT_BUTTON_CUSTOM_ID = "pepper_police:contact_staff"
COMPLETE_BUTTON_CUSTOM_ID = "pepper_police:request_completed"


# =====================
# MODAL
# =====================
class TicketModal(discord.ui.Modal, title="Open Ticket for Pepper Police"):
    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="Kurzer Titel für dein Anliegen",
        style=discord.TextStyle.short,
        max_length=100,
        required=True,
    )
    details = discord.ui.TextInput(
        label="Details",
        placeholder="Beschreibe dein Anliegen so ausführlich wie möglich...",
        style=discord.TextStyle.paragraph,
        max_length=1800,
        required=True,
    )

    def __init__(self, staff_channel: discord.TextChannel):
        super().__init__()
        self.staff_channel = staff_channel

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"🚨 New Ticket: {self.subject.value}",
            description=self.details.value,
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=f"{interaction.user} ({interaction.user.id})",
            icon_url=interaction.user.display_avatar.url,
        )
        embed.set_footer(text=TICKET_MARKER)

        view = RequestCompletedView()
        try:
            await self.staff_channel.send(embed=embed, view=view)
        except discord.HTTPException:
            logger.exception("Konnte Ticket nicht in Staff-Channel posten")
            await interaction.response.send_message(
                "❌ Ticket konnte nicht übermittelt werden. Bitte melde dich manuell beim Staff.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "✅ Dein Ticket wurde an die Pepper Police übermittelt. Danke!",
            ephemeral=True,
        )


# =====================
# VIEWS (persistent)
# =====================
class ContactStaffView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Contact Staff",
        style=discord.ButtonStyle.danger,
        emoji="🚨",
        custom_id=CONTACT_BUTTON_CUSTOM_ID,
    )
    async def contact_staff(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Nur auf dem Server verfügbar.", ephemeral=True
            )
            return

        staff_channel = guild.get_channel(STAFF_CHANNEL_ID)
        if not isinstance(staff_channel, discord.TextChannel):
            try:
                staff_channel = await guild.fetch_channel(STAFF_CHANNEL_ID)
            except Exception:
                staff_channel = None

        if not isinstance(staff_channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Staff-Channel ist nicht erreichbar.", ephemeral=True
            )
            return

        await interaction.response.send_modal(TicketModal(staff_channel))


class RequestCompletedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Request Completed",
        style=discord.ButtonStyle.success,
        emoji="👍",
        custom_id=COMPLETE_BUTTON_CUSTOM_ID,
    )
    async def request_completed(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            logger.exception("Konnte Ticket-Nachricht nicht löschen")
            try:
                await interaction.response.send_message(
                    "❌ Konnte Ticket nicht löschen.", ephemeral=True
                )
            except discord.InteractionResponded:
                pass
            return

        if not interaction.response.is_done():
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass


# =====================
# COG
# =====================
class PepperPolice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._setup_done = False

    async def cog_load(self):
        # Persistente Views registrieren, damit Buttons auch nach Restart
        # ansprechbar sind, während wir sie neu posten.
        self.bot.add_view(ContactStaffView())
        self.bot.add_view(RequestCompletedView())

    @commands.Cog.listener()
    async def on_ready(self):
        # nur einmal ausführen (on_ready kann mehrfach feuern)
        if self._setup_done:
            return
        self._setup_done = True
        try:
            await self._refresh_panels()
        except Exception:
            logger.exception("Pepper Police panel refresh fehlgeschlagen")

    async def _refresh_panels(self):
        for guild in self.bot.guilds:
            await self._cleanup_contact_channel(guild)
            await self._cleanup_staff_channel(guild)
            await self._post_contact_panel(guild)

    async def _safe_text_channel(
        self, guild: discord.Guild, channel_id: int
    ) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    def _is_own_message(self, msg: discord.Message) -> bool:
        return msg.author.id == self.bot.user.id

    def _is_contact_panel(self, msg: discord.Message) -> bool:
        if not self._is_own_message(msg):
            return False
        if msg.content and CONTACT_MARKER in msg.content:
            return True
        for embed in msg.embeds:
            if embed.footer and embed.footer.text and CONTACT_MARKER in embed.footer.text:
                return True
        # Fallback: enthält den Button mit unserer custom_id
        for row in msg.components:
            for comp in getattr(row, "children", []):
                if getattr(comp, "custom_id", None) == CONTACT_BUTTON_CUSTOM_ID:
                    return True
        return False

    def _is_ticket_post(self, msg: discord.Message) -> bool:
        if not self._is_own_message(msg):
            return False
        for embed in msg.embeds:
            if embed.footer and embed.footer.text and TICKET_MARKER in embed.footer.text:
                return True
        for row in msg.components:
            for comp in getattr(row, "children", []):
                if getattr(comp, "custom_id", None) == COMPLETE_BUTTON_CUSTOM_ID:
                    return True
        return False

    async def _cleanup_contact_channel(self, guild: discord.Guild):
        ch = await self._safe_text_channel(guild, CONTACT_CHANNEL_ID)
        if ch is None:
            return
        try:
            async for msg in ch.history(limit=200):
                if self._is_contact_panel(msg):
                    try:
                        await msg.delete()
                    except discord.HTTPException:
                        logger.exception("Konnte altes Contact-Panel nicht löschen")
        except Exception:
            logger.exception("Fehler beim Aufräumen von Contact-Channel")

    async def _cleanup_staff_channel(self, guild: discord.Guild):
        """Alte, hängengebliebene Ticket-Posts entfernen, damit die 'Request Completed'
        Buttons in offenen Tickets nach Restart weiter funktionieren wir posten
        sie NICHT neu, da Ticket-Inhalt user-spezifisch ist. Die persistente View
        greift ohnehin für alle offenen Tickets."""
        # Da wir persistente Views mit fester custom_id verwenden, funktionieren
        # bereits existierende Tickets auch nach einem Restart weiter.
        # Nichts zu tun hier — Methode bleibt als Hook falls du das Verhalten
        # ändern möchtest (z.B. alle offenen Tickets beim Restart wegräumen).
        return

    async def _post_contact_panel(self, guild: discord.Guild):
        ch = await self._safe_text_channel(guild, CONTACT_CHANNEL_ID)
        if ch is None:
            return
        embed = discord.Embed(
            title="🚨 Pepper Police — Contact Staff",
            description=(
                "Du hast ein Anliegen, brauchst Hilfe oder möchtest etwas melden?\n\n"
                "Klick unten auf **Contact Staff** und öffne ein Ticket. "
                "Dein Anliegen wird vertraulich an das Staff-Team weitergeleitet."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text=CONTACT_MARKER)
        try:
            await ch.send(embed=embed, view=ContactStaffView())
        except discord.HTTPException:
            logger.exception("Konnte Contact-Panel nicht posten")


# =====================
# SETUP
# =====================
async def setup(bot: commands.Bot):
    await bot.add_cog(PepperPolice(bot))