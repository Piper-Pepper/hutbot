import asyncio
import datetime
import subprocess
import discord
from discord import app_commands
from discord.ext import commands
from riddle_system import setup_riddle_commands
from ticket import PPTicket
from forward import DMForwarder
from ppost import PPostCommand
from ppost import RoleButtonView, load_state
from dotenv import load_dotenv
import os


state = load_state()




# === Bot setup ===
intents = discord.Intents.all()
intents.members = True
intents.message_content = True

load_dotenv()  # Diese Zeile lädt die .env Datei
TOKEN = os.getenv('DISCORD_TOKEN')


bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree



DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1346843244067160074/1381375333491675217/idcard_small.png"
DEFAULT_HUTMEMBER_IMAGE_URL = DEFAULT_IMAGE_URL

# Hier kommt der /help Command:

@bot.tree.command(name="help", description="Show a list of available bot commands.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
  
        title="🤖 Bot Command Guide",
        description="Here is a list of all available bot commands and what they do:",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="⭐ /pepper",
        value="Shows a private info box about a user (can be posted publicly).", 
        inline=False
    )

    embed.add_field(
        name="👥 /hutmember",
        value="Lists members of the server (can be posted publicly).", 
        inline=False
    )

    embed.add_field(
        name="🧩 /riddle",
        value="Post a riddle with an interactive button for submitting answers.", 
        inline=False
    )

    embed.add_field(
        name="📝 /ppost",
        value="Post an announcement or info embed (with optional image).", 
        inline=False
    )

    embed.add_field(
        name="🚨 /ppticket",
        value=(
            "Submit a ticket to the Pepper Police.\n"
            "Includes a **Send a Ticket** button and supports case management."
        ),
        inline=False
    )

    embed.set_footer(text="Need help? Contact an admin or use this command anytime!")

    await interaction.response.send_message(embed=embed, ephemeral=True)

# === /hutmember command ===
@tree.command(name="hutmember", description="Show all members with a given role")
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
        if top_role == guild.default_role:
            top_role_display = "**No Role**"
        else:
            top_role_display = f"**{top_role.mention}**"
        
        avatar_link = m.display_avatar.url
        
        display_name_link = f"[**{m.display_name}**]({avatar_link})"
        display_name_mention = f"<@{m.id}>"
        return f"{display_name_link} — {top_role_display} — *({days}d)*"

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


# === Pepper roles ===
special_roles_to_highlight = {
    1346428405368750122: "👮‍♂️ *(Mod Team)*",
    1346414581643219029: "💋 Your favourite...",
    1375143857024401478: "*(XP Leader)*",
    "Server Booster": "🚀 *(Hut-Boosters)*",
    1378442177763479654: "**3.** *Voice Time*",
    1375481531426144266: "**1.** *Voice Time*",
    1378130233693306950: "**2.** Voice Time",
    1381454281500262520: "*(1rd #msg)*",
    1381454805205258250: "*(2rd #msg)*",
    1381455215215247481: "*(3rd #msg)*",
    1379909107926171668: "*(🤖 My lil Bots)*",
    1346479048175652924: "*(Stream VJ)*",
    1361993080013717678: "*(**NO NSFW**)*",
    1379175952147546215: "*(Stream Alerts)*",
    1346549280617271326: "*(more Alerts)*",
    1380610400416043089: "*(Riddler of the Hut)*"
}

level_roles = {
    1377051179615522926: ("0️⃣3️⃣", "ₜᵢₑᵣ ₁"),
    1375147276413964408: ("1️⃣1️⃣", "ₜᵢₑᵣ ₂"),
    1376592697606930593: ("2️⃣1️⃣", "ₜₜᵢₑᵣ ₃"),
    1381791848875430069: ("3️⃣3️⃣", "ₜᵢₑᵣ ₄"),
    1375666588404940830: ("4️⃣2️⃣", "ₜᵢₑᵣ ₅"),
    1375584380914896978: ("6️⃣9️⃣", "ₜᵢₑᵣ ₆")
}

location_roles = {
    "Europe", "North America", "Asia", "Oceania", "Africa", "South America", "Outer Goonverse"
}

gender_roles = {
    "Male", "Female", "Non-Binary"
}

stoner_role_id = 1346461573392105474

@tree.context_menu(name="🛖 Goon Hut Info")
async def pepper_command(interaction: discord.Interaction, user: discord.User):
    await send_pepper_embed(interaction, user)

@tree.command(name="pepper", description="Show detailed info about a server member")
@app_commands.describe(
    user="The member to show info for",
    open="Show embed publicly (True) or private (False)",
    mention_group="Optional: Select a role to mention (only if open=True)",
    text="Optional: Additional text to display",
    image_url="Optional: URL of an image to display below the embed"
)
@app_commands.rename(mention_group="mention-group")
async def pepper_slash(interaction: discord.Interaction, user: discord.User, open: bool = False, mention_group: discord.Role = None, text: str = None, image_url: str = None):
    if not open and mention_group is not None:
        await interaction.response.send_message("⚠️ You can only use **mention-group** if **open** is set to True.", ephemeral=True)
        return
    await send_pepper_embed(interaction, user, open=open, mention_group=mention_group, text=text, image_url=image_url)

