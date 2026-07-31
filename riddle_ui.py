# riddle_ui.py  (Teil 1/2)
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional, Literal

import discord
from discord import Interaction
from discord.ui import View, Modal, TextInput, Select

from riddle_core import (
    RIDDLE_ROLE_ID, RIDDLE_MANAGER_ROLE_ID, MAX_RIDDLE_SLOTS, MAX_EXTRA_PING_ROLES,
    DEFAULT_IMAGE_URL, SUBMIT_BUTTON_ID, VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID,
    VOTE_CHANNEL_ID,
    logger, to_int, safe_int, clean_value, is_http_url, truncate_text,
    clamp_embed_value, clamp_embed_description, extract_first_url, footer_text,
    parse_csv_role_ids, unique_role_mentions, safe_defer, member_has_role,
)

if TYPE_CHECKING:
    from riddle import RiddleCog


# =============================================================================
# INTERNAL HELPERS
# =============================================================================
def _spoiler_safe(text: str) -> str:
    """Neutralize existing '||' so a user-controlled string can't break a spoiler wrapper."""
    return text.replace("||", "\u200b|\u200b|\u200b") if text else text


def _first_line(text: Optional[str], max_len: int = 200) -> str:
    """First non-empty line of a string (URLs stripped), truncated. Falls back to placeholder."""
    if not text:
        return "*no solution set*"
    body, _ = extract_first_url(text)
    base = (body or text).strip()
    line = base.split("\n", 1)[0].strip() if base else ""
    return truncate_text(line, max_len) if line else "*no solution set*"


# =============================================================================
# EMBED BUILDERS
# =============================================================================
def build_active_riddle_embed(guild: Optional[discord.Guild], riddle: dict) -> discord.Embed:
    r_no = to_int(riddle.get("riddle_no"), to_int(riddle.get("id"), 0))
    xp = max(0, to_int(riddle.get("xp"), 0))
    e = discord.Embed(
        title=f"🧩 Riddle No.{r_no}",
        description=clamp_embed_description((riddle.get("text") or "*No riddle text set.*").strip()),
        color=discord.Color.blurple(),
    )
    if guild:
        e.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    e.add_field(name="🏆 Award", value=f"{xp} XP", inline=False)
    img = riddle.get("image_url")
    if not is_http_url(img):
        img = DEFAULT_IMAGE_URL
    if is_http_url(img):
        e.set_image(url=img)
    e.set_footer(text=footer_text(guild))
    return e


def build_fresh_solved_post_embed(
    guild: Optional[discord.Guild],
    riddle: dict,
    solver_mention: str,
    solver_display_name: str,
    solver_avatar_url: Optional[str],
    submitted_answer: str,
) -> discord.Embed:
    r_no = to_int(riddle.get("riddle_no"), to_int(riddle.get("id"), 0))
    xp = max(0, to_int(riddle.get("xp"), 0))
    sol_text_raw = (riddle.get("solution") or "").strip()
    sol_text, more_url = extract_first_url(sol_text_raw)
    riddle_text = (riddle.get("text") or "*No text*").strip()

    e = discord.Embed(
        title=f"🎉 Riddle No.{r_no} — Solved!",
        description=clamp_embed_description(
            f"Congratulations {solver_mention}!\n\n"
            f"**Riddle:**\n{riddle_text}"
        ),
        color=discord.Color.gold(),
    )
    if solver_avatar_url:
        e.set_author(name=solver_display_name, icon_url=solver_avatar_url)
    else:
        e.set_author(name=solver_display_name)

    if submitted_answer and submitted_answer.strip():
        safe_ans = _spoiler_safe(submitted_answer.strip())
        e.add_field(
            name="🧠 Winning Answer",
            value=clamp_embed_value(f"||{safe_ans}||"),
            inline=False,
        )

    if sol_text or more_url:
        parts: list[str] = []
        if sol_text:
            parts.append(_spoiler_safe(sol_text))
        if more_url:
            parts.append(f"[🔗 MORE]({more_url})")
        joined = "\n\n".join(parts)
        e.add_field(
            name="✅ Solution",
            value=clamp_embed_value(f"||{joined}||"),
            inline=False,
        )

    e.add_field(name="🏆 Award", value=f"{xp} XP", inline=True)

    r_img = riddle.get("image_url")
    if is_http_url(r_img):
        e.set_thumbnail(url=r_img)
    s_img = riddle.get("solution_url")
    if is_http_url(s_img):
        e.set_image(url=s_img)

    e.set_footer(text=footer_text(guild))
    return e


def build_solved_ping_post_embed(
    guild: Optional[discord.Guild],
    riddle: dict,
    solver_mention: str,
    solver_avatar_url: Optional[str],
) -> discord.Embed:
    r_no = to_int(riddle.get("riddle_no"), to_int(riddle.get("id"), 0))
    xp = max(0, to_int(riddle.get("xp"), 0))
    sol_text_raw = (riddle.get("solution") or "").strip()
    sol_text, more_url = extract_first_url(sol_text_raw)

    e = discord.Embed(
        title=f"🧩 Riddle No.{r_no} — Solved!",
        description=f"Congratulations {solver_mention}! 🎉",
        color=discord.Color.green(),
    )
    if sol_text or more_url:
        parts: list[str] = []
        if sol_text:
            parts.append(sol_text)
        if more_url:
            parts.append(f"[🔗 MORE]({more_url})")
        e.add_field(name="✅ Solution", value=clamp_embed_value("\n\n".join(parts)), inline=False)
    e.add_field(name="🏆 XP", value=f"{xp}", inline=True)
    if solver_avatar_url:
        e.set_thumbnail(url=solver_avatar_url)
    e.set_footer(text=footer_text(guild))
    return e


