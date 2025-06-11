import discord
from discord.ext import commands
from discord import app_commands

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

TICKET_CHANNEL_ID = 1346418734360956972
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382071708449771540/police_fuckthat.gif"
REQUIRED_ROLE_ID = 1346428405368750122

class PPTicket(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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

        await interaction.response.send_message("Ticket embed has been created!", ephemeral=True)

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
            await interaction.response.send_message(
                "This ticket is no longer active after a restart.\nPlease use `/ppticket` again.",
                ephemeral=True
            )
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

        embed = discord.Embed(title=f"📩 {self.ticket_title}", color=discord.Color.green())
        embed.add_field(name="Original Text", value=self.ticket_text, inline=False)
        embed.add_field(name="Response", value=self.modal_text.value, inline=False)
        embed.set_author(name=self.ticket_creator.display_name, icon_url=self.ticket_creator.display_avatar.url)
        embed.set_footer(text=f"Submitted at {discord.utils.format_dt(discord.utils.utcnow(), style='F')}")

        view = CaseClosedView(
            title=self.ticket_title,
            original_text=self.ticket_text,
            ticket_creator=self.ticket_creator
        )

        msg = await ticket_channel.send(embed=embed, view=view)
        view.message = msg

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
        if not self.title or not self.original_text or not self.ticket_creator:
            await interaction.response.send_message(
                "This case cannot be closed anymore. Try re-creating the ticket.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Case Closed: {self.title}",
            description=self.original_text,
            color=discord.Color.dark_red()
        )
        embed.set_author(
            name=self.ticket_creator.display_name,
            icon_url=self.ticket_creator.display_avatar.url
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(
            text=f"Case closed by {interaction.user.display_name} at {discord.utils.format_dt(discord.utils.utcnow(), style='F')}"
        )

        await interaction.channel.send(embed=embed)

        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass

        await interaction.response.send_message("Case closed!", ephemeral=True)

import discord
from discord.ext import commands
from discord import app_commands

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

TICKET_CHANNEL_ID = 1346418734360956972
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1382071708449771540/police_fuckthat.gif"
REQUIRED_ROLE_ID = 1346428405368750122

class PPTicket(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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

        await interaction.response.send_message("Ticket embed has been created!", ephemeral=True)

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
            await interaction.response.send_message(
                "This ticket is no longer active after a restart.\nPlease use `/ppticket` again.",
                ephemeral=True
            )
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

        embed = discord.Embed(title=f"📩 {self.ticket_title}", color=discord.Color.green())
        embed.add_field(name="Original Text", value=self.ticket_text, inline=False)
        embed.add_field(name="Response", value=self.modal_text.value, inline=False)
        embed.set_author(name=self.ticket_creator.display_name, icon_url=self.ticket_creator.display_avatar.url)
        embed.set_footer(text=f"Submitted at {discord.utils.format_dt(discord.utils.utcnow(), style='F')}")

        view = CaseClosedView(
            title=self.ticket_title,
            original_text=self.ticket_text,
            ticket_creator=self.ticket_creator
        )

        msg = await ticket_channel.send(embed=embed, view=view)
        view.message = msg

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
        if not self.title or not self.original_text or not self.ticket_creator:
            await interaction.response.send_message(
                "This case cannot be closed anymore. Try re-creating the ticket.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Case Closed: {self.title}",
            description=self.original_text,
            color=discord.Color.dark_red()
        )
        embed.set_author(
            name=self.ticket_creator.display_name,
            icon_url=self.ticket_creator.display_avatar.url
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(
            text=f"Case closed by {interaction.user.display_name} at {discord.utils.format_dt(discord.utils.utcnow(), style='F')}"
        )

        await interaction.channel.send(embed=embed)

        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass

        await interaction.response.send_message("Case closed!", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    # Register persistent views with known custom_id
    bot.add_view(TicketView())        # Persistent button: ticket_send_button
    bot.add_view(CaseClosedView())    # Persistent button: ticket_case_closed_button
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

async def setup():
    await bot.add_cog(PPTicket(bot))

bot.loop.create_task(setup())
bot.run("YOUR_TOKEN_HERE")

