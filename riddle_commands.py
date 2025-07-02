
# -------- Main Cog --------
class RiddleCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle_add", description="Create a new riddle (Mods only)")
    async def riddle_add(self, interaction: discord.Interaction,
                         text: str,
                         solution: str,
                         image_url: str = "",
                         mentions: str = "",
                         solution_image: str = "",
                         award: str = ""):
        if MOD_ROLE_ID not in [role.id for role in interaction.user.roles]:
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        riddle_id = str(uuid.uuid4())[:8]

        # Ensure all fields are included, even if they're not provided (set to None if empty)
        riddle_data = {
            "text": text,
            "solution": solution,
            "image_url": image_url if image_url else None,  # Set to None if not provided
            "solution_url": solution_image if solution_image else None,  # Set to None if not provided
            "mentions": [x.strip() for x in mentions.split(",") if x.strip()] if mentions else None,  # Empty list if no mentions
            "award": award if award else None,  # Set to None if not provided
            "riddle_id": riddle_id,
            "ersteller": str(interaction.user.id),
            "winner": None,
            "created_at": datetime.utcnow().isoformat(),
            "channel_id": None,
            "button_id": None,
            "suggestions": []  # Start with an empty list for suggestions
        }

        print(f"Adding riddle: {riddle_data}")

        # Save the new riddle to the in-memory cache and the JSONBin
        riddle_cache[riddle_id] = riddle_data
        save_riddles()

        # Send confirmation message with the riddle preview
        embed = build_riddle_embed(riddle_data, interaction.guild, interaction.user)
        await interaction.followup.send(
            "🧩 Riddle created. Here’s your preview:",
            embed=embed,
            view=RiddleEditView(self.bot, riddle_id),
            ephemeral=True
        )


    @app_commands.command(name="riddle_list", description="List all riddles with buttons")
    async def riddle_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="🧩 Active Riddles",
            description="Select a riddle below to edit it.",
            color=discord.Color.blurple()
        )

        try:
            view = RiddleListView(self.bot)
        except Exception as e:
            print(f"❌ Error building riddle list: {e}")
            embed.description = "⚠️ Failed to load riddles."
            view = None

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCommands(bot))