def build_wrong_post_embed(
    guild: Optional[discord.Guild],
    riddle: dict,
    submitter_mention: str,
    submitter_name: str,
    submitter_avatar_url: Optional[str],
    submitted_answer: str,
) -> discord.Embed:
    r_no = to_int(riddle.get("riddle_no"), to_int(riddle.get("id"), 0))
    xp = max(0, to_int(riddle.get("xp"), 0))
    e = discord.Embed(
        title=f"❌ Wrong Answer — Riddle No.{r_no} still open",
        description=clamp_embed_description(
            f"{submitter_mention}, your submitted solution was rejected.\n"
            f"The riddle is **still open** — keep trying!"
        ),
        color=discord.Color.red(),
    )
    if submitter_avatar_url:
        e.set_author(name=submitter_name, icon_url=submitter_avatar_url)
    else:
        e.set_author(name=submitter_name)
    e.add_field(name="🧩 Riddle", value=clamp_embed_value(riddle.get("text") or "*No text*"), inline=False)
    e.add_field(name="🧠 Submitted Answer", value=clamp_embed_value(submitted_answer or "*empty*"), inline=False)
    e.add_field(name="🏆 Award (still up for grabs)", value=f"{xp} XP", inline=True)
    r_img = riddle.get("image_url")
    if is_http_url(r_img):
        e.set_thumbnail(url=r_img)
    e.set_footer(text=footer_text(guild))
    return e


def build_vote_embed(
    guild: Optional[discord.Guild], riddle: dict, submitter_id: int,
    submitter_name: str, submitter_avatar_url: Optional[str], submitted_answer: str,
) -> discord.Embed:
    e = discord.Embed(
        title="📜 New Solution Submitted",
        description=clamp_embed_description(riddle.get("text") or "*No riddle text*"),
        color=discord.Color.gold(),
    )
    if submitter_avatar_url:
        e.set_author(name=submitter_name, icon_url=submitter_avatar_url)
    else:
        e.set_author(name=submitter_name)
    e.add_field(name="🧠 User Answer", value=clamp_embed_value(submitted_answer or "*empty*"), inline=False)
    e.add_field(name="✅ Correct Solution", value=clamp_embed_value(riddle.get("solution") or "*Not set*"), inline=False)
    e.add_field(name="🏆 Award", value=f"{max(0, to_int(riddle.get('xp'), 0))} XP", inline=True)
    e.add_field(name="🆔 User ID", value=str(submitter_id), inline=True)
    r_img = riddle.get("image_url")
    if is_http_url(r_img):
        e.set_thumbnail(url=r_img)
    e.set_footer(text=footer_text(guild))
    return e


def build_xp_reminder_embed(
    guild: Optional[discord.Guild], solver_mention: str, solver_name: str,
    solver_avatar_url: Optional[str], xp_amount: int, riddle_no: int,
) -> discord.Embed:
    xp = max(0, to_int(xp_amount, 0))
    safe_name = (solver_name or "UnknownUser").replace('"', "").strip() or "UnknownUser"
    e = discord.Embed(
        title="💰 XP Award — Reminder",
        description=clamp_embed_description(
            f"Riddle **No.{riddle_no}** was solved by {solver_mention}.\n"
            f"Please run one of these commands to grant the XP:"
        ),
        color=discord.Color.gold(),
    )
    e.add_field(name="Command (by name)", value=f"`/xp app {xp} {safe_name}`", inline=False)
    e.add_field(name="Command (by mention)", value=f"`/xp app {xp} {solver_mention}`", inline=False)
    e.add_field(name="Amount", value=f"**{xp} XP**", inline=True)
    if solver_avatar_url:
        e.set_thumbnail(url=solver_avatar_url)
    e.set_footer(text=footer_text(guild))
    return e


async def edit_vote_result_message(msg: discord.Message, *, ok: bool, moderator_mention: str):
    try:
        if msg.embeds:
            d = msg.embeds[0].to_dict()
            d["fields"] = [f for f in d.get("fields", []) if f.get("name") not in {"✅ Result", "❌ Result"}]
            e = discord.Embed.from_dict(d)
        else:
            e = discord.Embed(title="📜 Solution Vote")
        if ok:
            e.color = discord.Color.green()
            e.add_field(name="✅ Result", value=clamp_embed_value(f"Approved by {moderator_mention}"), inline=False)
        else:
            e.color = discord.Color.red()
            e.add_field(name="❌ Result", value=clamp_embed_value(f"Rejected by {moderator_mention}"), inline=False)
        await msg.edit(embed=e, view=None)
    except Exception:
        pass


class LoggedPersistentView(View):
    def __init__(self, timeout=None):
        super().__init__(timeout=timeout)

    async def on_error(self, interaction: Interaction, error: Exception, item):
        logger.exception("View error in %s: %s", self.__class__.__name__, error)


