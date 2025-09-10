import datetime
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp


DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1414114417800515607/idcard_small.png"  # Anpassen bei Bedarf
JSONBIN_URL = "https://api.jsonbin.io/v3/b/686699c18960c979a5b67e34/latest"
HEADERS = {
    "X-Master-Key": "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"
}


special_roles_to_highlight = {
    1346428405368750122: "*(Mod Team👮‍♂️)*",
    1346414581643219029: "*(your favourite...💋)*",
    1375143857024401478: "*(XP Leader🏆)*",
    "Server Booster": "*(Hut-Booster 🚀)*",
    1378442177763479654: "*(3. Voice Time🎤)*",
    1375481531426144266: "*(1. Voice Time🎤)*",
    1378130233693306950: "*(2. Voice Time🎤)*",
    1381454281500262520: "*(1. #msg✍️)*",
    1381454805205258250: "*(2. #msg✍️)*",
    1381455215215247481: "*(3. #msg✍️)*",
    1379909107926171668: "*(My lil' Goon-Bots🤖)*",
    1346479048175652924: "*(Stream VJ)*",
    1361993080013717678: "*(**NO NSFW**)*",
    1379175952147546215: "*(Stream Alerts)*",
    1346549280617271326: "*(more 📨 by me...)*",
    1380610400416043089: "*(Riddler of the Hut)*",
 }

level_roles = {
    1377051179615522926: ("0️⃣3️⃣", "ₜᵢₑᵣ ₁"),
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

async def send_pepper_embed(interaction, user, open=False, mention_group=None, text=None, image_url=None):
    await interaction.response.defer(ephemeral=not open)
    guild = interaction.guild

    member = guild.get_member(user.id)
    if not member:
        try:
            member = await guild.fetch_member(user.id)
        except discord.NotFound:
            member = None

    # ❌ Wenn User nicht mehr auf dem Server ist → Sad Piper anzeigen
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

    sorted_roles = sorted((role for role in member.roles if role != guild.default_role), key=lambda r: r.name.lower())
    highlighted_roles, normal_roles = [], []
    location_role = gender_role = stoner_buddy = dm_open = None

    for role in sorted(member.roles, key=lambda r: r.position, reverse=True):
        role_highlight = special_roles_to_highlight.get(role.id) or special_roles_to_highlight.get(role.name)
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
        title=f"ᕼᑌT ᗰEᗰᗷEᖇ: \n{member.global_name or member.name} *({user.name})*",
        color=embed_color
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.add_field(name="​ᴀᴄᴄᴏᴜɴᴛ\n", value=created_at, inline=True)
    embed.add_field(name="​ᴊᴏɪɴᴇᴅ\n", value=joined_at, inline=True)
    embed.add_field(name="ᴛᴏᴘ ʀᴏʟᴇ​\n", value=member.top_role.mention if member.top_role != guild.default_role else "No top role", inline=True)
    embed.add_field(name="🌍ʟᴏᴄᴀᴛɪᴏɴ", value=location_role or "No location role", inline=True)
    embed.add_field(name="🚻ɢᴇɴᴅᴇʀ", value=gender_role or "No gender role", inline=True)
    if stoner_buddy:
        embed.add_field(name="✅ɢᴀɴᴊᴀ", value=stoner_buddy, inline=True)
    if dm_open:
        embed.add_field(name="📬​Open for DM",value=dm_open, inline=False)    
    if level_roles_of_member:
        embed.add_field(name="🏆 𝙇𝙀𝙑𝙀𝙇𝙎", value="\n".join(level_roles_of_member), inline=False)
    if highlighted_roles:
        embed.add_field(name="⭐ Special Roles", value="\n".join(highlighted_roles), inline=False)

    
    # 🧠 Fetch JSONBin Riddle Stats

        
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(JSONBIN_URL, headers=HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    riddles_info = data.get("record", {}).get(str(user.id))
                    if riddles_info:
                        solved = riddles_info.get("solved_riddles", 0)
                        xp = riddles_info.get("xp", 0)
                        embed.add_field(name="🧩ℜ𝔦𝔡𝔡𝔩𝔢 𝔇𝔞𝔱𝔞", value=f"🔓 {solved} /  🧠 {xp} XP", inline=True)
                else:
                    print(f"Failed to fetch riddle data: HTTP {resp.status}")
    except Exception as e:
        print(f"Error fetching riddle data: {e}")

    embed.add_field(name="🎭 𝙍𝙊𝙇𝙀𝙎", value=", ".join(normal_roles) if normal_roles else "No roles", inline=False)
    embed.set_thumbnail(url=user.avatar.url if user.avatar else user.default_avatar.url)
    embed.set_image(url=image_url if image_url else DEFAULT_IMAGE_URL)
    embed.set_footer(text="👅...and don't forget to lick the butt... of your favourite Goonette-Slut!")


    final_content = ""
    if open and mention_group:
        final_content += mention_group.mention + "\n"
    if text:
        final_content += text

    await interaction.followup.send(content=final_content if final_content else None, embed=embed, ephemeral=not open)

async def setup(bot: commands.Bot):
    @bot.tree.context_menu(name="🛖 Goon Hut Info")
    async def pepper_context(interaction: discord.Interaction, user: discord.User):
        await send_pepper_embed(interaction, user)

    @bot.tree.command(name="pepper", description="Do it Pepper-Style 🫦 ... and show your ID Card..")
    @app_commands.describe(
        user="The gooner to show info for",
        open="Show your ID to all publicly? (True) or private (False)",
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


