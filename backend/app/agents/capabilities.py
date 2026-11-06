"""Model capabilities probing, persistence, and auto-mode resolution.

Stores probed model capabilities in data/model_capabilities.json keyed by
sha256(model + base_url), allowing automatic mode resolution (native vs json_prompt,
native tools vs prefetch) without manual configuration.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.settings import LLMSettings

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_FILE = REPO_ROOT / "data" / "model_capabilities.json"

# Process-level dynamic fallbacks (e.g. after a runtime 400 schema error)
_PROCESS_FALLBACKS: dict[str, dict[str, Any]] = {}


def compute_capability_key(model: str, base_url: str | None) -> str:
    """Computes sha256 hash key over model and base_url."""
    norm_model = (model or "").strip()
    norm_url = (base_url or "").strip().rstrip("/")
    payload = f"{norm_model}|{norm_url}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_capabilities(model: str, base_url: str | None) -> dict[str, Any] | None:
    """Loads saved capabilities for a model and base_url if present."""
    if not CAPABILITIES_FILE.is_file():
        return None
    try:
        content = json.loads(CAPABILITIES_FILE.read_text(encoding="utf-8"))
        key = compute_capability_key(model, base_url)
        return content.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read %s: %s", CAPABILITIES_FILE, exc)
        return None


def save_capabilities(model: str, base_url: str | None, caps: dict[str, Any]) -> None:
    """Persists model capabilities to data/model_capabilities.json."""
    CAPABILITIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if CAPABILITIES_FILE.is_file():
        try:
            existing = json.loads(CAPABILITIES_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = {}

    key = compute_capability_key(model, base_url)
    caps_record = dict(caps)
    caps_record["model"] = model
    caps_record["base_url"] = base_url
    caps_record["probed_at"] = datetime.now(timezone.utc).isoformat()
    existing[key] = caps_record

    try:
        CAPABILITIES_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        logger.info("Saved model capabilities for key %s to %s", key, CAPABILITIES_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to save capabilities file: %s", exc)


def record_process_fallback(model: str, base_url: str | None, key: str, value: Any = True) -> None:
    """Records a runtime fallback for this process (e.g. after schema 400)."""
    cap_key = compute_capability_key(model, base_url)
    if cap_key not in _PROCESS_FALLBACKS:
        _PROCESS_FALLBACKS[cap_key] = {}
    _PROCESS_FALLBACKS[cap_key][key] = value
    logger.info("Process fallback recorded: [%s] %s=%s", cap_key[:8], key, value)


def get_process_fallback(model: str, base_url: str | None, key: str) -> Any | None:
    """Retrieves a process-level runtime fallback if set."""
    cap_key = compute_capability_key(model, base_url)
    return _PROCESS_FALLBACKS.get(cap_key, {}).get(key)


def resolve_effective_modes(settings: LLMSettings) -> tuple[str, str, bool]:
    """Resolves effective (structured_output_mode, tools_mode, temperature_supported).

    Returns:
        (structured_output_mode, tools_mode, temperature_supported)
        where structured_output_mode in ("native", "json_prompt"),
              tools_mode in ("native", "prefetch"),
              temperature_supported is bool.
    """
    model = settings.llm_model
    base_url = settings.openai_base_url
    caps = load_capabilities(model, base_url)

    # 1. Structured output mode
    struct_mode = settings.structured_output_mode
    proc_struct = get_process_fallback(
        model, base_url, "structured_output_mode"
    ) or get_process_fallback(model, base_url, "structured")
    if proc_struct:
        effective_struct = "json_prompt" if proc_struct is True else str(proc_struct)
    elif struct_mode == "auto":
        if caps and "recommended_structured_output_mode" in caps:
            effective_struct = caps["recommended_structured_output_mode"]
        elif caps and (
            caps.get("structured_outputs") is False or caps.get("native_structured_output") is False
        ):
            effective_struct = "json_prompt"
        else:
            effective_struct = "native"
    else:
        effective_struct = struct_mode

    # 2. Tools mode
    tools_mode = settings.tools_mode
    proc_tools = get_process_fallback(model, base_url, "tools_mode") or get_process_fallback(
        model, base_url, "tools"
    )
    if proc_tools:
        effective_tools = "prefetch" if proc_tools is True else str(proc_tools)
    elif tools_mode == "auto":
        if caps and "recommended_tools_mode" in caps:
            effective_tools = caps["recommended_tools_mode"]
        elif caps and caps.get("tool_calling") is False:
            effective_tools = "prefetch"
        else:
            effective_tools = "native"
    else:
        effective_tools = tools_mode

    # 3. Temperature supported
    proc_temp = get_process_fallback(model, base_url, "temperature_supported")
    if proc_temp is not None:
        temp_supported = bool(proc_temp)
    elif caps and "temperature_supported" in caps:
        temp_supported = bool(caps["temperature_supported"])
    else:
        temp_supported = True

    return effective_struct, effective_tools, temp_supported
