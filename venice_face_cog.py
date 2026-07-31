# venice_face_cog.py
import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

from venice_shared import (
    SERVER_ANIM_ICON,
    AnimateEphemeralView,
    add_rating_reactions,
    build_progress_embed,
    bytes_to_b64,
    bytes_to_data_url,
    codeblock_safe,
    eta_text,
    extract_image_from_response,
    get_image_limit_for_member,
    get_member_tier,
    get_quota_store,
    looks_like_image,
    refresh_starter_message,
    register_starter_reposter,
    repost_starter_for_channel,
    role_ids_for_tier_and_above,
    run_with_progress,
    seconds_human,
    send_ephemeral,
    send_image_quota_message,
    send_image_with_compression,
    send_tier_locked_message,
    trim,
)

load_dotenv()
logger = logging.getLogger("venice_face_cog")

# =================================================
# ENV
# =================================================
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
VENICE_IMAGE_EDIT_URL = os.getenv(
    "VENICE_IMAGE_EDIT_URL",
    "https://api.venice.ai/api/v1/image/edit",
)

if not VENICE_API_KEY:
    raise RuntimeError("VENICE_API_KEY not set in .env")

# =================================================
# CONFIG - ADJUST HERE
# =================================================
FACE_CHANNEL_ID = 1416468498305126522

# Referenzbild.
# PRIMÄR: lokale Datei (empfohlen - läuft nie ab)
# FALLBACK: URL. Signierte Discord-CDN-Links (?ex=...&hm=...) verfallen
# nach ~24h! Nach einem Bot-Restart danach schlägt JEDE Generierung fehl.
# Beim ersten erfolgreichen Download wird die Datei lokal gecacht.
FACE_REFERENCE_FILE = os.getenv("FACE_REFERENCE_FILE", "assets/piper_face_ref.jpg")
FACE_REFERENCE_URL = os.getenv(
    "FACE_REFERENCE_URL",
    "https://cdn.discordapp.com/attachments/1383652563408392232/1532219730218450965/"
    "piper_close_up-1_nude.jpg?ex=6a6c0e52&is=6a6abcd2&"
    "hm=61096ebefe0fbc3d80dbb39d591cc55aefa042fb37997b4d6ecf669e64ed474e&",
)

# Mindest-Tier für den Face-Generator (nutzt zentrale TIER_RULES)
FACE_REQUIRED_TIER = 2
REQUIRED_ROLE_IDS: set[int] = set(role_ids_for_tier_and_above(FACE_REQUIRED_TIER))

PROMPT_HIDDEN_SUFFIX = (
    " photorealistic, sharp focus, cinematic lighting, high detail."
    " Piper: 20years old woman with pale skin, freckles, green eyes and red bangs."
    " Her mouth has a slight overbite."
    " She wears glowing green wireless headphones without cables."
    " Her height is 155cm."
    " Her skin is sweaty and wet."
    " professional photography."
)

FACE_INSTRUCTION_SUFFIX = (
    " IMPORTANT: The reference image provided shows a specific woman's face. "
    "Whenever a woman appears in the generated scene, she MUST have this exact face "
    "and identity - same facial features, same eyes, same nose, same mouth, same skin tone. "
    "Preserve her facial identity perfectly. Only the environment, pose, clothing and "
    "context should follow the text description; the face itself must remain identical "
    "to the reference."
)

# Ausgabeparameter. None = Feld wird NICHT gesendet (Modell nutzt seinen Default).
# aspect_ratio="auto" ist bei Edit-Endpoints meist KEIN gültiger Enum-Wert;
# ohne das Feld übernimmt das Modell die Maße des Eingangsbildes.
FACE_ASPECT_RATIO: Optional[str] = None
FACE_RESOLUTION: Optional[str] = "1K"
FACE_SAFE_MODE: Optional[bool] = False
FACE_OUTPUT_FORMAT: Optional[str] = "png"

# =================================================
# QUOTA (geteilt mit Image-Cog über die Registry)
# =================================================
IMAGE_QUOTA_FILE = os.getenv("IMAGE_QUOTA_FILE", "goonhut_image_quota.json")
image_quota = get_quota_store(IMAGE_QUOTA_FILE)

