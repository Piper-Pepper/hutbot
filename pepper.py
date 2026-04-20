import datetime
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio
import time


DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1414114417800515607/idcard_small.png"
JSONBIN_URL = "https://api.jsonbin.io/v3/b/686699c18960c979a5b67e34/latest"
HEADERS = {
    "X-Master-Key": "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
}

# ✅ Globale Session
session = None

# ✅ Einfacher Cache (verhindert Spam-Requests)
json_cache = None
json_cache_time = 0
CACHE_DURATION = 30  # Sekunden


special_roles_to_highlight = {
    1346428405368750122: "*(Mod👮‍♂️)*",
    1346414581643219029: "",
    1375143857024401478: "*(XP🏆)*",
    1346439507171475457: "",
    1378442177763479654: "*(3.🎤)*",
    1375481531426144266: "*(1.🎤)*",
    1378130233693306950: "*(2.🎤)*",
    1381454281500262520: "*(1.✍️)*",
    1381454805205258250: "*(2.✍️)*",
    1381455215215247481: "*(3.✍️)*",
    1379909107926171668: "",
    1346479048175652924: "",
    1361993080013717678: "",
    1379175952147546215: "",
    1346549280617271326: "",
    1380610400416043089: "",
}

level_roles = {
    1377051179615522926: ("0️⃣4️⃣", "ₜᵢₑᵣ ₁"),
    1375147276413964408: ("1️⃣1️⃣", "ₜᵢₑᵣ ₂"),
    1376592697606930593: ("2️⃣1️⃣", "ₜᵢₑᵣ ₃"),
    1381791848875430069: ("3️⃣3️⃣", "ₜᵢₑᵣ ₄"),
    1375666588404940830: ("4️⃣2️⃣", "ₜᵢₑᵣ ₅"),
    1375584380914896978: ("6️⃣9️⃣", "ₜᵢₑᵣ ₆")
}

location_roles = {
    "Europe", "North America", "Asia", "Oceania", "Africa", "South America", "Outer Gσσɳʋҽɾʂҽ"
}

gender_roles = {
    "Male", "Female", "Non-Binary"
}

stoner_role_id = 1346461573392105474
dm_id = 1387850018471284760


# ✅ JSON Fetch mit Cache
async def get_riddle_data(user_id):
    global json_cache, json_cache_time, session

    now = time.time()

    # Cache nutzen
    if json_cache and (now - json_cache_time < CACHE_DURATION):
        return json_cache.get(str(user_id))

    try:
        async with session.get(JSONBIN_URL, headers=HEADERS) as resp:
            if resp.status == 200:
                data = await resp.json()
                json_cache = data.get("record", {})
                json_cache_time = now
                return json_cache.get(str(user_id))
    except Exception as e:
        print(f"Error fetching riddle data: {e}")

    return None


