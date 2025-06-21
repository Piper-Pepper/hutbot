import discord
import uuid
from discord.ext import commands
from riddle_core import riddle_manager, Core
from riddle import ActionButtonsView

def create_riddle_embed(riddle_id, author: discord.User, text, created_at,
                        image_url=None, award=None, solution_image=None,
                        status="open", winner=None):
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
        embed.add_field(name="Winner", value=winner.mention if winner else "No winner", inline=False)
        if solution_image:
            embed.set_image(url=solution_image)

    return embed

class RiddleSelect(discord.ui.Select):
    def __init__(self, riddles: dict):
        options = []
        for rid, riddle in riddles.items():
            label = rid
            desc = riddle["text"][:50] + ("..." if len(riddle["text"]) > 50 else "")
            options.append(discord.SelectOption(label=label, description=desc))
        super().__init__(
            placeholder="Wähle ein Rätsel aus...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="riddle_select_menu"
        )

    async def callback(self, interaction: discord.Interaction):
        riddle_id = self.values[0]
        riddle = riddle_manager.get_riddle(riddle_id)
        if not riddle:
            await interaction.response.send_message("Rätsel nicht gefunden.", ephemeral=True)
            return

        author = interaction.client.get_user(riddle["author_id"])
        winner = interaction.client.get_user(riddle.get("winner_id")) if riddle.get("winner_id") else None

        embed = create_riddle_embed(
            riddle_id,
            author,
            riddle["text"],
            riddle["created_at"],
            image_url=riddle.get("image_url"),
            award=riddle.get("award"),
            solution_image=riddle.get("solution_image"),
            status=riddle.get("status"),
            winner=winner
        )

        view = ActionButtonsView(riddle_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

class RiddleListView(discord.ui.View):
    def __init__(self, open_riddles: dict):
        super().__init__(timeout=None)
        self.add_item(RiddleSelect(open_riddles))

class RiddleCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="riddle_add", description="Create a new riddle")
    async def riddle_add(self, interaction: discord.Interaction,
                        text: str,
                        solution: str,
                        image_url: str = None,
                        award: str = None,
                        solution_image: str = None,
                        mention1: discord.Member = None,
                        mention2: discord.Member = None):
        author = interaction.user

        # Berechtigung checken
        if Core.RIDDLE_CREATOR_ROLE_ID not in [r.id for r in author.roles]:
            await interaction.response.send_message("You don't have permission to add riddles.", ephemeral=True)
            return

        riddle_id = str(uuid.uuid4())[:8]
        created_at = Core.get_timestamp()

        riddle_data = {
            "author_id": author.id,
            "text": text,
            "solution": solution.strip().lower(),
            "created_at": created_at,
            "status": "open",
            "image_url": image_url or Core.DEFAULT_IMAGE_URL,
            "award": award if award else None,
            "solution_image": solution_image if solution_image else None,
            "mention1_id": mention1.id if mention1 else None,
            "mention2_id": mention2.id if mention2 else None,
            "winner_id": None,
            "channel_id": Core.FIXED_CHANNEL_ID
        }

        riddle_manager.add_riddle(riddle_id, riddle_data)
        await riddle_manager.save_data()

        # Das Embed muss auch mentions anzeigen, also die IDs holen
        embed = create_riddle_embed(
            riddle_id,
            author,
            text,
            created_at,
            image_url=image_url,
            award=award,
            solution_image=solution_image,
            status="open",
            winner=None
        )

        # Erwähnungen als Text hinzufügen, wenn vorhanden
        mentions_text = ""
        if mention1:
            mentions_text += f"\n{mention1.mention}"
        if mention2:
            mentions_text += f"\n{mention2.mention}"
        if mentions_text:
            embed.add_field(name="Mentions", value=mentions_text, inline=False)

        await interaction.response.send_message(
            content=f"✅ Rätsel mit ID `{riddle_id}` wurde gespeichert. Hier deine Vorschau:",
            embed=embed,
            ephemeral=True
        )



    @discord.app_commands.command(name="riddle_list", description="List all riddles")
    async def riddle_list(self, interaction: discord.Interaction):
        try:
            open_riddles = {k: v for k, v in riddle_manager.cache.items() if v["status"] == "open"}
            closed_riddles = {k: v for k, v in riddle_manager.cache.items() if v["status"] == "closed"}

            embed = discord.Embed(title="Riddle List", color=discord.Color.teal())
            embed.add_field(
                name=f"Open Riddles ({len(open_riddles)})",
                value="\n".join(f"`{k}` | {v['created_at']}" for k, v in open_riddles.items()) or "None",
                inline=False
            )
            embed.add_field(
                name=f"Closed Riddles ({len(closed_riddles)})",
                value="\n".join(f"`{k}` | {v['created_at']}" for k, v in closed_riddles.items()) or "None",
                inline=False
            )

            if open_riddles:
                view = RiddleListView(open_riddles)
                await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=False)

        except Exception as e:
            await interaction.response.send_message("Fehler beim Abrufen der Rätselliste.", ephemeral=False)
            print(f"[ERROR][riddle_list]: {e}")

async def setup(bot):
    await bot.add_cog(RiddleCommands(bot))
