import discord
from datetime import datetime

DEFAULT_RIDDLE_IMAGE = "https://cdn.discordapp.com/attachments/1383652563408392232/1383653344601444433/riddle_logo.jpg"
DEFAULT_SOLUTION_IMAGE = "https://cdn.discordapp.com/attachments/1383652563408392232/1384295668176388229/zombie_piper.gif"

def format_multiline(text: str) -> str:
    """Ensures that \\n gets converted to actual line breaks."""
    return text.replace("\\n", "\n")

def build_riddle_embed(riddle: dict, guild: discord.Guild, author: discord.Member, editable=False) -> discord.Embed:
    """Builds the embed for displaying the riddle."""
    date = datetime.fromisoformat(riddle.get("created_at", datetime.utcnow().isoformat()))
    embed = discord.Embed(
        title=f"**Goon Hut Riddle** ({date.strftime('%B %d, %Y')})",
        description=format_multiline(riddle["text"]),
        color=discord.Color.orange()
    )
    embed.set_image(url=riddle.get("image_url") or DEFAULT_RIDDLE_IMAGE)
    embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)

    if riddle.get("award"):
        embed.add_field(name="🎁 Award", value=riddle["award"], inline=False)

    embed.set_footer(
        text=f"{guild.name} • ID: {riddle['riddle_id']}",
        icon_url=guild.icon.url if guild.icon else discord.Embed.Empty
    )

    return embed

def build_solution_submission_embed(riddle: dict, submitter: discord.Member, solution: str) -> discord.Embed:
    """Builds the embed for a submitted solution."""
    embed = discord.Embed(
        title=f"{submitter.display_name}'s Solution",
        color=discord.Color.teal(),
        description=format_multiline(riddle["text"])
    )
    embed.set_author(name=submitter.display_name, icon_url=submitter.display_avatar.url)
    embed.add_field(name="Submitted Solution", value=format_multiline(solution), inline=False)
    embed.add_field(name="Expected Answer", value=format_multiline(riddle["solution"]), inline=False)
    return embed

def build_wrong_solution_embed(riddle: dict, submitter: discord.Member, solution: str, guild: discord.Guild) -> discord.Embed:
    """Builds the embed for an incorrect solution."""
    embed = discord.Embed(
        title="Wrong Answer!",
        description=format_multiline(riddle["text"]),
        color=discord.Color.red()
    )
    embed.set_author(name=submitter.display_name, icon_url=submitter.display_avatar.url)
    embed.set_image(url=riddle.get("image_url") or DEFAULT_RIDDLE_IMAGE)
    embed.add_field(name="Submitted Solution", value=format_multiline(solution), inline=False)
    embed.add_field(name="Result", value="Sadly your submitted solution was not correct.", inline=False)
    embed.set_footer(text=f"{guild.name} • ID: {riddle['riddle_id']}", icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
    return embed

def build_win_embed(riddle: dict, guild: discord.Guild, winner: discord.Member | None, solution: str | None = "") -> discord.Embed:
    """Builds the embed for announcing the winner."""
    date = datetime.fromisoformat(riddle.get("created_at", datetime.utcnow().isoformat()))
    embed = discord.Embed(
        title=f"**Goon Hut Riddle** ({date.strftime('%B %d, %Y')})",
        description=format_multiline(riddle["text"]),
        color=discord.Color.green()
    )
    embed.set_image(url=riddle.get("solution_url") or DEFAULT_SOLUTION_IMAGE)

    if winner:
        embed.set_author(name=winner.display_name, icon_url=winner.display_avatar.url)
    else:
        embed.add_field(name="Nobody solved it!", value="Sadly no one could solve the riddle in time.", inline=False)

    if riddle.get("award"):
        embed.add_field(name="🎁 Award", value=riddle["award"], inline=False)

    if solution:
        embed.add_field(name="💡 Submitted Solution", value=format_multiline(solution), inline=False)

    embed.add_field(name="✅ Correct Answer", value=format_multiline(riddle["solution"]), inline=False)

    embed.set_footer(
        text=f"{guild.name} • ID: {riddle['riddle_id']}",
        icon_url=guild.icon.url if guild.icon else discord.Embed.Empty
    )
    return embed
