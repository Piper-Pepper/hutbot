# venice_face_cog.py
import asyncio
import contextlib
import json
import logging
import os
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp
import discord
from discord import app_commands
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
# CONFIG
# =================================================
FACE_CHANNEL_ID = 1416468498305126522
OWNER_USER_ID = 1292194320786522223

# Legacy-Fallback (falls Pool leer)
FACE_REFERENCE_FILE = os.getenv("FACE_REFERENCE_FILE", "assets/piper_face_ref.jpg")
FACE_REFERENCE_URL = os.getenv("FACE_REFERENCE_URL", "")

# Pool-System
FACE_POOL_FILE = os.getenv("FACE_POOL_FILE", "venice_face_pool.json")
FACE_POOL_DIR = Path(os.getenv("FACE_POOL_DIR", "assets/face_pool"))

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
    " IMPORTANT: The primary reference image shows a specific woman's face. "
    "Whenever a woman appears in the generated scene, she MUST have this exact face "
    "and identity - same facial features, same eyes, same nose, same mouth, same skin tone. "
    "Preserve her facial identity perfectly. If additional reference images are provided, "
    "use them as guidance for body shape or clothing while keeping the face from the primary reference."
)

FACE_ASPECT_RATIO: Optional[str] = None
FACE_RESOLUTION: Optional[str] = "1K"
FACE_SAFE_MODE: Optional[bool] = False
FACE_OUTPUT_FORMAT: Optional[str] = "png"

# =================================================
# QUOTA
# =================================================
IMAGE_QUOTA_FILE = os.getenv("IMAGE_QUOTA_FILE", "goonhut_image_quota.json")
image_quota = get_quota_store(IMAGE_QUOTA_FILE)

FACE_CAPS_FILE = os.getenv("FACE_CAPS_FILE", "venice_face_model_caps.json")

# =================================================
# MODELS — hier stehen jetzt die neuen Button-Namen
# =================================================
MODELS: dict[str, dict[str, Any]] = {
    "qwen-edit-uncensored": {
        "label": "🔞 See Piper nude (Variant 1)",
        "button_label": "🔞 See Piper nude (V1)",
        "prompt_limit": 3000, "ab18": True,
    },
    "seedream-v5-pro-edit": {
        "label": "🔞 See Piper nude (Variant 2)",
        "button_label": "🔞 See Piper nude (V2)",
        "prompt_limit": 5000, "ab18": True,
    },
    "nano-banana-2-edit": {
        "label": "👗 See Piper clothed",
        "button_label": "👗 See Piper clothed",
        "prompt_limit": 10000, "ab18": False,
    },
}
MODEL_ORDER = list(MODELS.keys())

BUTTON_MESSAGE_TEXT = "💡 Pick a mode below to generate a 🎭 face-consistent image of Piper!"
LEGACY_STARTER_TEXTS = {
    BUTTON_MESSAGE_TEXT,
    "💡 Pick a model button for a 🎭 face-consistent image!",
    "💡 Choose a model for a 🎭 face-consistent image!",
    "💡 Choose Model for 🎭 face-consistent image!",
}
RECENT_SCAN_LIMIT = 12


# =================================================
# MODEL CAPS STORE (self-healing payload)
# =================================================
OPTIONAL_PARAM_KEYS: tuple[str, ...] = (
    "aspect_ratio", "resolution", "safe_mode", "output_format",
    "seed", "variants", "negative_prompt", "strength",
    "reference_images",
)

IMAGE_STYLE_RAW = "raw"
IMAGE_STYLE_DATAURL = "dataurl"
IMAGE_STYLE_ORDER = (IMAGE_STYLE_RAW, IMAGE_STYLE_DATAURL)


class ModelCapsStore:
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
                        "blocked": {str(b) for b in blocked if str(b) in OPTIONAL_PARAM_KEYS},
                        "image_style": style if style in IMAGE_STYLE_ORDER else IMAGE_STYLE_RAW,
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
                mid: {"blocked": sorted(e["blocked"]), "image_style": e["image_style"]}
                for mid, e in self._data.items()
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
        return f"style={entry['image_style']}, blocked={','.join(blocked) if blocked else 'none'}"


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


def _encode_image(image_bytes: bytes, style: str) -> str:
    return bytes_to_data_url(image_bytes) if style == IMAGE_STYLE_DATAURL else bytes_to_b64(image_bytes)