# Gelernte Modell-Eigenheiten (überlebt Neustarts)
FACE_CAPS_FILE = os.getenv("FACE_CAPS_FILE", "venice_face_model_caps.json")

# =================================================
# MODELS
# Reihenfolge in MODEL_ORDER = Anzeigereihenfolge.
# Neue Modelle hier eintragen -> Button erscheint automatisch und
# die API-Eigenheiten werden beim ersten Aufruf selbst gelernt.
# =================================================
AB18_ICON = "🔞"
MODELS: dict[str, dict[str, Any]] = {
    "qwen-edit-uncensored": {
        "label": f"🧠 Qwen Edit Uncensored {AB18_ICON}",
        "prompt_limit": 3000, "short": "QEU", "icon": "🧠", "ab18": True,
    },
    "seedream-v5-pro-edit": {
        "label": f"🌊 Seedream V5 Pro Edit {AB18_ICON}",
        "prompt_limit": 5000, "short": "SV5", "icon": "🌊", "ab18": True,
    },
    "nano-banana-2-edit": {
        "label": "🍌 Nano Banana 2 Edit",
        "prompt_limit": 10000, "short": "NB2", "icon": "🍌", "ab18": False,
    },
}
MODEL_ORDER = list(MODELS.keys())

BUTTON_MESSAGE_TEXT = "💡 Pick a model button for a 🎭 face-consistent image!"
LEGACY_STARTER_TEXTS = {
    BUTTON_MESSAGE_TEXT,
    "💡 Choose a model for a 🎭 face-consistent image!",
    "💡 Choose Model for 🎭 face-consistent image!",
}
RECENT_SCAN_LIMIT = 12


# =================================================
# MODEL CAPABILITY STORE (selbstheilender Payload)
# =================================================
# Nur diese Felder dürfen gestrichen werden. model/prompt/image nie.
OPTIONAL_PARAM_KEYS: tuple[str, ...] = (
    "aspect_ratio",
    "resolution",
    "safe_mode",
    "output_format",
    "seed",
    "variants",
    "negative_prompt",
    "strength",
)

IMAGE_STYLE_RAW = "raw"
IMAGE_STYLE_DATAURL = "dataurl"
IMAGE_STYLE_ORDER = (IMAGE_STYLE_RAW, IMAGE_STYLE_DATAURL)


class ModelCapsStore:
    """
    Merkt sich pro Modell:
      - blocked:     optionale Felder, die die API abgelehnt hat
      - image_style: welches Bild-Encoding akzeptiert wurde
    Persistiert als JSON, damit die Lernkurve Neustarts überlebt.
    """

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._dirty = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.file_path.exists():
            return
        try:
            raw = self.file_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                for mid, entry in data.items():
                    if not isinstance(entry, dict):
                        continue
                    blocked = entry.get("blocked") or []
                    style = entry.get("image_style")
                    self._data[str(mid)] = {
                        "blocked": {
                            str(b) for b in blocked if str(b) in OPTIONAL_PARAM_KEYS
                        },
                        "image_style": (
                            style if style in IMAGE_STYLE_ORDER else IMAGE_STYLE_RAW
                        ),
                    }
            logger.info("Model caps loaded for %d model(s)", len(self._data))
        except Exception as e:
            logger.warning("Model caps load failed: %s", e)

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            parent = self.file_path.parent
            if parent and str(parent) != ".":
                parent.mkdir(parents=True, exist_ok=True)
            payload = {
                mid: {
                    "blocked": sorted(entry["blocked"]),
                    "image_style": entry["image_style"],
                }
                for mid, entry in self._data.items()
            }
            tmp = self.file_path.with_suffix(self.file_path.suffix + f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.file_path)
            self._dirty = False
        except Exception as e:
            logger.warning("Model caps save failed: %s", e)

    def _entry(self, model_id: str) -> dict[str, Any]:
        self.load()
        entry = self._data.get(model_id)
        if entry is None:
            entry = {"blocked": set(), "image_style": IMAGE_STYLE_RAW}
            self._data[model_id] = entry
        return entry

    def blocked(self, model_id: str) -> set[str]:
        return set(self._entry(model_id)["blocked"])

    def image_style(self, model_id: str) -> str:
        return self._entry(model_id)["image_style"]

    def block(self, model_id: str, keys: set[str]) -> set[str]:
        """Streicht Felder. Gibt zurück, was davon neu war."""
        entry = self._entry(model_id)
        new = {k for k in keys if k in OPTIONAL_PARAM_KEYS} - entry["blocked"]
        if new:
            entry["blocked"] |= new
            self._dirty = True
            self.save()
        return new

    def set_image_style(self, model_id: str, style: str) -> bool:
        if style not in IMAGE_STYLE_ORDER:
            return False
        entry = self._entry(model_id)
        if entry["image_style"] == style:
            return False
        entry["image_style"] = style
        self._dirty = True
        self.save()
        return True

    def reset(self, model_id: Optional[str] = None) -> None:
        self.load()
        if model_id is None:
            self._data.clear()
        else:
            self._data.pop(model_id, None)
        self._dirty = True
        self.save()

    def describe(self, model_id: str) -> str:
        entry = self._entry(model_id)
        blocked = sorted(entry["blocked"])
        return (
            f"style={entry['image_style']}, "
            f"blocked={','.join(blocked) if blocked else 'none'}"
        )


