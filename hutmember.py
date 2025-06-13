import datetime
import discord
from discord import app_commands
from discord.ext import commands

DEFAULT_HUTMEMBER_IMAGE_URL = "https://example.com/default_hutmember_image.jpg"  # Anpassen bei Bedarf

async def send_paginated_hutmember(interaction: discord.Interaction, role: discord.Role, sort: str = "joined", open: bool = False):
    await interaction.response.defer(ephemeral=not open)
    guild = interaction.guild
    members = [m for m in guild.members if role in m.roles]
    if not members:
        await interaction.followup.send("❌ No members found with this role.", ephemeral=True)
        return

    now = datetime.datetime.now(datetime.timezone.utc)

    if sort == "alpha":
        members.sort(key=lambda m: m.display_name.lower())
    else:
        members.sort(key=lambda m: m.joined_at or datetime.datetime.max)

    per_page = 15
    total_pages = (len(members) - 1) // per_page + 1
    current_page = 0

    def format_member_line(m):
        days = (now - m.joined_at).days if m.joined_at else "?"
        top_role = m.top_role
        top_role_display = f"**{top_role.mention}**" if top_role != guild.default_role else "**No Role**"
        avatar_link = m.display_avatar.url
        display_name_link = f"[**{m.display_name}**]({avatar_link})"
        return f"{display_name_link} dsads — {top_role_display} — *({days}d)*"

    def get_page_embed(page):
        start = page * per_page
        end = start + per_page
        chunk = members[start:end]
        lines = [format_member_line(m) for m in chunk]

        embed = discord.Embed(
            title=f"🛖 Members of: {role.name}",
            description="\n".join(lines),
            color=role.color if role.color != discord.Color.default() else discord.Color.dark_gold()
        )
        if chunk:
            m = chunk[0]
            embed.set_thumbnail(url=m.avatar.url if m.avatar else m.default_avatar.url)

        if getattr(role, "icon", None):
            embed.set_author(name=role.name, icon_url=role.icon.url)

        embed.set_image(url=DEFAULT_HUTMEMBER_IMAGE_URL)
        embed.set_footer(text=f"Page {page + 1} / {total_pages} • Total: {len(members)} member(s)")
        return embed

    class PaginationView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=180)

        @discord.ui.button(label="⏪ Back", style=discord.ButtonStyle.secondary, disabled=True)
        async def back(self, interaction_button: discord.Interaction, button: discord.ui.Button):
            nonlocal current_page
            if current_page > 0:
                current_page -= 1
                await interaction_button.response.edit_message(embed=get_page_embed(current_page), view=self)
                self.update_buttons()

        @discord.ui.button(label="Next ⏩", style=discord.ButtonStyle.secondary)
        async def next(self, interaction_button: discord.Interaction, button: discord.ui.Button):
            nonlocal current_page
            if current_page < total_pages - 1:
                current_page += 1
                await interaction_button.response.edit_message(embed=get_page_embed(current_page), view=self)
                self.update_buttons()

        def update_buttons(self):
            self.children[0].disabled = current_page == 0
            self.children[1].disabled = current_page >= total_pages - 1

    view = PaginationView()
    view.update_buttons()
    await interaction.followup.send(embed=get_page_embed(current_page), view=view, ephemeral=not open)

# Slash-Command registrieren
async def setup(bot: commands.Bot):
    @bot.tree.command(name="hutmember", description="Show all members with a given role")
    @app_commands.describe(
        role="The role whose members should be displayed",
        sort="Sorting order of the members",
        open="Visibility: public or only visible to you"
    )
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="Joined the Hut", value="joined"),
            app_commands.Choice(name="Alphabetical", value="alpha"),
        ]
    )
    async def hutmember(
        interaction: discord.Interaction,
        role: discord.Role,
        sort: app_commands.Choice[str] = None,
        open: bool = False
    ):
        await send_paginated_hutmember(interaction, role, sort=sort.value if sort else "joined", open=open)