async def send_pepper_embed(interaction, user, open=False, mention_group=None, text=None, image_url=None):
    await interaction.response.defer(ephemeral=not open)

    guild = interaction.guild

    member = guild.get_member(user.id)
    if not member:
        try:
            member = await guild.fetch_member(user.id)
        except discord.NotFound:
            member = None

    if not member:
        embed = discord.Embed(
            title="❌ Member not found",
            description="This member is currently not a member of the **Goon Hut.**",
            color=discord.Color.red()
        )
        embed.set_image(url="https://cdn.discordapp.com/attachments/1383652563408392232/1415301679242280980/Sad_piper.gif")
        await interaction.followup.send(embed=embed, ephemeral=not open)
        return

    now = datetime.datetime.now(datetime.timezone.utc)

    def format_date_with_days(date):
        if not date:
            return "Unknown"
        days_ago = (now - date).days
        return f"\n{date.strftime('%Y-%m-%d %H:%M UTC')}\n(**{days_ago} days ago**)"

    joined_at = format_date_with_days(member.joined_at)
    created_at = format_date_with_days(user.created_at)

    highlighted_roles, normal_roles = [], []
    location_role = gender_role = stoner_buddy = dm_open = None

    for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
        role_highlight = special_roles_to_highlight.get(role.id)
        if role_highlight:
            highlighted_roles.append(f"▶ {role.mention} ⏭ {role_highlight}")
        elif role.id in level_roles:
            continue
        elif role.name in location_roles and not location_role:
            location_role = role.mention
        elif role.name in gender_roles and not gender_role:
            gender_role = role.mention
        elif role.id == stoner_role_id:
            stoner_buddy = " ₛₜₒₙₑᵣ Bᵤddy💨"
        elif role.id == dm_id:
            dm_open = "✅💌"
        else:
            normal_roles.append(f"{role.mention}")

    level_roles_of_member = [
        f"{level_roles[role.id][0]}​{role.mention}​{level_roles[role.id][1]}"
        for role in sorted(member.roles, key=lambda r: r.position, reverse=True)
        if role.id in level_roles
    ]

    embed_color = member.top_role.color if member.top_role.color.value else discord.Color.dark_gold()

    embed = discord.Embed(
        title=f"ᕼᑌT ᗰEᗰᗷEᖇ:\n{member.global_name or member.name} *({user.name})*",
        color=embed_color
    )

    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)

    embed.add_field(name="​ᴀᴄᴄᴏᴜɴᴛ", value=created_at, inline=True)
    embed.add_field(name="​ᴊᴏɪɴᴇᴅ", value=joined_at, inline=True)
    embed.add_field(name="ᴛᴏᴘ ʀᴏʟᴇ", value=member.top_role.mention if member.top_role != guild.default_role else "No top role", inline=True)
    embed.add_field(name="🌍ʟᴏᴄᴀᴛɪᴏɴ", value=location_role or "No location role", inline=True)
    embed.add_field(name="🚻ɢᴇɴᴅᴇʀ", value=gender_role or "No gender role", inline=True)

    if stoner_buddy:
        embed.add_field(name="✅ɢᴀɴᴊᴀ", value=stoner_buddy, inline=True)

    if dm_open:
        embed.add_field(name="📬​Open for DM", value=dm_open, inline=False)

    if level_roles_of_member:
        embed.add_field(name="🏆 𝙇𝙀𝙑𝙀𝙇𝙎", value="\n".join(level_roles_of_member), inline=False)

    if highlighted_roles:
        embed.add_field(name="⭐ Special Roles", value="\n".join(highlighted_roles), inline=False)

    # ✅ JSON Daten (jetzt gecached)
    riddles_info = await get_riddle_data(user.id)
    if riddles_info:
        solved = riddles_info.get("solved_riddles", 0)
        xp = riddles_info.get("xp", 0)
        embed.add_field(name="🧩ℜ𝔦𝔡𝔡𝔩𝔢 𝔇𝔞𝔱𝔞", value=f"🔓 {solved} / 🧠 {xp} XP", inline=True)

    embed.add_field(name="🎭 𝙍𝙊𝙇𝙀𝙎", value=", ".join(normal_roles) if normal_roles else "No roles", inline=False)

    embed.set_thumbnail(url=user.avatar.url if user.avatar else user.default_avatar.url)
    embed.set_image(url=image_url if image_url else DEFAULT_IMAGE_URL)

    embed.set_footer(text="👅...and don't forget to lick the butt... of your favourite Goonette-Slut!")

    final_content = ""
    if open and mention_group:
        final_content += mention_group.mention + "\n"
    if text:
        final_content += text

    # ✅ kleiner Delay gegen Burst
    await asyncio.sleep(0.5)

    await interaction.followup.send(
        content=final_content if final_content else None,
        embed=embed,
        ephemeral=not open
    )


async def setup(bot: commands.Bot):
    global session
    session = aiohttp.ClientSession()

    @bot.tree.context_menu(name="🛖 Goon Hut Info")
    async def pepper_context(interaction: discord.Interaction, user: discord.User):
        await send_pepper_embed(interaction, user)

    @bot.tree.command(name="pepper", description="Do it Pepper-Style 🫦 ... and show your ID Card..")
    async def pepper_slash(interaction: discord.Interaction, user: discord.User, open: bool = False, mention_group: discord.Role = None, text: str = None, image_url: str = None):
        if not open and mention_group is not None:
            await interaction.response.send_message("⚠️ You can only use **mention-group** if **open** is set to True.", ephemeral=True)
            return
        await send_pepper_embed(interaction, user, open=open, mention_group=mention_group, text=text, image_url=image_url)