model_caps = ModelCapsStore(FACE_CAPS_FILE)


def _keys_anywhere(node: Any, out: set[str], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(str(k).lower())
            _keys_anywhere(v, out, depth + 1)
    elif isinstance(node, list):
        for it in node[:40]:
            _keys_anywhere(it, out, depth + 1)


def _collect_rejected_params(body_text: str) -> set[str]:
    """Findet heraus, welche optionalen Felder die API beanstandet hat."""
    bad: set[str] = set()
    found: set[str] = set()

    with contextlib.suppress(Exception):
        _keys_anywhere(json.loads(body_text), found)

    lowered = (body_text or "").lower()
    for key in OPTIONAL_PARAM_KEYS:
        if key in found:
            bad.add(key)
        elif re.search(rf"\b{re.escape(key)}\b", lowered):
            bad.add(key)
    return bad


def _mentions_image_field(body_text: str) -> bool:
    lowered = (body_text or "").lower()
    return bool(re.search(r"\b(image|image_url|input_image|base64|data url)\b", lowered))


def _build_edit_payload(model_id: str, prompt: str, image_bytes: bytes) -> dict[str, Any]:
    blocked = model_caps.blocked(model_id)
    style = model_caps.image_style(model_id)

    payload: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "image": (
            bytes_to_data_url(image_bytes) if style == IMAGE_STYLE_DATAURL
            else bytes_to_b64(image_bytes)
        ),
    }

    optional: dict[str, Any] = {
        "aspect_ratio": FACE_ASPECT_RATIO,
        "resolution": FACE_RESOLUTION,
        "safe_mode": FACE_SAFE_MODE,
        "output_format": FACE_OUTPUT_FORMAT,
    }
    for k, v in optional.items():
        if v is not None and k not in blocked:
            payload[k] = v

    return payload


def _model_param_footer(model_id: str) -> str:
    """Footer zeigt nur, was wirklich gesendet wurde."""
    blocked = model_caps.blocked(model_id)
    bits = []
    if FACE_ASPECT_RATIO and "aspect_ratio" not in blocked:
        bits.append(FACE_ASPECT_RATIO)
    if FACE_RESOLUTION and "resolution" not in blocked:
        bits.append(FACE_RESOLUTION)
    return " • ".join(bits) if bits else "model default"


# =================================================
# ROLE GATE
# =================================================
def user_has_required_role(member: Optional[discord.Member]) -> bool:
    if not isinstance(member, discord.Member):
        return False
    return get_member_tier(member) >= FACE_REQUIRED_TIER