# =============================================================================
# MODALS
# =============================================================================
class SubmitSolutionModal(Modal):
    def __init__(self, cog: "RiddleCog", riddle_id: int):
        super().__init__(title="Submit your solution")
        self.cog = cog
        self.riddle_id = riddle_id
        self.answer = TextInput(label="Your Answer", style=discord.TextStyle.paragraph,
                                required=True, max_length=4000)
        self.add_item(self.answer)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction, ephemeral=True):
            return
        riddle = await self.cog.repo.get_open_riddle_by_id(interaction.guild.id, self.riddle_id)
        if not riddle:
            await interaction.followup.send("⚠️ This riddle is no longer active.", ephemeral=True)
            return
        ans = clean_value(str(self.answer.value or ""))
        if not ans:
            await interaction.followup.send("❌ Answer cannot be empty.", ephemeral=True)
            return
        sid = await self.cog.repo.create_submission_pending(
            interaction.guild.id, to_int(riddle["id"], 0), interaction.user.id, ans,
        )
        if not sid:
            await interaction.followup.send("❌ Could not save your submission.", ephemeral=True)
            return
        vote_channel = await self.cog.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            await self.cog.repo.delete_submission(sid)
            await interaction.followup.send("❌ Vote channel not available.", ephemeral=True)
            return
        embed = build_vote_embed(
            interaction.guild, riddle, interaction.user.id,
            str(interaction.user), interaction.user.display_avatar.url, ans,
        )
        try:
            vm = await vote_channel.send(embed=embed, view=VoteButtons(self.cog))
            await self.cog.repo.set_submission_vote_message(sid, vm.id)
        except Exception:
            await self.cog.repo.delete_submission(sid)
            await interaction.followup.send("❌ Failed to post submission for voting.", ephemeral=True)
            return
        await interaction.followup.send("✅ Your solution was submitted for review.", ephemeral=True)


class RiddleContentModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int, riddle_id: Optional[int],
                 current: Optional[dict], ping_preview: str):
        super().__init__(title=f"Slot {slot_no} Content")
        self.panel = panel
        self.slot_no = slot_no
        self.riddle_id = riddle_id
        cur = current or {}
        self.text = TextInput(label="Riddle Text", style=discord.TextStyle.paragraph,
                              default=cur.get("text") or "", required=True, max_length=4000)
        self.solution = TextInput(label="Solution", style=discord.TextStyle.paragraph,
                                  default=cur.get("solution") or "", required=True, max_length=4000)
        self.xp = TextInput(label="XP Reward", default=str(max(0, to_int(cur.get("xp"), 0))),
                            required=True, max_length=10)
        self.pings = TextInput(label="Ping roles (preview, read-only)", default=ping_preview,
                               required=False, max_length=500)
        self.add_item(self.text); self.add_item(self.solution)
        self.add_item(self.xp); self.add_item(self.pings)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction):
            return
        gid = interaction.guild.id
        xp = max(0, to_int(self.xp.value, 0))
        if self.riddle_id:
            changed = await self.panel.cog.repo.update_open_riddle_content_by_id(
                gid, self.riddle_id, interaction.user.id,
                str(self.text.value), str(self.solution.value), xp,
            )
            if not changed:
                self.panel.last_info = "⚠️ Riddle changed while editing."
                await self.panel.safe_edit_panel()
                return
        else:
            rid = await self.panel.cog.repo.upsert_slot_content(
                guild_id=gid, user_id=interaction.user.id, slot_no=self.slot_no,
                text=str(self.text.value), solution=str(self.solution.value), xp=xp,
            )
            if not rid:
                self.panel.last_info = "❌ Save failed."
                await self.panel.safe_edit_panel()
                return
        await self.panel.cog.normalize_after_structure_change(gid)
        if await self.panel.cog.repo.is_enabled(gid):
            await self.panel.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=False)
        self.panel.last_info = "✅ Saved."
        await self.panel.safe_edit_panel()


class RiddleImagesModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int, riddle_id: int, current: dict):
        super().__init__(title=f"Slot {slot_no} Images")
        self.panel = panel
        self.riddle_id = riddle_id
        self.riddle_image = TextInput(label="Riddle Image URL (blank = clear)",
                                      default=current.get("image_url") or "",
                                      required=False, max_length=2000)
        self.solution_image = TextInput(label="Solution Image URL (blank = clear)",
                                        default=current.get("solution_url") or "",
                                        required=False, max_length=2000)
        self.add_item(self.riddle_image); self.add_item(self.solution_image)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction):
            return
        r_img = clean_value(self.riddle_image.value)
        s_img = clean_value(self.solution_image.value)
        if r_img and not is_http_url(r_img):
            self.panel.last_info = "❌ Invalid riddle image URL."
            await self.panel.safe_edit_panel()
            return
        if s_img and not is_http_url(s_img):
            self.panel.last_info = "❌ Invalid solution image URL."
            await self.panel.safe_edit_panel()
            return
        good = await self.panel.cog.repo.set_riddle_images_by_id_open(
            interaction.guild.id, self.riddle_id, r_img, s_img, interaction.user.id,
        )
        self.panel.last_info = "✅ Images updated." if good else "⚠️ Riddle no longer open."
        if good and await self.panel.cog.repo.is_enabled(interaction.guild.id):
            await self.panel.cog.enforce_enabled_state(interaction.guild.id, allow_ping=False, force_repost=False)
        await self.panel.safe_edit_panel()


