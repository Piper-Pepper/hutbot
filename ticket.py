import discord
from discord.ext import commands
from discord import app_commands
import json
import os

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

TICKET_CHANNEL_ID = 1346418734360956972
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382071708449771540/police_fuckthat.gif"
REQUIRED_ROLE_ID = 1346428405368750122
TICKET_STORAGE = "tickets.json"
YOUR_GUILD_ID = 123456789012345678  # Ersetze dies mit deiner tatsächlichen Guild-ID

# ---------------- Persistence ----------------
def load_tickets():
    if os.path.exists(TICKET_STORAGE):
        with open(TICKET_STORAGE, "r") as f:
            return json.load(f)
    return {}

def save_ticket(message_id, title, text, user_id, channel_id):
    data = load_tickets()
    data[str(message_id)] = {
        "title": title,
        "text": text,
        "user_id": user_id,
        "channel_id": channel_id
    }
    with open(TICKET_STORAGE, "w") as f:
        json.dump(data, f)

def remove_ticket(message_id):
    data = load_tickets()
    if str(message_id) in data:
        del data[str(message_id)]
        with open(TICKET_STORAGE, "w") as f:
            json.dump(data, f)

# ---------------- Views & Modals ----------------
class TicketView(discord.ui.View):
    def __init__(self, ticket_title=None, ticket_text=None, ticket_creator=None):
        super().__init__(timeout=None)
        self.ticket_title = ticket_title
        self.ticket_text = ticket_text
        self.ticket_creator = ticket_creator
        self.message = None

    @discord.ui.button(label="Send a Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_send_button")
    async def send_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.ticket_title or not self.ticket_text or not self.ticket_creator:
            await interaction.response.send_message("This ticket is no longer active after a restart.\nPlease use `/ppticket` again.", ephemeral=True)
            return
        await interaction.response.send_modal(SendTicketModal(
            ticket_title=self.ticket_title,
            ticket_text=self.ticket_text,
            ticket_creator=self.ticket_creator
        ))

class SendTicketModal(discord.ui.Modal, title="Submit Your Ticket"):
    modal_text = discord.ui.TextInput(label="Your message", style=discord.TextStyle.paragraph, required=True)

    def __init__(self, ticket_title, ticket_text, ticket_creator):
        super().__init__()
        self.ticket_title = ticket_title
        self.ticket_text = ticket_text
        self.ticket_creator = ticket_creator

    async def on_submit(self, interaction: discord.Interaction):
        ticket_channel = interaction.client.get_channel(TICKET_CHANNEL_ID)
        if not ticket_channel:
            await interaction.response.send_message("Ticket channel not found.", ephemeral=True)
            return

        embed = discord.Embed(title=f"📉 {self.ticket_title}", color=discord.Color.green())
        embed.add_field(name="Original Text", value=self.ticket_text, inline=False)
        embed.add_field(name="Response", value=self.modal_text.value, inline=False)
        embed.set_author(name=self.ticket_creator.display_name, icon_url=self.ticket_creator.display_avatar.url)
        embed.set_footer(text=f"Submitted at {discord.utils.format_dt(discord.utils.utcnow(), style='F')}")

        view = CaseClosedView(title=self.ticket_title, original_text=self.ticket_text, ticket_creator=self.ticket_creator)
        msg = await ticket_channel.send(embed=embed, view=view)
        view.message = msg
        save_ticket(msg.id, self.ticket_title, self.ticket_text, self.ticket_creator.id, ticket_channel.id)

        await interaction.response.send_message("Your ticket has been submitted!", ephemeral=True)

