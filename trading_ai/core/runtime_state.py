from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.core.execution import OpenPosition
from trading_ai.core.strategy import RiskManager
from trading_ai.utils.logger import get_logger

log = get_logger(__name__)


def _position_from_raw(raw: Dict[str, Any], file_path: Path) -> Optional[OpenPosition]:
    try:
        return OpenPosition(
            order_id=str(raw.get("order_id") or ""),
            symbol=str(raw.get("symbol") or ""),
            side=str(raw.get("side") or "BUY"),  # type: ignore[arg-type]
            volume=float(raw.get("volume") or 0.0),
            entry_price=float(raw.get("entry_price") or 0.0),
            position_id=str(raw.get("position_id")) if raw.get("position_id") else None,
            opened_ts=float(raw.get("opened_ts") or time.time()),
        )
    except Exception as exc:
        log.warning("runtime state open position invalid %s: %s", file_path, exc)
        return None


def load_runtime_positions_state(path: Path) -> Tuple[List[OpenPosition], List[Dict[str, Any]], Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return [], [], {}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("runtime state load failed %s: %s", file_path, exc)
        return [], [], {}

    open_positions: List[OpenPosition] = []
    for raw in list(payload.get("open_positions") or []):
        if isinstance(raw, dict):
            pos = _position_from_raw(raw, file_path)
            if pos is not None:
                open_positions.append(pos)

    if not open_positions:
        pos_raw = dict(payload.get("open_position") or {})
        if pos_raw:
            pos = _position_from_raw(pos_raw, file_path)
            if pos is not None:
                open_positions.append(pos)

    open_contexts = [dict(item) for item in list(payload.get("open_contexts") or []) if isinstance(item, dict)]
    if not open_contexts:
        open_context = payload.get("open_context")
        if isinstance(open_context, dict):
            open_contexts.append(open_context)
    risk_state = dict(payload.get("risk") or {})
    return open_positions, open_contexts, risk_state


def load_runtime_state(path: Path) -> Tuple[Optional[OpenPosition], Optional[Dict[str, Any]], Dict[str, Any]]:
    open_positions, open_contexts, risk_state = load_runtime_positions_state(path)
    open_position = open_positions[-1] if open_positions else None
    open_context = open_contexts[-1] if open_contexts else None
    return open_position, open_context, risk_state


def save_runtime_state(
    path: Path,
    *,
    open_position: Optional[OpenPosition],
    open_context: Optional[Dict[str, Any]],
    risk: RiskManager,
    open_positions: Optional[List[OpenPosition]] = None,
    open_contexts: Optional[List[Dict[str, Any]]] = None,
) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    positions = list(open_positions or ([] if open_position is None else [open_position]))
    contexts = list(open_contexts or ([] if open_context is None else [open_context]))
    legacy_position = positions[-1] if positions else None
    legacy_context = contexts[-1] if contexts else None
    payload = {
        "updated_ts": time.time(),
        "open_position": asdict(legacy_position) if legacy_position is not None else None,
        "open_positions": [asdict(position) for position in positions],
        "open_context": legacy_context if legacy_context is not None else None,
        "open_contexts": contexts,
        "risk": risk.snapshot(),
    }
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, file_path)