class ChampionsImportModal(Modal):
    def __init__(self, cog: "RiddleCog", mode: Literal["merge", "replace"]):
        super().__init__(title=f"Import Champions JSON ({mode})")
        self.cog = cog
        self.mode = mode
        self.payload = TextInput(label="Paste JSON", style=discord.TextStyle.paragraph,
                                 required=True, max_length=4000)
        self.add_item(self.payload)

    def parse_rows(self, raw: str) -> list[tuple[int, int, int]]:
        obj = json.loads(raw)
        rows: dict[int, tuple[int, int, int]] = {}

        def put(uid: int, solved: Any, xp: Any):
            if uid <= 0:
                return
            rows[uid] = (uid, max(0, to_int(solved, 0)), max(0, to_int(xp, 0)))

        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    uid = to_int(item.get("user_id") or item.get("id"), 0)
                    put(uid, item.get("solved") or item.get("solved_riddles"), item.get("xp"))
        elif isinstance(obj, dict):
            if "users" in obj and isinstance(obj["users"], list):
                for item in obj["users"]:
                    if isinstance(item, dict):
                        uid = to_int(item.get("user_id") or item.get("id"), 0)
                        put(uid, item.get("solved") or item.get("solved_riddles"), item.get("xp"))
            else:
                for k, v in obj.items():
                    uid = to_int(k, 0)
                    if isinstance(v, dict):
                        put(uid, v.get("solved") or v.get("solved_riddles"), v.get("xp"))
                    elif isinstance(v, (list, tuple)) and len(v) >= 2:
                        put(uid, v[0], v[1])
        return list(rows.values())

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction, ephemeral=True):
            return
        try:
            incoming = self.parse_rows(self.payload.value or "")
        except Exception as e:
            await interaction.followup.send(f"❌ JSON parse error: {e}", ephemeral=True)
            return
        filtered: list[tuple[int, int, int]] = []
        for uid, solved, xp in incoming:
            if await self.cog.user_is_excluded(interaction.guild, uid):
                continue
            filtered.append((uid, solved, xp))
        if self.mode == "replace":
            await self.cog.repo.replace_user_stats(interaction.guild.id, filtered)
        else:
            current = {uid: (uid, solved, xp)
                       for uid, solved, xp in await self.cog.repo.stats_entries(interaction.guild.id)}
            for uid, solved, xp in filtered:
                if uid in current:
                    _, s0, x0 = current[uid]
                    current[uid] = (uid, s0 + solved, x0 + xp)
                else:
                    current[uid] = (uid, solved, xp)
            await self.cog.repo.replace_user_stats(interaction.guild.id, list(current.values()))
        await self.cog.rebuild_cached_solved_total_for_guild(interaction.guild.id)
        await self.cog.sync_open_slot_numbers_for_guild(interaction.guild.id)
        await interaction.followup.send(
            f"✅ Import done ({len(filtered)} rows used, excluded users filtered out).",
            ephemeral=True,
        )


# =============================================================================
# PING ROLES PICKER
# =============================================================================
class PingRolesPickerView(View):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int,
                 riddle_id: int, current_ids: list[int]):
        super().__init__(timeout=180)
        self.panel = panel
        self.slot_no = slot_no
        self.riddle_id = riddle_id
        self.current_ids = current_ids
        self.picker_message: Optional[discord.Message] = None
        self._picked_ids: list[int] = list(current_ids[:MAX_EXTRA_PING_ROLES])

        self.role_select = discord.ui.RoleSelect(
            placeholder=f"Select up to {MAX_EXTRA_PING_ROLES} extra ping roles",
            min_values=0, max_values=MAX_EXTRA_PING_ROLES, row=0,
        )
        try:
            self.role_select.default_values = [
                discord.SelectDefaultValue(id=rid, type=discord.SelectDefaultValueType.role)
                for rid in current_ids[:MAX_EXTRA_PING_ROLES]
            ]
        except Exception:
            pass
        self.role_select.callback = self.on_role_select
        self.add_item(self.role_select)

        save_btn = discord.ui.Button(label="💾 Save Selection", style=discord.ButtonStyle.success, row=1)
        clear_btn = discord.ui.Button(label="🗑 Clear All", style=discord.ButtonStyle.secondary, row=1)
        cancel_btn = discord.ui.Button(label="✖ Cancel", style=discord.ButtonStyle.danger, row=1)
        save_btn.callback = self.on_save
        clear_btn.callback = self.on_clear
        cancel_btn.callback = self.on_cancel
        self.add_item(save_btn); self.add_item(clear_btn); self.add_item(cancel_btn)

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.panel.owner_id

    def _filter_ids(self, ids: list[int]) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for rid in ids:
            if not rid or rid == RIDDLE_ROLE_ID or rid in seen:
                continue
            seen.add(rid); out.append(rid)
            if len(out) >= MAX_EXTRA_PING_ROLES:
                break
        return out

    async def _close_picker(self, interaction: Interaction):
        try:
            if self.picker_message:
                await self.picker_message.delete()
            else:
                await interaction.delete_original_response()
        except Exception:
            pass

    async def on_role_select(self, interaction: Interaction):
        picked = [r.id for r in self.role_select.values]
        self._picked_ids = self._filter_ids(picked)
        if not await safe_defer(interaction, ephemeral=True):
            return
        try:
            preview = ", ".join(f"<@&{r}>" for r in self._picked_ids) or "*none*"
            if self.picker_message:
                await self.picker_message.edit(
                    content=f"**Current selection:** {preview}\nClick **💾 Save Selection** to apply.",
                    view=self,
                )
        except Exception:
            pass

    async def on_save(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction, ephemeral=True):
            return
        ids = self._filter_ids(self._picked_ids)
        csv = ",".join(str(i) for i in ids) if ids else None
        good = await self.panel.cog.repo.set_riddle_mentions_by_id_open(
            interaction.guild.id, self.riddle_id, csv, interaction.user.id,
        )
        self.panel.last_info = (f"✅ Ping roles updated ({len(ids)} extra)."
                                if good else "⚠️ Riddle no longer open.")
        if good and await self.panel.cog.repo.is_enabled(interaction.guild.id):
            await self.panel.cog.enforce_enabled_state(interaction.guild.id, allow_ping=False, force_repost=False)
        await self._close_picker(interaction)
        await self.panel.safe_edit_panel()

    async def on_clear(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction, ephemeral=True):
            return
        good = await self.panel.cog.repo.set_riddle_mentions_by_id_open(
            interaction.guild.id, self.riddle_id, None, interaction.user.id,
        )
        self.panel.last_info = ("✅ Cleared all extra ping roles."
                                if good else "⚠️ Riddle no longer open.")
        if good and await self.panel.cog.repo.is_enabled(interaction.guild.id):
            await self.panel.cog.enforce_enabled_state(interaction.guild.id, allow_ping=False, force_repost=False)
        await self._close_picker(interaction)
        await self.panel.safe_edit_panel()

    async def on_cancel(self, interaction: Interaction):
        if not await safe_defer(interaction, ephemeral=True):
            return
        self.panel.last_info = "Ping roles unchanged."
        await self._close_picker(interaction)
        await self.panel.safe_edit_panel()