class CaseClosedView(discord.ui.View):
    def __init__(self, title=None, original_text=None, ticket_creator=None):
        super().__init__(timeout=None)
        self.title = title
        self.original_text = original_text
        self.ticket_creator = ticket_creator
        self.message = None

    @discord.ui.button(label="Case Closed", style=discord.ButtonStyle.danger, custom_id="ticket_case_closed_button")
    async def case_closed_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.message:
            await interaction.response.send_message("Cannot close this case anymore.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Case Closed: {self.title}" if self.title else "Case Closed",
            description=self.original_text if self.original_text else "Case has been resolved.",
            color=discord.Color.dark_red()
        )
        if self.ticket_creator:
            embed.set_author(name=self.ticket_creator.display_name, icon_url=self.ticket_creator.display_avatar.url)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Case closed by {interaction.user.display_name} at {discord.utils.format_dt(discord.utils.utcnow(), style='F')}")

        await interaction.channel.send(embed=embed)

        if self.ticket_creator:
            try:
                await self.ticket_creator.send("Thank you! The Pepper Police cared about you sent ticket!")
            except:
                pass

        try:
            await self.message.delete()
        except:
            pass

        remove_ticket(self.message.id)
        await interaction.response.send_message("Case closed!", ephemeral=True)

# ---------------- Cog with Slash Commands ----------------
class PPTicket(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # /ppticket command (for mod-created tickets using button + modal)
    @app_commands.command(name="ppticket", description="Create a ticket for the Pepper Police")
    @app_commands.describe(
        title="The ticket title",
        text="The ticket content",
        image_url="Optional image URL to show instead of default"
    )
    async def ppticket(self, interaction: discord.Interaction, title: str, text: str, image_url: str = None):
        has_role = any(role.id == REQUIRED_ROLE_ID for role in interaction.user.roles)
        if not has_role:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        embed = discord.Embed(title=f"**{title}**", description=text, color=discord.Color.orange())
        embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else discord.Embed.Empty)
        embed.set_image(url=image_url if image_url else DEFAULT_IMAGE_URL)

        view = TicketView(ticket_title=title, ticket_text=text, ticket_creator=interaction.user)
        msg = await interaction.channel.send(embed=embed, view=view)
        view.message = msg
        save_ticket(msg.id, title, text, interaction.user.id, interaction.channel.id)

        await interaction.response.send_message("Ticket system ready. Users can now submit a ticket.", ephemeral=True)

    # /ppolice command (ticket with text provided via slash; optional user mention)
    @app_commands.command(name="ppolice", description="Let someone send a ticket to the Pepper Police")
    @app_commands.describe(
        text="The reason for the ticket",
        user="Optional user to report or refer to"
    )
    async def ppolice(self, interaction: discord.Interaction, text: str, user: discord.User = None):
        has_role = any(role.id == REQUIRED_ROLE_ID for role in interaction.user.roles)
        if not has_role:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        title = "Report to the Pepper Police"
        description = text
        if user:
            description += f"\n\n🚨 Reported User: {user.mention}"

        embed = discord.Embed(title=f"**{title}**", description=description, color=discord.Color.orange())
        embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else discord.Embed.Empty)
        embed.set_image(url=DEFAULT_IMAGE_URL)

        view = TicketView(ticket_title=title, ticket_text=description, ticket_creator=interaction.user)
        msg = await interaction.channel.send(embed=embed, view=view)
        view.message = msg
        save_ticket(msg.id, title, description, interaction.user.id, interaction.channel.id)

        await interaction.response.send_message("Pepper Police ticket created. Users can now submit their message.", ephemeral=True)

# ---------------- Restore Tickets on Ready ----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Falls du Guild-Commands verwendest:
    guild_obj = discord.Object(id=YOUR_GUILD_ID)
    bot.tree.copy_global_to(guild=guild_obj)
    await bot.tree.sync(guild=guild_obj)
    
    data = load_tickets()
    for message_id, info in data.items():
        try:
            channel = bot.get_channel(int(info["channel_id"]))
            if not channel:
                continue
            msg = await channel.fetch_message(int(message_id))
            user = await bot.fetch_user(int(info["user_id"]))
            view = TicketView(ticket_title=info["title"], ticket_text=info["text"], ticket_creator=user)
            view.message = msg
            await msg.edit(view=view)
            # Registriere den View auch global, damit er persistiert
            bot.add_view(view)
        except Exception as e:
            print(f"Failed to restore ticket {message_id}: {e}")

# ---------------- Cog Setup ----------------
async def setup(bot):
    await bot.add_cog(PPTicket(bot))
