import discord
from riddle_core import Core

def create_riddle_embed(riddle_id, author: discord.User, text, created_at,
                        image_url=None, award=None, mention1=None, mention2=None,
                        solution_image=None, status="open", winner=None):
    embed = discord.Embed(
        title=f"Goon Hut Riddle (Created: {created_at})",
        description=text,
        color=discord.Color.blue()
    )
    embed.set_image(url=image_url or Core.DEFAULT_IMAGE_URL)
    avatar_url = author.avatar.url if author and author.avatar else discord.utils.MISSING
    if avatar_url is not discord.utils.MISSING:
        embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text=f"{author.name if author else 'Unknown'} | ID: {riddle_id}")

    if award:
        embed.add_field(name="Award", value=award, inline=False)

    if status == "closed":
        embed.title = f"✅ Goon Hut Riddle - Closed (Created: {created_at})"
        if winner:
            embed.add_field(name="Winner", value=winner.mention, inline=False)
        else:
            embed.add_field(name="Winner", value="No winner", inline=False)
        if solution_image:
            embed.set_image(url=solution_image)

    return embed