# =============================================================================
# PERSISTENT VIEWS
# =============================================================================
class SubmitButton(discord.ui.Button):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(label="🧠 Submit Solution", style=discord.ButtonStyle.primary,
                         custom_id=SUBMIT_BUTTON_ID)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            return
        r = None
        if interaction.message:
            r = await self.cog.repo.get_open_riddle_by_message(interaction.guild.id, interaction.message.id)
        if not r:
            r = await self.cog.repo.get_open_slot1(interaction.guild.id)
        if not r:
            try:
                await interaction.response.send_message("⚠️ There is no active riddle right now.", ephemeral=True)
            except Exception:
                pass
            return
        await interaction.response.send_modal(SubmitSolutionModal(self.cog, to_int(r["id"], 0)))


class SubmitButtonView(LoggedPersistentView):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(timeout=None)
        self.add_item(SubmitButton(cog))


class _VoteBaseButton(discord.ui.Button):
    def __init__(self, cog: "RiddleCog", approve: bool, label: str,
                 style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.approve = approve

    async def callback(self, interaction: Interaction):
        if interaction.guild is None or interaction.message is None:
            return

        if not isinstance(interaction.user, discord.Member) or not member_has_role(
            interaction.user, RIDDLE_MANAGER_ROLE_ID
        ):
            try:
                await interaction.response.send_message("🔒 Only riddle managers may vote.", ephemeral=True)
            except Exception:
                pass
            return

        if not await safe_defer(interaction, ephemeral=True):
            return

        try:
            if self.approve:
                status, ctx = await self.cog.repo.approve_submission(
                    interaction.message.id, interaction.user.id,
                )
            else:
                status, ctx = await self.cog.repo.reject_submission(
                    interaction.message.id, interaction.user.id,
                )
        except Exception:
            logger.exception("Vote button DB error")
            try:
                await interaction.followup.send(
                    "❌ Internal DB error while processing your vote. Check logs.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        if status in {"not_found", "already_done", "riddle_closed"}:
            reason = {
                "not_found":     "This submission is no longer tracked (message/DB out of sync).",
                "already_done":  "This submission was already voted on.",
                "riddle_closed": "The related riddle is already closed.",
            }[status]
            try:
                await interaction.followup.send(f"⚠️ {reason}", ephemeral=True)
            except Exception:
                pass
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            return

        try:
            if self.approve and ctx:
                await self.cog.finalize_correct(interaction.guild, ctx, interaction.user)
            elif not self.approve and ctx:
                await self.cog.finalize_wrong(interaction.guild, ctx, interaction.user)
        except Exception:
            logger.exception("Vote finalize failed")
            try:
                await interaction.followup.send(
                    "❌ Vote was recorded but post-processing crashed. Check logs.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        try:
            await edit_vote_result_message(
                interaction.message, ok=self.approve, moderator_mention=interaction.user.mention,
            )
        except Exception:
            pass

        try:
            await interaction.followup.send(
                ("✅ Vote applied: **Correct** (solved flow triggered)."
                 if self.approve else
                 "✅ Vote applied: **Wrong** (public wrong-answer post sent, riddle stays open)."),
                ephemeral=True,
            )
        except Exception:
            pass


class VoteSuccessButton(_VoteBaseButton):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(cog, True, "👍 Correct", discord.ButtonStyle.success, VOTE_UP_BUTTON_ID)


class VoteFailButton(_VoteBaseButton):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(cog, False, "👎 Wrong", discord.ButtonStyle.danger, VOTE_DOWN_BUTTON_ID)


class VoteButtons(LoggedPersistentView):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))
# riddle_ui.py  (Teil 2/2)

# =============================================================================
# ADMIN PANEL  ( /riddle )
# =============================================================================
class _BaseSlotSelect(Select):
    """Shared behavior for the two slot dropdowns (filled / empty)."""
    _placeholder = "Select slot"
    _kind_label = ""

    def __init__(self, panel: "RiddleAdminPanelView", *, options: list[discord.SelectOption], row: int):
        self.panel = panel
        # If a category is empty, we still need at least 1 option → dummy sentinel.
        if not options:
            options = [discord.SelectOption(
                label=f"(no {self._kind_label} slots)",
                value="_none_", default=True,
            )]
        super().__init__(
            placeholder=self._placeholder,
            min_values=1, max_values=1,
            options=options[:25], row=row,
        )

    async def callback(self, interaction: Interaction):
        val = self.values[0]
        if val == "_none_":
            await safe_defer(interaction)
            return
        self.panel.selected_slot = max(1, min(MAX_RIDDLE_SLOTS, to_int(val, 1)))
        if not await safe_defer(interaction):
            return
        self.panel.last_info = f"Selected {self._kind_label} slot {self.panel.selected_slot}."
        await self.panel.safe_edit_panel()


class FilledSlotsSelect(_BaseSlotSelect):
    _placeholder = "📋 Filled slots — pick to edit"
    _kind_label = "filled"

    def __init__(self, panel: "RiddleAdminPanelView", row: int):
        opts: list[discord.SelectOption] = []
        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            r = panel.slot_map.get(slot)
            if not r:
                continue
            desc = _first_line(r.get("solution"), 90)
            opts.append(discord.SelectOption(
                label=f"Slot {slot}",
                value=str(slot),
                description=desc[:100],
                default=(slot == panel.selected_slot),
                emoji="🧩",
            ))
        super().__init__(panel, options=opts, row=row)


class EmptySlotsSelect(_BaseSlotSelect):
    _placeholder = "🈳 Empty slots — pick to fill"
    _kind_label = "empty"

    def __init__(self, panel: "RiddleAdminPanelView", row: int):
        opts: list[discord.SelectOption] = []
        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            if panel.slot_map.get(slot):
                continue
            opts.append(discord.SelectOption(
                label=f"Slot {slot}",
                value=str(slot),
                description="EMPTY — click to create",
                default=(slot == panel.selected_slot),
                emoji="⬜",
            ))
        super().__init__(panel, options=opts, row=row)


class PanelButton(discord.ui.Button):
    def __init__(self, panel: "RiddleAdminPanelView", label: str, action: str, row: int,
                 style: discord.ButtonStyle = discord.ButtonStyle.primary):
        super().__init__(label=label, style=style, row=row)
        self.panel = panel
        self.action = action

    async def callback(self, interaction: Interaction):
        try:
            await self.panel.handle_action(interaction, self.action)
        except discord.NotFound:
            return
        except discord.HTTPException as e:
            # 50027 / 401 -> ephemeral admin-panel webhook token expired (~15 min)
            if getattr(e, "code", None) == 50027 or getattr(e, "status", None) == 401:
                try:
                    await interaction.response.send_message(
                        "⌛ This admin panel has expired. Please run `/riddle` again.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                return
            logger.exception("Panel button callback failed")
        except Exception:
            logger.exception("Panel button callback failed")


class RiddleAdminPanelView(View):
    def __init__(self, cog: "RiddleCog", owner_id: int, guild_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.selected_slot: int = 1
        self.slot_map: dict[int, dict] = {}
        self.state: dict = {}
        self.last_info: str = "Ready."
        self.message: Optional[discord.Message] = None
        self.show_full_preview: bool = False  # default: compact preview

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def refresh_data(self):
        self.slot_map = await self.cog.repo.open_slot_map(self.guild_id)
        self.state = await self.cog.repo.get_state_row(self.guild_id)

    async def rebuild_items(self):
        self.clear_items()
        enabled = bool(to_int(self.state.get("is_enabled"), 0))

        # Row 0: Filled slots dropdown
        self.add_item(FilledSlotsSelect(self, row=0))
        # Row 1: Empty slots dropdown
        self.add_item(EmptySlotsSelect(self, row=1))

        # Row 2: editing actions
        self.add_item(PanelButton(self, "✏️ Edit Content", "edit_content", 2))
        self.add_item(PanelButton(self, "🖼️ Edit Images", "edit_images", 2))
        self.add_item(PanelButton(self, "🎯 Ping Roles", "edit_mentions", 2))
        self.add_item(PanelButton(self, "↘ Move to End", "move_to_end", 2, discord.ButtonStyle.secondary))

        # Row 3: destructive + on/off + publish
        self.add_item(PanelButton(self, "🗑️ Delete Slot", "delete_slot", 3, discord.ButtonStyle.danger))
        self.add_item(PanelButton(
            self, "🔴 Turn OFF" if enabled else "🟢 Turn ON", "toggle", 3,
            discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
        ))
        self.add_item(PanelButton(self, "📢 Post Now", "post_now", 3))
        self.add_item(PanelButton(self, "🔒 Close Active", "close_active", 3, discord.ButtonStyle.danger))

        # Row 4: view controls
        preview_label = "🔎 Show Full Preview" if not self.show_full_preview else "📇 Show Compact Preview"
        self.add_item(PanelButton(self, preview_label, "toggle_preview", 4, discord.ButtonStyle.secondary))
        self.add_item(PanelButton(self, "🔄 Refresh", "refresh", 4, discord.ButtonStyle.secondary))

    async def build_embeds(self, guild: Optional[discord.Guild]) -> list[discord.Embed]:
        enabled = bool(to_int(self.state.get("is_enabled"), 0))
        solved_cached = await self.cog.repo.get_cached_solved_total(self.guild_id)

        # ---------- Main overview embed ----------
        main = discord.Embed(
            title="🗂️ Riddle Control Center",
            description=(
                f"**{MAX_RIDDLE_SLOTS} slots** • base ping + up to {MAX_EXTRA_PING_ROLES} extra roles\n"
                f"_System auto-STOPs after every solve — turn ON to post the next riddle._"
            ),
            color=discord.Color.green() if enabled else discord.Color.orange(),
        )
        main.add_field(name="System", value="🟢 ON" if enabled else "🟠 OFF", inline=True)
        main.add_field(name="Selected Slot", value=str(self.selected_slot), inline=True)
        main.add_field(name="Solved (cached, filtered)", value=str(solved_cached), inline=True)

        occupied = sum(1 for s in range(1, MAX_RIDDLE_SLOTS + 1) if self.slot_map.get(s))
        main.add_field(
            name="Occupancy",
            value=f"`{occupied}/{MAX_RIDDLE_SLOTS}` slots filled (left-compact)",
            inline=False,
        )

        # Compact one-line-per-slot listing using SOLUTION 1st line only
        lines: list[str] = []
        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            row = self.slot_map.get(slot)
            marker = "🟩" if slot == self.selected_slot else ("⬛" if row else "⬜")
            if not row:
                lines.append(f"{marker} **Slot {slot}** — `EMPTY`")
                continue
            shown_no = solved_cached + slot
            extras = parse_csv_role_ids(row.get("mention_role_ids"))
            xp = to_int(row.get("xp"), 0)
            preview = _first_line(row.get("solution"), 80)
            active_tag = " · ⭐ ACTIVE" if slot == 1 else ""
            lines.append(
                f"{marker} **Slot {slot}** · No.{shown_no} · {xp}XP · +{len(extras)} roles{active_tag} — _{preview}_"
            )
        main.add_field(
            name="Slots",
            value=clamp_embed_value("\n".join(lines)) if lines else "*none*",
            inline=False,
        )
        main.add_field(name="Status", value=clamp_embed_value(self.last_info), inline=False)
        main.set_footer(text=footer_text(guild))

        embeds: list[discord.Embed] = [main]

        # ---------- Selected-slot preview (compact / full toggle) ----------
        row = self.slot_map.get(self.selected_slot)
        if row:
            shown_no = solved_cached + self.selected_slot
            r_url = clean_value(row.get("image_url"))
            s_url = clean_value(row.get("solution_url"))

            if self.show_full_preview:
                # ----- FULL detail preview -----
                preview = discord.Embed(
                    title=f"🔍 Slot {self.selected_slot} · Riddle No.{shown_no}",
                    description=clamp_embed_description(row.get("text") or "*No text*"),
                    color=discord.Color.blurple(),
                )
                extra_ids = parse_csv_role_ids(row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]
                extra_mentions = ", ".join(f"<@&{rid}>" for rid in extra_ids) if extra_ids else "*none*"
                preview.add_field(
                    name="🔔 Ping Roles",
                    value=f"**Base:** <@&{RIDDLE_ROLE_ID}>\n**Extra:** {extra_mentions}",
                    inline=False,
                )
                preview.add_field(name="🏆 XP", value=str(to_int(row.get("xp"), 0)), inline=True)
                preview.add_field(name="🆔 Riddle ID", value=str(to_int(row.get("id"), 0)), inline=True)
                sol = row.get("solution")
                preview.add_field(
                    name="✅ Solution (stored)",
                    value=clamp_embed_value(f"||{sol}||" if sol else "*not set*"),
                    inline=False,
                )
                preview.add_field(
                    name="🖼️ Riddle Image URL",
                    value=clamp_embed_value(r_url or "*not set*"),
                    inline=False,
                )
                preview.add_field(
                    name="🧩 Solution Image URL",
                    value=clamp_embed_value(s_url or "*not set*"),
                    inline=False,
                )
                preview.set_footer(text=f"Full preview · riddle_id={to_int(row.get('id'), 0)}")
                embeds.append(preview)

                if is_http_url(r_url):
                    thumb_r = discord.Embed(title="🖼️ Riddle Image (preview)", color=discord.Color.blurple())
                    thumb_r.set_thumbnail(url=r_url)
                    embeds.append(thumb_r)
                if is_http_url(s_url):
                    thumb_s = discord.Embed(title="🧩 Solution Image (preview)", color=discord.Color.green())
                    thumb_s.set_thumbnail(url=s_url)
                    embeds.append(thumb_s)

            else:
                # ----- COMPACT preview: solution thumbnail + 1st line of solution -----
                first_line = _first_line(row.get("solution"), 200)
                preview = discord.Embed(
                    title=f"🔍 Slot {self.selected_slot} · Riddle No.{shown_no}",
                    description=clamp_embed_description(f"**Solution (1st line):**\n{first_line}"),
                    color=discord.Color.blurple(),
                )
                if is_http_url(s_url):
                    preview.set_thumbnail(url=s_url)
                preview.set_footer(text=f"Compact preview · riddle_id={to_int(row.get('id'), 0)}")
                embeds.append(preview)

        return embeds

    async def safe_edit_panel(self):
        if not self.message:
            return
        await self.refresh_data()
        await self.rebuild_items()
        try:
            await self.message.edit(embeds=await self.build_embeds(self.message.guild), view=self)
        except discord.NotFound:
            self.message = None
            return
        except discord.HTTPException as e:
            if getattr(e, "code", None) == 50027 or getattr(e, "status", None) == 401:
                self.message = None
                self.stop()
                logger.info("Admin panel session expired (invalid webhook token) – user must rerun /riddle.")
                return
            logger.exception("Panel edit failed")
        except Exception:
            logger.exception("Panel edit failed")

    async def on_timeout(self):
        if self.message:
            try:
                for child in self.children:
                    if hasattr(child, "disabled"):
                        child.disabled = True
                await self.message.edit(view=self)
            except Exception:
                pass
        self.stop()

    async def handle_action(self, interaction: Interaction, action: str):
        if interaction.guild is None:
            return
        row = self.slot_map.get(self.selected_slot)

        # --- MODAL actions ---
        if action == "edit_content":
            ping_preview = f"Base: <@&{RIDDLE_ROLE_ID}>"
            if row:
                extras = parse_csv_role_ids(row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]
                ping_preview += "; Extra: " + (", ".join(f"<@&{x}>" for x in extras) if extras else "-")
            else:
                ping_preview += "; Extra: -"
            rid = to_int(row["id"], 0) if row else None
            await interaction.response.send_modal(
                RiddleContentModal(self, self.selected_slot, rid, row, ping_preview)
            )
            return

        if action == "edit_images":
            if not row:
                if not await safe_defer(interaction):
                    return
                self.last_info = "⚠️ Slot empty."
                await self.safe_edit_panel()
                return
            await interaction.response.send_modal(
                RiddleImagesModal(self, self.selected_slot, to_int(row["id"], 0), row)
            )
            return

        if action == "edit_mentions":
            if not row:
                if not await safe_defer(interaction):
                    return
                self.last_info = "⚠️ Slot empty."
                await self.safe_edit_panel()
                return
            if not await safe_defer(interaction, ephemeral=True):
                return
            current_ids = parse_csv_role_ids(row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]
            picker = PingRolesPickerView(self, self.selected_slot, to_int(row["id"], 0), current_ids)
            preview = ", ".join(f"<@&{r}>" for r in current_ids) or "*none*"
            picker.picker_message = await interaction.followup.send(
                content=(
                    f"🎯 **Pick ping roles for Slot {self.selected_slot}**\n"
                    f"Currently set: {preview}\n"
                    f"Base role <@&{RIDDLE_ROLE_ID}> is always pinged and is filtered "
                    f"from the extras automatically."
                ),
                view=picker, ephemeral=True, wait=True,
                allowed_mentions=discord.AllowedMentions(roles=False, users=False, everyone=False),
            )
            return

        # --- generic actions ---
        if not await safe_defer(interaction):
            return
        gid = interaction.guild.id

        if action == "toggle_preview":
            self.show_full_preview = not self.show_full_preview
            self.last_info = "🔎 Preview: FULL." if self.show_full_preview else "📇 Preview: COMPACT."
            await self.safe_edit_panel()
            return

        if action == "refresh":
            await self.cog.normalize_after_structure_change(gid)
            if await self.cog.repo.is_enabled(gid):
                await self.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=False)
            self.last_info = "✅ Refreshed + gaps compacted."
            await self.safe_edit_panel()
            return

        if action == "move_to_end":
            if not row:
                self.last_info = "⚠️ Slot empty."
                await self.safe_edit_panel()
                return
            moved = await self.cog.repo.move_open_riddle_to_end(gid, to_int(row["id"], 0))
            await self.cog.normalize_after_structure_change(gid)
            if await self.cog.repo.is_enabled(gid):
                await self.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=True)
            self.last_info = "✅ Moved to end." if moved else "⚠️ Move failed."
            await self.safe_edit_panel()
            return

        if action == "delete_slot":
            if not row:
                self.last_info = "⚠️ Slot already empty."
            else:
                closed = await self.cog.repo.close_open_riddle_by_id(
                    gid, to_int(row["id"], 0), interaction.user.id
                )
                self.last_info = "✅ Deleted." if closed else "⚠️ Already closed."
            await self.cog.normalize_after_structure_change(gid)
            if await self.cog.repo.is_enabled(gid):
                await self.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=True)
            else:
                await self.cog.remove_active_riddle_posts(gid)
            await self.safe_edit_panel()
            return

        if action == "toggle":
            if await self.cog.repo.is_enabled(gid):
                await self.cog.repo.set_enabled(gid, False)
                await self.cog.remove_active_riddle_posts(gid)
                self.last_info = "✅ System turned OFF."
            else:
                s1 = await self.cog.repo.get_open_slot1(gid)
                if not s1:
                    self.last_info = "⚠️ Cannot turn ON — Slot 1 is empty."
                else:
                    await self.cog.repo.set_enabled(gid, True)
                    res = await self.cog.publish_slot1_post(
                        gid, force_repost=True, allow_role_ping=True
                    )
                    self.last_info = f"✅ System turned ON ({res})."
            await self.safe_edit_panel()
            return

        if action == "post_now":
            s1 = await self.cog.repo.get_open_slot1(gid)
            if not s1:
                self.last_info = "⚠️ Slot 1 is empty."
            else:
                await self.cog.repo.set_enabled(gid, True)
                res = await self.cog.publish_slot1_post(
                    gid, force_repost=True, allow_role_ping=True
                )
                self.last_info = f"✅ Posted now: {res}"
            await self.safe_edit_panel()
            return

        if action == "close_active":
            r = await self.cog.repo.close_slot1_unsolved(gid, interaction.user.id)
            if not r:
                self.last_info = "⚠️ No active riddle in Slot 1."
            else:
                await self.cog.normalize_after_structure_change(gid)
                await self.cog.repo.set_enabled(gid, False)
                await self.cog.remove_active_riddle_posts(gid)
                self.last_info = "✅ Active riddle closed. System OFF."
            await self.safe_edit_panel()
            return


