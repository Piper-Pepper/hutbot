# 📁 riddle_embeds.py
import discord
from datetime import datetime

def format_riddle_embed(riddle, author, guild, show_mentions=True):
    embed = discord.Embed(
        title=f"**Goon Hut Riddle** ({datetime.now().strftime('%b %d, %Y')})",
        description=riddle['text'].replace("\\n", "\n"),
        color=discord.Color.blurple()
    )
    image_url = riddle.get("image_url") or "https://cdn.discordapp.com/attachments/1383652563408392232/1383653344601444433/riddle_logo.jpg"
    embed.set_image(url=image_url)
    embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
    if riddle.get("award"):
        embed.add_field(name="Award", value=riddle['award'], inline=False)
    embed.set_footer(text=f"{guild.name} | ID: {riddle['riddle_id']}", icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
    return embed


def format_wrong_solution_embed(riddle, user, guild):
    embed = discord.Embed(
        title="❌ Wrong Answer",
        description=f"**{riddle['text']}**\n\n**{user.mention}'s solution:** {riddle['submitted']}\n\nSadly your submitted solution was not correct.",
        color=discord.Color.red()
    )
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    embed.set_footer(text=f"{guild.name} | ID: {riddle['riddle_id']}", icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
    return embed


def format_win_embed(riddle, winner, guild):
    embed = discord.Embed(
        title=f"**Goon Hut Riddle** ({datetime.now().strftime('%b %d, %Y')})",
        description=riddle['text'].replace("\\n", "\n"),
        color=discord.Color.green()
    )
    image_url = riddle.get("solution_url") or "https://cdn.discordapp.com/attachments/1383652563408392232/1384295668176388229/zombie_piper.gif"
    embed.set_image(url=image_url)
    if winner:
        embed.set_author(name=winner.display_name, icon_url=winner.display_avatar.url)
    else:
        embed.description += "\n\nSadly no one could solve the riddle in time."
    if riddle.get("award"):
        embed.add_field(name="Award", value=riddle['award'], inline=False)
    if winner:
        embed.add_field(name="Submitted Solution", value=riddle.get("submitted", "-"), inline=False)
    embed.add_field(name="Correct Solution", value=riddle['solution'], inline=False)
    embed.set_footer(text=f"{guild.name} | ID: {riddle['riddle_id']}", icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
    return embed