def _build_edit_payload(
    model_id: str,
    prompt: str,
    face_bytes: bytes,
    secondary_bytes: Optional[bytes] = None,
) -> dict[str, Any]:
    blocked = model_caps.blocked(model_id)
    style = model_caps.image_style(model_id)

    payload: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "image": _encode_image(face_bytes, style),
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

    if secondary_bytes and "reference_images" not in blocked:
        payload["reference_images"] = [_encode_image(secondary_bytes, style)]

    return payload


def _model_param_footer(model_id: str) -> str:
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
# LEGACY FACE REFERENCE CACHE (Fallback)
# =================================================
class FaceReferenceCache:
    def __init__(self, local_path: str | Path, url: str = ""):
        self.local_path = Path(local_path)
        self.url = url or ""
        self._raw: Optional[bytes] = None
        self._lock = asyncio.Lock()

    async def get_bytes(self, session: Optional[aiohttp.ClientSession], force: bool = False) -> Optional[bytes]:
        if self._raw and not force:
            return self._raw
        async with self._lock:
            if self._raw and not force:
                return self._raw
            if self.local_path.exists():
                try:
                    raw = await asyncio.to_thread(self.local_path.read_bytes)
                    if looks_like_image(raw):
                        self._raw = raw
                        return self._raw
                except Exception as e:
                    logger.error("Legacy face ref unreadable: %s", e)
            if not self.url or session is None or session.closed:
                return None
            try:
                async with session.get(self.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        return None
                    raw = await resp.read()
                    if not looks_like_image(raw):
                        return None
                    with contextlib.suppress(Exception):
                        self.local_path.parent.mkdir(parents=True, exist_ok=True)
                        await asyncio.to_thread(self.local_path.write_bytes, raw)
                    self._raw = raw
                    return self._raw
            except Exception as e:
                logger.error("Legacy face ref load error: %s", e)
                return None


face_ref_cache = FaceReferenceCache(FACE_REFERENCE_FILE, FACE_REFERENCE_URL)


# =================================================
# FACE POOL STORE — die neue zentrale Verwaltung
# =================================================
class FacePoolStore:
    """
    3 Face-Slots + 1 Body-Slot + 1 Clothing-Slot.
    Bilder liegen als Datei in FACE_POOL_DIR, Konfiguration in JSON.
    """

    def __init__(self, config_file: Path | str, storage_dir: Path | str):
        self.config_file = Path(config_file)
        self.storage_dir = Path(storage_dir)
        self._data: dict[str, Any] = {
            "face_slots": [None, None, None],
            "body_slot": None,
            "clothing_slot": None,
        }
        self._loaded = False
        self._lock = asyncio.Lock()

    def _ensure_dir(self):
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def load(self):
        if self._loaded:
            return
        self._loaded = True
        if not self.config_file.exists():
            return
        try:
            raw = self.config_file.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                slots = data.get("face_slots")
                if isinstance(slots, list) and len(slots) == 3:
                    self._data["face_slots"] = [s if isinstance(s, str) else None for s in slots]
                body = data.get("body_slot")
                self._data["body_slot"] = body if isinstance(body, str) else None
                cloth = data.get("clothing_slot")
                self._data["clothing_slot"] = cloth if isinstance(cloth, str) else None
        except Exception as e:
            logger.warning("Face pool load failed: %s", e)

    def save(self):
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config_file.with_suffix(f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.config_file)
        except Exception as e:
            logger.warning("Face pool save failed: %s", e)

    def _slot_key(self, slot_type: str, index: Optional[int]) -> tuple[str, str]:
        if slot_type == "face":
            assert index is not None and 0 <= index < 3
            return f"face_slots[{index}]", f"face_{index+1}"
        if slot_type == "body":
            return "body_slot", "body"
        if slot_type == "clothing":
            return "clothing_slot", "clothing"
        raise ValueError(f"unknown slot: {slot_type}")

    def _remove_stale_variants(self, basename: str, keep: Path):
        for ext in ("png", "jpg", "jpeg", "webp"):
            p = self.storage_dir / f"{basename}.{ext}"
            if p != keep and p.exists():
                with contextlib.suppress(Exception):
                    p.unlink()

    def _set_path(self, slot_type: str, index: Optional[int], path: Optional[str]):
        if slot_type == "face":
            self._data["face_slots"][index] = path
        elif slot_type == "body":
            self._data["body_slot"] = path
        elif slot_type == "clothing":
            self._data["clothing_slot"] = path

    async def set_slot(
        self, slot_type: str, index: Optional[int], image_bytes: bytes, extension: str = "png"
    ) -> str:
        async with self._lock:
            self.load()
            self._ensure_dir()
            _, basename = self._slot_key(slot_type, index)
            ext = extension.lower().lstrip(".")
            if ext not in ("png", "jpg", "jpeg", "webp"):
                ext = "png"
            path = self.storage_dir / f"{basename}.{ext}"
            await asyncio.to_thread(path.write_bytes, image_bytes)
            self._remove_stale_variants(basename, path)
            self._set_path(slot_type, index, str(path))
            self.save()
            return str(path)

    async def clear_slot(self, slot_type: str, index: Optional[int] = None):
        async with self._lock:
            self.load()
            _, basename = self._slot_key(slot_type, index)
            current = self.get_path(slot_type, index)
            if current:
                with contextlib.suppress(Exception):
                    Path(current).unlink()
            for ext in ("png", "jpg", "jpeg", "webp"):
                p = self.storage_dir / f"{basename}.{ext}"
                if p.exists():
                    with contextlib.suppress(Exception):
                        p.unlink()
            self._set_path(slot_type, index, None)
            self.save()

    def get_path(self, slot_type: str, index: Optional[int] = None) -> Optional[str]:
        self.load()
        if slot_type == "face":
            return self._data["face_slots"][index]
        if slot_type == "body":
            return self._data["body_slot"]
        if slot_type == "clothing":
            return self._data["clothing_slot"]
        return None

    def get_face_slots(self) -> list[Optional[str]]:
        self.load()
        return list(self._data["face_slots"])

    async def read_random_face_bytes(self) -> tuple[Optional[bytes], Optional[int]]:
        """Wählt zufällig einen befüllten Face-Slot. Gibt (bytes, slot_index) zurück."""
        slots = [(i, p) for i, p in enumerate(self.get_face_slots()) if p and Path(p).exists()]
        if not slots:
            return None, None
        i, chosen = random.choice(slots)
        try:
            raw = await asyncio.to_thread(Path(chosen).read_bytes)
            return raw, i
        except Exception as e:
            logger.error("Face slot read failed: %s", e)
            return None, None

    async def read_secondary_bytes(self, is_nsfw: bool) -> Optional[bytes]:
        """NSFW → body, SFW → clothing."""
        path = self.get_path("body" if is_nsfw else "clothing")
        if not path or not Path(path).exists():
            return None
        try:
            return await asyncio.to_thread(Path(path).read_bytes)
        except Exception as e:
            logger.error("Secondary slot read failed: %s", e)
            return None


face_pool = FacePoolStore(FACE_POOL_FILE, FACE_POOL_DIR)


# =================================================
# HELPERS
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


def _model_button_label(model_id: str) -> str:
    cfg = MODELS.get(model_id, {})
    return str(cfg.get("button_label") or cfg.get("label") or model_id)[:80]


def is_starter_message(msg: discord.Message) -> bool:
    if msg.embeds or msg.attachments:
        return False
    if msg.components:
        for row in msg.components:
            for child in row.children:
                cid = getattr(child, "custom_id", None)
                if isinstance(cid, str) and (
                    cid.startswith("venice_face_model_select:")
                    or cid.startswith("venice_face_model_btn:")
                ):
                    return True
    return (msg.content or "").strip() in LEGACY_STARTER_TEXTS


def _face_progress_embed(user, prompt, model_id, percent, eta_sec, stage, quota) -> discord.Embed:
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
# API CALL — self-healing, jetzt mit optionalem secondary image
# =================================================
async def venice_edit(
    session: aiohttp.ClientSession,
    model_id: str,
    prompt: str,
    face_bytes: bytes,
    secondary_bytes: Optional[bytes] = None,
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
        payload = _build_edit_payload(model_id, prompt, face_bytes, secondary_bytes)
        logger.info(
            "[FACE %s] -> POST model=%s keys=%s prompt_len=%d secondary=%s caps(%s)",
            req_id, model_id, sorted(payload.keys()), len(prompt),
            "yes" if secondary_bytes else "no", model_caps.describe(model_id),
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

                if resp.status in (400, 415, 422):
                    rejected = _collect_rejected_params(body)
                    if rejected:
                        newly = model_caps.block(model_id, rejected)
                        if newly:
                            heals += 1
                            continue

                    if _mentions_image_field(body):
                        current = model_caps.image_style(model_id)
                        healed = False
                        for style in IMAGE_STYLE_ORDER:
                            if style != current and model_caps.set_image_style(model_id, style):
                                heals += 1
                                healed = True
                                break
                        if healed:
                            continue
                        return None, f"HTTP {resp.status}: {body[:400]}"

                    remaining = set(OPTIONAL_PARAM_KEYS) - model_caps.blocked(model_id)
                    if remaining:
                        model_caps.block(model_id, remaining)
                        heals += 1
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
# STARTER (Buttons) — unverändert in Logik, neue Labels
# =================================================
class StarterModelButton(discord.ui.Button):
    def __init__(self, session_ref, channel_id, model_id, row=0,
                 style=discord.ButtonStyle.secondary):
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
            await send_ephemeral(interaction, "❌ Backend session not ready. Try again in a moment.")
            return
        await interaction.response.send_modal(
            FacePromptModal(session, self.model_id, interaction.user.id)
        )


class StarterView(discord.ui.View):
    def __init__(self, session_ref, channel_id: int):
        super().__init__(timeout=None)
        ids = _ordered_model_ids()
        if not ids:
            self.add_item(discord.ui.Button(
                label="No models available",
                style=discord.ButtonStyle.secondary,
                disabled=True, row=0,
            ))
            return
        for i, mid in enumerate(ids[:25]):
            is_nsfw = _model_is_ab18(MODELS[mid])
            style = discord.ButtonStyle.danger if is_nsfw else discord.ButtonStyle.success
            self.add_item(StarterModelButton(
                session_ref=session_ref, channel_id=channel_id,
                model_id=mid, row=i // 5, style=style,
            ))


# =================================================
# MODAL
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
            placeholder="Piper in a red dress on a rainy Tokyo street at night...",
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
            interaction=interaction, session=self.session,
            model_id=self.model_id, user_prompt=user_prompt,
        )


# =================================================
# GENERATION FLOW — jetzt mit Pool + Zufalls-Face + Secondary
# =================================================
async def run_face_generation(interaction, session, model_id, user_prompt):
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

    progress_msg = None
    quota_success = False
    token_quota = None

    try:
        image_limit = get_image_limit_for_member(member)
        ok, state, token_quota = await image_quota.reserve(
            interaction.guild.id, interaction.user.id, image_limit, 1
        )
        if not ok:
            await send_image_quota_message(interaction, member, state)
            return

        # === ZUFÄLLIGE FACE-AUSWAHL AUS DEM POOL ===
        face_bytes, chosen_slot = await face_pool.read_random_face_bytes()
        if face_bytes is None:
            # Fallback auf Legacy-Referenz
            face_bytes = await face_ref_cache.get_bytes(session)
        if not face_bytes:
            await send_ephemeral(
                interaction,
                "❌ No face reference available.\n"
                "Use `/face_config` to upload at least one face image.",
            )
            return

        # === SECONDARY IMAGE: body für NSFW, clothing für SFW ===
        cfg = MODELS[model_id]
        is_nsfw = _model_is_ab18(cfg)
        secondary_bytes = await face_pool.read_secondary_bytes(is_nsfw)

        logger.info(
            "[FACE gen] user=%s model=%s face_slot=%s secondary=%s(%s)",
            interaction.user.id, model_id,
            (chosen_slot + 1) if chosen_slot is not None else "legacy",
            "body" if is_nsfw else "clothing",
            "yes" if secondary_bytes else "no",
        )

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
            venice_edit(session, model_id, full_prompt, face_bytes, secondary_bytes)
        )

        def gen_embed(percent, eta):
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
        guild_icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
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
# /face_config — OWNER-ONLY GUI zum Slot-Management
# =================================================
def _build_config_embed() -> discord.Embed:
    slots = face_pool.get_face_slots()
    body = face_pool.get_path("body")
    clothing = face_pool.get_path("clothing")

    def show(p: Optional[str]) -> str:
        return f"✅ `{Path(p).name}`" if p and Path(p).exists() else "❌ empty"

    e = discord.Embed(
        title="🎭 Face Pool Configuration",
        description=(
            "**Face slots** — up to 3 different face images. "
            "One is picked randomly per generation.\n"
            "**Body** — sent as reference to 🔞 NSFW models (Variant 1 & 2).\n"
            "**Clothing** — sent as reference to the 👗 clothed model."
        ),
        color=discord.Color.purple(),
    )
    for i, s in enumerate(slots):
        e.add_field(name=f"Face {i+1}", value=show(s), inline=True)
    e.add_field(name="🧍 Body (NSFW)", value=show(body), inline=False)
    e.add_field(name="👗 Clothing (SFW)", value=show(clothing), inline=False)
    e.set_footer(text="Use the buttons below to upload or clear slots.")
    return e


class FaceConfigView(discord.ui.View):
    def __init__(self, owner_id: int, bot: commands.Bot):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await send_ephemeral(interaction, "🚫 This panel is owner-only.")
            return False
        return True

    async def _await_upload(
        self, interaction: discord.Interaction, slot_type: str, index: Optional[int], slot_label: str,
    ):
        # Erst dem User sagen was er tun soll
        await interaction.response.send_message(
            f"📎 **Upload for {slot_label}**\n"
            f"Send an image message (as attachment) in this channel within **90 seconds**.\n"
            f"Your upload will be deleted right after processing.",
            ephemeral=True,
        )

        def check(m: discord.Message) -> bool:
            if m.author.id != self.owner_id:
                return False
            if m.channel.id != interaction.channel_id:
                return False
            if not m.attachments:
                return False
            att = m.attachments[0]
            ct = (att.content_type or "").lower()
            fn = (att.filename or "").lower()
            return ct.startswith("image/") or any(fn.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp"))

        try:
            msg: discord.Message = await self.bot.wait_for("message", check=check, timeout=90.0)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏱️ Upload timeout. Try again.", ephemeral=True)
            return

        att = msg.attachments[0]
        try:
            img_bytes = await att.read()
        except Exception as e:
            await interaction.followup.send(f"❌ Read failed: {e}", ephemeral=True)
            return

        if not looks_like_image(img_bytes):
            await interaction.followup.send("❌ That doesn't look like a valid image.", ephemeral=True)
            return

        # Extension aus Dateiname bestimmen
        ext = "png"
        fn_low = (att.filename or "").lower()
        for e_ in ("jpeg", "jpg", "png", "webp"):
            if fn_low.endswith(f".{e_}"):
                ext = e_
                break

        try:
            path = await face_pool.set_slot(slot_type, index, img_bytes, ext)
        except Exception as e:
            await interaction.followup.send(f"❌ Save failed: {e}", ephemeral=True)
            return

        with contextlib.suppress(Exception):
            await msg.delete()

        await interaction.followup.send(
            f"✅ **{slot_label}** saved → `{Path(path).name}` ({len(img_bytes):,} bytes).",
            ephemeral=True,
        )

        # Panel aktualisieren
        with contextlib.suppress(Exception):
            await interaction.edit_original_response(embed=_build_config_embed(), view=self)

    # ---- Face uploads (row 0) ----
    @discord.ui.button(label="📤 Face 1", style=discord.ButtonStyle.primary, row=0)
    async def up_f1(self, interaction, button): await self._await_upload(interaction, "face", 0, "Face 1")
    @discord.ui.button(label="📤 Face 2", style=discord.ButtonStyle.primary, row=0)
    async def up_f2(self, interaction, button): await self._await_upload(interaction, "face", 1, "Face 2")
    @discord.ui.button(label="📤 Face 3", style=discord.ButtonStyle.primary, row=0)
    async def up_f3(self, interaction, button): await self._await_upload(interaction, "face", 2, "Face 3")

    # ---- Body / Clothing (row 1) ----
    @discord.ui.button(label="🧍 Upload Body", style=discord.ButtonStyle.success, row=1)
    async def up_body(self, interaction, button): await self._await_upload(interaction, "body", None, "Body")
    @discord.ui.button(label="👗 Upload Clothing", style=discord.ButtonStyle.success, row=1)
    async def up_cloth(self, interaction, button): await self._await_upload(interaction, "clothing", None, "Clothing")

    # ---- Clear (row 2 + 3) ----
    # ---- Clear helper + buttons ----
    async def _clear(self, interaction: discord.Interaction, slot_type: str, index: Optional[int], label: str):
        await face_pool.clear_slot(slot_type, index)
        await interaction.response.edit_message(embed=_build_config_embed(), view=self)
        await interaction.followup.send(f"🗑️ **{label}** cleared.", ephemeral=True)

    @discord.ui.button(label="🗑️ F1", style=discord.ButtonStyle.danger, row=2)
    async def clr_f1(self, interaction, button): await self._clear(interaction, "face", 0, "Face 1")
    @discord.ui.button(label="🗑️ F2", style=discord.ButtonStyle.danger, row=2)
    async def clr_f2(self, interaction, button): await self._clear(interaction, "face", 1, "Face 2")
    @discord.ui.button(label="🗑️ F3", style=discord.ButtonStyle.danger, row=2)
    async def clr_f3(self, interaction, button): await self._clear(interaction, "face", 2, "Face 3")
    @discord.ui.button(label="🗑️ Body", style=discord.ButtonStyle.danger, row=2)
    async def clr_body(self, interaction, button): await self._clear(interaction, "body", None, "Body")
    @discord.ui.button(label="🗑️ Cloth", style=discord.ButtonStyle.danger, row=2)
    async def clr_cloth(self, interaction, button): await self._clear(interaction, "clothing", None, "Clothing")

    # ---- Refresh (row 3) ----
    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=3)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_build_config_embed(), view=self)


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
        face_pool.load()
        self.bot.add_view(StarterView(self._session_ref, FACE_CHANNEL_ID))
        register_starter_reposter(FACE_CHANNEL_ID, self.ensure_starter_message)

    def cog_unload(self):
        model_caps.save()
        face_pool.save()
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

    # =========================================
    # SLASH COMMAND — nur der Owner
    # =========================================
    @app_commands.command(
        name="face_config",
        description="Manage the face pool (3 face slots + body + clothing). Owner only.",
    )
    async def face_config_slash(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_USER_ID:
            await send_ephemeral(interaction, "🚫 This command is owner-only.")
            return
        view = FaceConfigView(owner_id=OWNER_USER_ID, bot=self.bot)
        await interaction.response.send_message(
            embed=_build_config_embed(),
            view=view,
            ephemeral=True,
        )

    # =========================================
    # ADMIN PREFIX COMMANDS
    # =========================================
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
        slots = face_pool.get_face_slots()
        pool_state = (
            f"faces: {sum(1 for s in slots if s)}/3, "
            f"body: {'✅' if face_pool.get_path('body') else '❌'}, "
            f"clothing: {'✅' if face_pool.get_path('clothing') else '❌'}"
        )
        await ctx.send(
            f"✅ Face cog reloaded.\n"
            f"• Legacy reference: {ref_state}\n"
            f"• Pool: {pool_state}\n"
            f"• Starter messages reposted: {reposted}"
        )

    @commands.command(name="face_caps")
    @commands.has_permissions(administrator=True)
    async def face_caps(self, ctx: commands.Context, model_id: Optional[str] = None):
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
        await self._ensure_session()
        face, _ = await face_pool.read_random_face_bytes()
        if face is None:
            face = await face_ref_cache.get_bytes(self.session)
        if not face:
            await ctx.send("❌ No face reference available (pool empty and no legacy fallback).")
            return

        status = await ctx.send("🧪 Testing models...")
        results: list[str] = []
        for mid in _ordered_model_ids():
            is_nsfw = _model_is_ab18(MODELS[mid])
            secondary = await face_pool.read_secondary_bytes(is_nsfw)
            img, err = await venice_edit(
                self.session, mid, "a simple portrait test",
                face, secondary, retries=0,
            )
            sec_tag = f" +{'body' if is_nsfw else 'clothing'}" if secondary else ""
            if img:
                results.append(f"✅ `{mid}`{sec_tag} → {len(img)} bytes • {model_caps.describe(mid)}")
            else:
                results.append(f"❌ `{mid}`{sec_tag} → {trim(err or 'unknown', 160)}")

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
            face_pool.load()
            with contextlib.suppress(Exception):
                await face_ref_cache.get_bytes(self.session)
            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if channel.id == FACE_CHANNEL_ID:
                        await self.ensure_starter_message(channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceFaceCog(bot))