# =================================================
# FACE REFERENCE CACHE
# =================================================
class FaceReferenceCache:
    def __init__(self, local_path: str | Path, url: str = ""):
        self.local_path = Path(local_path)
        self.url = url or ""
        self._raw: Optional[bytes] = None
        self._lock = asyncio.Lock()

    async def get_bytes(
        self, session: Optional[aiohttp.ClientSession], force: bool = False
    ) -> Optional[bytes]:
        if self._raw and not force:
            return self._raw
        async with self._lock:
            if self._raw and not force:
                return self._raw

            # 1) Lokale Datei bevorzugen
            if self.local_path.exists():
                try:
                    raw = await asyncio.to_thread(self.local_path.read_bytes)
                    if looks_like_image(raw):
                        self._raw = raw
                        logger.info(
                            "Face ref from file: %s (%d bytes)", self.local_path, len(raw)
                        )
                        return self._raw
                    logger.error("Face ref file is not an image: %s", self.local_path)
                except Exception as e:
                    logger.error("Face ref file unreadable (%s): %s", self.local_path, e)

            # 2) URL-Fallback + lokal cachen
            if not self.url or session is None or session.closed:
                logger.error("No face reference available (no file, no usable URL/session)")
                return None
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with session.get(self.url, timeout=timeout) as resp:
                    if resp.status != 200:
                        logger.error(
                            "Face ref download HTTP %s - signed CDN link may have expired",
                            resp.status,
                        )
                        return None
                    raw = await resp.read()
                    if not looks_like_image(raw):
                        logger.error("Face ref URL did not return an image")
                        return None

                    def _persist():
                        parent = self.local_path.parent
                        if parent and str(parent) != ".":
                            parent.mkdir(parents=True, exist_ok=True)
                        self.local_path.write_bytes(raw)

                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(_persist)
                        logger.info("Face ref cached locally: %s", self.local_path)

                    self._raw = raw
                    return self._raw
            except Exception as e:
                logger.error("Face ref load error: %s", e)
                return None


face_ref_cache = FaceReferenceCache(FACE_REFERENCE_FILE, FACE_REFERENCE_URL)


# =================================================
# MODEL LABEL HELPERS
# =================================================
def get_model_label(model_id: str) -> str:
    return (MODELS.get(model_id) or {}).get("label", model_id)


def _ordered_model_ids() -> list[str]:
    ordered = [m for m in MODEL_ORDER if m in MODELS]
    extras = [m for m in MODELS if m not in ordered]
    return ordered + extras


def _model_is_ab18(cfg: dict[str, Any]) -> bool:
    label = str(cfg.get("label", ""))
    return bool(cfg.get("ab18")) or ("🔞" in label) or ("18+" in label)


def _model_icon(cfg: dict[str, Any]) -> str:
    icon = str(cfg.get("icon", "")).strip()
    if icon:
        return icon
    label = str(cfg.get("label", "")).strip()
    first = label.split(" ", 1)[0] if label else ""
    return first if first and not first[0].isalnum() else "🎭"


def _model_short(model_id: str, cfg: dict[str, Any]) -> str:
    short = str(cfg.get("short", "")).strip()
    if short:
        return short.upper()[:10]

    parts = [p for p in re.split(r"[-_]+", model_id) if p]
    if len(parts) > 1 and parts[-1].lower() == "edit":
        parts = parts[:-1]

    out: list[str] = []
    for p in parts[:4]:
        if re.fullmatch(r"v\d+", p, flags=re.IGNORECASE):
            out.append(p.upper())
        elif p.isdigit():
            out.append(p)
        else:
            out.append(p[0].upper())
    return ("".join(out) or model_id[:4].upper())[:8]


def _model_button_label(model_id: str) -> str:
    cfg = MODELS.get(model_id, {})
    ab = " 18+" if _model_is_ab18(cfg) else ""
    return f"{_model_icon(cfg)} {_model_short(model_id, cfg)}{ab}".strip()[:80]