# =============================================================================
# CHAMPIONS VIEW  ( /riddle-champ )
# =============================================================================
class ChampionsView(View):
    def __init__(self, entries: list[tuple[int, int, float, int]], total_solved: int,
                 name_cache: dict[int, str], avatar_cache: dict[int, str],
                 image_url: Optional[str], owner_id: Optional[int]):
        super().__init__(timeout=300)
        self.entries = entries
        self.total_solved = total_solved
        self.name_cache = name_cache
        self.avatar_cache = avatar_cache
        self.page = 0
        self.per_page = 6
        self.max_page = max((len(entries) - 1) // self.per_page, 0)
        self.page1_image_url = image_url if is_http_url(image_url) else DEFAULT_IMAGE_URL
        self.default_image_url = DEFAULT_IMAGE_URL
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None
        self._sync()

    def _sync(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.max_page

    def _name(self, uid: int) -> str:
        return self.name_cache.get(uid, f"User {uid}")

    def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        rows = self.entries[start:start + self.per_page]
        e = discord.Embed(
            title=f"🏆 Riddle Champions — Total solved: {self.total_solved}",
            description=f"Page {self.page + 1}/{self.max_page + 1}",
            color=discord.Color.gold(),
        )
        if rows:
            for i, (uid, solved, percent, xp) in enumerate(rows, start=start + 1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"`#{i}`"
                e.add_field(
                    name=f"{medal} {self._name(uid)}",
                    value=f"🧩 **{solved}** solved · 📊 {percent:.1f}% · 🧠 {xp} XP",
                    inline=False,
                )
        else:
            e.add_field(name="No data", value="No entries yet.", inline=False)
        if self.page == 0 and rows:
            top_uid = rows[0][0]
            av = self.avatar_cache.get(top_uid)
            if av:
                e.set_thumbnail(url=av)
        img = self.page1_image_url if self.page == 0 else self.default_image_url
        if is_http_url(img):
            e.set_image(url=img)
        return e

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            return False
        return True

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)