async def send_pepper_embed(interaction, user, open=False, mention_group=None, text=None, image_url=None):
    await interaction.response.defer(ephemeral=not open)
    guild = interaction.guild
    member = guild.get_member(user.id) or await guild.fetch_member(user.id)
    if not member:
        await interaction.followup.send(content="❌ Member not found.", ephemeral=True)
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    def format_date_with_days(date):
        if not date:
            return "Unknown"
        days_ago = (now - date).days
        return f"\n{date.strftime('%Y-%m-%d %H:%M UTC')}\n(**{days_ago} days ago**)"

    joined_at = format_date_with_days(member.joined_at)
    created_at = format_date_with_days(user.created_at)

    sorted_roles = sorted((role for role in member.roles if role != guild.default_role), key=lambda r: r.name.lower())
    highlighted_roles, normal_roles = [], []
    location_role = gender_role = stoner_buddy = None

    for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
        role_highlight = special_roles_to_highlight.get(role.id) or special_roles_to_highlight.get(role.name)
        if role_highlight:
            highlighted_roles.append(f"👉 {role.mention} / {role_highlight}")
        elif role.id in level_roles:
            continue
        elif role.name in location_roles and not location_role:
            location_role = role.mention
        elif role.name in gender_roles and not gender_role:
            gender_role = role.mention
        elif role.id == stoner_role_id:
            stoner_buddy = " ₛₜₒₙₑᵣ Bᵤddy💨"
        else:
            normal_roles.append(f"{role.mention}")

    level_roles_of_member = [
        f"{level_roles[role.id][0]}​{role.mention}​{level_roles[role.id][1]}"
        for role in sorted_roles if role.id in level_roles
    ]

    embed_color = member.top_role.color if member.top_role.color.value else discord.Color.dark_gold()
    embed = discord.Embed(
        title=f"ᕼᑌT ᗰEᗰᗷEᖇ: \n{member.global_name or member.name} *({user.name})*",
        color=embed_color
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.add_field(name="​ᴀᴄᴄᴏᴜɴᴛ\n", value=created_at, inline=True)
    embed.add_field(name="​ᴊᴏɪɴᴇᴅ\n", value=joined_at, inline=True)
    top_role = member.top_role.mention if member.top_role != guild.default_role else "No top role"
    embed.add_field(name="ᴛᴏᴘ ʀᴏʟᴇ​\n", value=top_role, inline=True)
    embed.add_field(name="🌍ʟᴏᴄᴀᴛɪᴏɴ", value=location_role or "No location role", inline=True)
    embed.add_field(name="🚻ɢᴇɴᴅᴇʀ", value=gender_role or "No gender role", inline=True)
    if stoner_buddy:
        embed.add_field(name="✅ɢᴀɴᴊᴀ", value=stoner_buddy, inline=True)
    if level_roles_of_member:
        embed.add_field(name="🏆 𝙇𝙀𝙑𝙀𝙇𝙎", value="\n".join(level_roles_of_member), inline=False)
    if highlighted_roles:
        embed.add_field(name="⭐ S̴̢̺͖̮̲̰̤̣̼̔p̵̫̬̙͍͍̺͌̇̑̆̚͘ê̴̫̱͚̩̊̈́̑c̶̛̛̬̗̗̜͚̙̼͊̀̃͐̽͆̃̕i̷̯̞̤̅͝ǎ̴̧̡̨̫̜̪͚̰͙̔́̈́l̶̲͍̮͓̣̬̮͔͂͆͂̊̌͐͝ ̵͖̼̳̤̯̘͋̽̊̿̽̌̍͂̕R̵̢͉̹̗̺͖̳̹̮̩͆̌͐Ơ̵̧̹̫̜̠͍̠̩̞̿͆͗̿L̵̡̙̰͍͈̻̝̦̺͌̔̆͊͊E̴̢̼̙̮̞͚̠͍̖̯͌͌̆Ṡ̸̥̹̱̻͉̠̪͕̐̾͝", value="\n".join(highlighted_roles), inline=False)
    embed.add_field(name="🎭 𝙍𝙊𝙇𝙀𝙎", value=", ".join(normal_roles) if normal_roles else "No roles", inline=False)
    embed.set_thumbnail(url=user.avatar.url if user.avatar else user.default_avatar.url)
    embed.set_image(url=image_url if image_url else DEFAULT_IMAGE_URL)
    embed.set_footer(text="Pumping forever... Cumming never...")

    final_content = ""
    if open and mention_group:
        final_content += mention_group.mention + "\n"
    if text:
        final_content += text

    await interaction.followup.send(content=final_content if final_content else None, embed=embed, ephemeral=not open)


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith("/generate"):
        try:
            await asyncio.sleep(13)
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
    await bot.process_commands(message)





@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}!")
    # Re-register persistent views
    await setup_riddle_commands(bot)
    await bot.add_cog(PPTicket(bot)) 
    await bot.add_cog(DMForwarder(bot)) 
    await bot.add_cog(PPostCommand(bot)) 
    await bot.change_presence(activity=discord.Game(name=".. with her Cum-Kitty"))
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync: {e}")

# Launch the bot
bot.run(TOKEN)