def is_starter_message(msg: discord.Message) -> bool:
    if msg.embeds or msg.attachments:
        return False
    if msg.components:
        for row in msg.components:
            for child in row.children:
                cid = getattr(child, "custom_id", None)
                if isinstance(cid, str) and (
                    cid.startswith("venice_face_model_select:")  # legacy select
                    or cid.startswith("venice_face_model_btn:")  # button system
                ):
                    return True
    return (msg.content or "").strip() in LEGACY_STARTER_TEXTS


def _face_progress_embed(
    user: discord.abc.User,
    prompt: str,
    model_id: str,
    percent: int,
    eta_sec: float,
    stage: str,
    quota: dict[str, int],
) -> discord.Embed:
    return build_progress_embed(
        title="🎭 FACE IMAGE RENDER",
        color=discord.Color.purple(),
        user=user,
        prompt=prompt,
        percent=percent,
        status_lines=[stage, f"ETA: `{eta_text(eta_sec)}`"],
        quota_name="Quota (24h, shared)",
        quota_used=int(quota["used"]),
        quota_limit=int(quota["limit"]),
        quota_remaining=int(quota["remaining"]),
        footer=f"{get_model_label(model_id)} • {_model_param_footer(model_id)}",
    )


# =================================================
# API CALL - selbstheilend
# =================================================
async def venice_edit(
    session: aiohttp.ClientSession,
    model_id: str,
    prompt: str,
    image_bytes: bytes,
    retries: int = 2,
) -> tuple[Optional[bytes], Optional[str]]:
    headers = {
        "Authorization": f"Bearer {VENICE_API_KEY}",
        "Content-Type": "application/json",
    }
    req_id = uuid.uuid4().hex[:8]
    timeout = aiohttp.ClientTimeout(total=180, connect=20, sock_read=150)

    last_error: Optional[str] = None
    heals = 0
    max_heals = len(OPTIONAL_PARAM_KEYS) + len(IMAGE_STYLE_ORDER)
    attempt = 0

    while attempt <= retries and heals <= max_heals:
        payload = _build_edit_payload(model_id, prompt, image_bytes)
        logger.info(
            "[FACE %s] -> POST model=%s keys=%s prompt_len=%d caps(%s)",
            req_id, model_id, sorted(payload.keys()), len(prompt),
            model_caps.describe(model_id),
        )
        try:
            async with session.post(
                VENICE_IMAGE_EDIT_URL, headers=headers, json=payload, timeout=timeout
            ) as resp:
                if resp.status == 200:
                    img = await extract_image_from_response(resp)
                    if img and looks_like_image(img):
                        return img, None
                    last_error = "Empty/invalid image response"
                    attempt += 1
                    continue

                body = ""
                with contextlib.suppress(Exception):
                    body = await resp.text()

                logger.error(
                    "[FACE %s] status=%s keys=%s body=%s",
                    req_id, resp.status, sorted(payload.keys()), body[:1200],
                )

                # --- Selbstheilung bei Validierungsfehlern ---
                if resp.status in (400, 415, 422):
                    rejected = _collect_rejected_params(body)
                    if rejected:
                        newly = model_caps.block(model_id, rejected)
                        if newly:
                            heals += 1
                            logger.warning(
                                "[FACE %s] model=%s rejects %s -> removed, retrying",
                                req_id, model_id, sorted(newly),
                            )
                            continue

                    # Bild-Encoding umschalten
                    if _mentions_image_field(body):
                        current = model_caps.image_style(model_id)
                        for style in IMAGE_STYLE_ORDER:
                            if style != current and model_caps.set_image_style(model_id, style):
                                heals += 1
                                logger.warning(
                                    "[FACE %s] model=%s image style %s -> %s, retrying",
                                    req_id, model_id, current, style,
                                )
                                break
                        else:
                            return None, f"HTTP {resp.status}: {body[:400]}"
                        continue

                    # Kein Feldname erkennbar: minimalen Payload versuchen
                    remaining = set(OPTIONAL_PARAM_KEYS) - model_caps.blocked(model_id)
                    if remaining:
                        model_caps.block(model_id, remaining)
                        heals += 1
                        logger.warning(
                            "[FACE %s] model=%s: minimal payload attempt", req_id, model_id
                        )
                        continue

                    return None, f"HTTP {resp.status}: {body[:400]}"

                if resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    attempt += 1
                    continue

                return None, f"HTTP {resp.status}: {body[:400]}"

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = f"Transport: {e}"
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                attempt += 1
                continue
            return None, last_error
        except Exception as e:
            logger.exception("[FACE %s] unexpected: %s", req_id, e)
            return None, f"Unexpected: {e}"

    return None, last_error or "All attempts failed"


async def send_role_locked_message(interaction: discord.Interaction):
    await send_tier_locked_message(
        interaction, FACE_REQUIRED_TIER, "The face-consistent image generator"
    )


# =================================================
# UI - STARTER (ein Button pro Modell)
# =================================================
class StarterModelButton(discord.ui.Button):
    def __init__(
        self,
        session_ref: Callable[[], Optional[aiohttp.ClientSession]],
        channel_id: int,
        model_id: str,
        row: int = 0,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ):
        self._session_ref = session_ref
        self.channel_id = channel_id
        self.model_id = model_id
        super().__init__(
            label=_model_button_label(model_id),
            style=style,
            custom_id=f"venice_face_model_btn:{channel_id}:{model_id}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not user_has_required_role(member):
            await send_role_locked_message(interaction)
            return

        if self.model_id not in MODELS:
            await send_ephemeral(interaction, "❌ Unknown model.")
            return

        session = self._session_ref()
        if session is None or session.closed:
            await send_ephemeral(
                interaction, "❌ Backend session not ready. Try again in a moment."
            )
            return

        await interaction.response.send_modal(
            FacePromptModal(session, self.model_id, interaction.user.id)
        )


class StarterView(discord.ui.View):
    def __init__(
        self,
        session_ref: Callable[[], Optional[aiohttp.ClientSession]],
        channel_id: int,
    ):
        super().__init__(timeout=None)
        model_ids = _ordered_model_ids()

        if not model_ids:
            self.add_item(discord.ui.Button(
                label="No models available",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                row=0,
            ))
            return

        # Discord max: 5 Reihen x 5 Buttons = 25
        for i, mid in enumerate(model_ids[:25]):
            self.add_item(StarterModelButton(
                session_ref=session_ref,
                channel_id=channel_id,
                model_id=mid,
                row=i // 5,
                style=discord.ButtonStyle.primary if i == 0 else discord.ButtonStyle.secondary,
            ))


# =================================================
# UI - MODAL
# =================================================
class FacePromptModal(discord.ui.Modal):
    def __init__(self, session: aiohttp.ClientSession, model_id: str, owner_id: int):
        self.session = session
        self.model_id = model_id
        self.owner_id = owner_id
        cfg = MODELS[model_id]
        super().__init__(title=f"🎭 {get_model_label(model_id)}"[:45])

        self.prompt = discord.ui.TextInput(
            label="Describe the scene",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=min(int(cfg["prompt_limit"]), 4000),
            placeholder="Woman in a red dress on a rainy Tokyo street at night...",
        )
        self.add_item(self.prompt)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This modal does not belong to you.")
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not user_has_required_role(member):
            await send_role_locked_message(interaction)
            return

        user_prompt = (self.prompt.value or "").strip()
        if not user_prompt:
            await send_ephemeral(interaction, "❌ Empty prompt.")
            return

        await interaction.response.defer(ephemeral=True)
        await run_face_generation(
            interaction=interaction,
            session=self.session,
            model_id=self.model_id,
            user_prompt=user_prompt,
        )


# =================================================
# GENERATION FLOW
# =================================================
async def run_face_generation(
    interaction: discord.Interaction,
    session: aiohttp.ClientSession,
    model_id: str,
    user_prompt: str,
):
    if not interaction.guild:
        await send_ephemeral(interaction, "❌ Server-only.")
        return
    if not interaction.channel:
        await send_ephemeral(interaction, "❌ Channel unavailable.")
        return

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if not user_has_required_role(member):
        await send_role_locked_message(interaction)
        return

    progress_msg: Optional[discord.Message] = None
    quota_success = False
    token_quota: Optional[dict[str, int]] = None

    try:
        image_limit = get_image_limit_for_member(member)
        ok, state, token_quota = await image_quota.reserve(
            interaction.guild.id, interaction.user.id, image_limit, 1
        )
        if not ok:
            await send_image_quota_message(interaction, member, state)
            return

        ref_bytes = await face_ref_cache.get_bytes(session)
        if not ref_bytes:
            await send_ephemeral(
                interaction,
                "❌ Could not load the face reference image.\n"
                f"Place it at `{FACE_REFERENCE_FILE}` "
                "(signed Discord CDN links expire after ~24h).",
            )
            return

        full_prompt = (
            f"{user_prompt.strip()}{PROMPT_HIDDEN_SUFFIX}{FACE_INSTRUCTION_SUFFIX}"
        ).strip()

        est_time = 35.0
        progress_msg = await send_ephemeral(
            interaction,
            embed=_face_progress_embed(
                interaction.user, user_prompt, model_id, 0, est_time,
                "Sending request to Venice...", state,
            ),
        )

        gen_task = asyncio.create_task(
            venice_edit(session, model_id, full_prompt, ref_bytes)
        )

        def gen_embed(percent: int, eta: float) -> discord.Embed:
            return _face_progress_embed(
                interaction.user, user_prompt, model_id, percent, eta,
                "Generating image with face reference...", state,
            )

        await run_with_progress(gen_task, progress_msg, est_time, 0, 97, gen_embed, 6.0)
        image_bytes, err = await gen_task

        if not image_bytes:
            await send_ephemeral(
                interaction,
                f"❌ Generation failed.\n```{codeblock_safe(trim(err or 'unknown error', 900))}```",
            )
            return

        if progress_msg:
            with contextlib.suppress(Exception):
                await progress_msg.edit(
                    content=None,
                    embed=_face_progress_embed(
                        interaction.user, user_prompt, model_id, 100, 0.0,
                        "Uploading...", state,
                    ),
                )

        embed = discord.Embed(
            title="🎭 Face Image",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=f"{interaction.user.display_name} • {datetime.now().strftime('%Y-%m-%d')}",
            icon_url=interaction.user.display_avatar.url,
        )
        embed.add_field(
            name="Prompt",
            value=f"```{codeblock_safe(trim(user_prompt, 1600))}```",
            inline=False,
        )
        guild_icon = (
            interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        )
        embed.set_footer(
            text=f"{get_model_label(model_id)} • {_model_param_footer(model_id)}",
            icon_url=guild_icon,
        )

        posted = await send_image_with_compression(
            channel=interaction.channel,
            interaction=interaction,
            image_bytes=image_bytes,
            embed=embed,
            content=f"{SERVER_ANIM_ICON} 🎭 **Face Image** • {interaction.user.mention}",
            filename_prompt=user_prompt,
            filename_fallback="faceimg",
        )
        if posted is None:
            await send_ephemeral(interaction, "❌ Upload failed after compression retries.")
            return

        quota_success = True
        await add_rating_reactions(posted)

        quota_now = await image_quota.peek(
            interaction.guild.id, interaction.user.id, image_limit
        )
        await send_ephemeral(
            interaction,
            content=(
                f"✅ Face image created.\n"
                f"Remaining in 24h (shared): "
                f"**{quota_now['remaining']} / {quota_now['limit']}** "
                f"(reset in **{seconds_human(int(quota_now['reset_in']))}**).\n"
                f"Use the button below if you want to animate this image."
            ),
            view=AnimateEphemeralView(
                owner_id=interaction.user.id,
                source_channel_id=interaction.channel.id,
                source_message_id=posted.id,
                prompt_text=user_prompt,
                ratio="16:9",
            ),
        )

    finally:
        if progress_msg:
            with contextlib.suppress(Exception):
                await progress_msg.delete()

        if not quota_success:
            await image_quota.rollback(token_quota)

        with contextlib.suppress(Exception):
            await repost_starter_for_channel(interaction.channel)


# =================================================
# COG
# =================================================
class VeniceFaceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._ready_bootstrap_done = False
        self._ready_lock = asyncio.Lock()

    def _session_ref(self) -> Optional[aiohttp.ClientSession]:
        return self.session

    async def _ensure_session(self):
        if self.session and not self.session.closed:
            return
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=300),
            connector=aiohttp.TCPConnector(limit=30, ttl_dns_cache=300),
        )

    async def cog_load(self):
        await self._ensure_session()
        model_caps.load()
        self.bot.add_view(StarterView(self._session_ref, FACE_CHANNEL_ID))
        register_starter_reposter(FACE_CHANNEL_ID, self.ensure_starter_message)

    def cog_unload(self):
        model_caps.save()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def ensure_starter_message(self, channel: discord.TextChannel):
        await self._ensure_session()
        await refresh_starter_message(
            channel=channel,
            bot_user_id=(self.bot.user.id if self.bot.user else None),
            content=BUTTON_MESSAGE_TEXT,
            view_factory=lambda: StarterView(self._session_ref, channel.id),
            matcher=is_starter_message,
            scan_limit=RECENT_SCAN_LIMIT,
        )

    @commands.command(name="face_reload")
    @commands.has_permissions(administrator=True)
    async def face_reload(self, ctx: commands.Context):
        await self._ensure_session()
        ref = await face_ref_cache.get_bytes(self.session, force=True)

        reposted = 0
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.id == FACE_CHANNEL_ID:
                    await self.ensure_starter_message(channel)
                    reposted += 1

        ref_state = f"✅ {len(ref)} bytes" if ref else "❌ not available"
        await ctx.send(
            f"✅ Face cog reloaded.\n"
            f"• Reference: {ref_state}\n"
            f"• Starter messages reposted: {reposted}"
        )

    @commands.command(name="face_caps")
    @commands.has_permissions(administrator=True)
    async def face_caps(self, ctx: commands.Context, model_id: Optional[str] = None):
        """Zeigt oder verwirft die gelernten Modell-Eigenheiten."""
        if model_id and model_id.lower() in ("reset", "clear"):
            model_caps.reset()
            await ctx.send("♻️ All learned model capabilities cleared.")
            return
        if model_id:
            if model_id not in MODELS:
                await ctx.send(f"❌ Unknown model `{model_id}`.")
                return
            model_caps.reset(model_id)
            await ctx.send(f"♻️ Learned capabilities for `{model_id}` cleared.")
            return

        lines = [f"`{mid}` → {model_caps.describe(mid)}" for mid in _ordered_model_ids()]
        await ctx.send("🧠 **Learned model capabilities**\n" + "\n".join(lines))

    @commands.command(name="face_test")
    @commands.has_permissions(administrator=True)
    async def face_test(self, ctx: commands.Context):
        """Testet alle Modelle mit einem Minimal-Prompt (verbraucht Quota-frei)."""
        await self._ensure_session()
        ref = await face_ref_cache.get_bytes(self.session)
        if not ref:
            await ctx.send("❌ No face reference available - cannot test.")
            return

        status = await ctx.send("🧪 Testing models...")
        results: list[str] = []
        for mid in _ordered_model_ids():
            img, err = await venice_edit(
                self.session, mid, "a simple portrait test", ref, retries=0
            )
            if img:
                results.append(f"✅ `{mid}` → {len(img)} bytes • {model_caps.describe(mid)}")
            else:
                results.append(f"❌ `{mid}` → {trim(err or 'unknown', 160)}")

        with contextlib.suppress(Exception):
            await status.delete()
        await ctx.send("🧪 **Model test results**\n" + "\n".join(results))

    @commands.Cog.listener()
    async def on_ready(self):
        async with self._ready_lock:
            if self._ready_bootstrap_done:
                return
            self._ready_bootstrap_done = True
            await self._ensure_session()
            model_caps.load()
            with contextlib.suppress(Exception):
                await face_ref_cache.get_bytes(self.session)
            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if channel.id == FACE_CHANNEL_ID:
                        await self.ensure_starter_message(channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceFaceCog(bot))