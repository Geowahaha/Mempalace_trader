from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.core.execution import OpenPosition
from trading_ai.core.strategy import RiskManager
from trading_ai.utils.logger import get_logger

log = get_logger(__name__)


def load_runtime_state(path: Path) -> Tuple[Optional[OpenPosition], Optional[Dict[str, Any]], Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return None, None, {}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("runtime state load failed %s: %s", file_path, exc)
        return None, None, {}

    pos_raw = dict(payload.get("open_position") or {})
    open_position: Optional[OpenPosition] = None
    if pos_raw:
        try:
            open_position = OpenPosition(
                order_id=str(pos_raw.get("order_id") or ""),
                symbol=str(pos_raw.get("symbol") or ""),
                side=str(pos_raw.get("side") or "BUY"),  # type: ignore[arg-type]
                volume=float(pos_raw.get("volume") or 0.0),
                entry_price=float(pos_raw.get("entry_price") or 0.0),
                position_id=str(pos_raw.get("position_id")) if pos_raw.get("position_id") else None,
                opened_ts=float(pos_raw.get("opened_ts") or time.time()),
            )
        except Exception as exc:
            log.warning("runtime state open_position invalid %s: %s", file_path, exc)
            open_position = None

    open_context = payload.get("open_context")
    risk_state = dict(payload.get("risk") or {})
    return open_position, open_context if isinstance(open_context, dict) else None, risk_state


def save_runtime_state(
    path: Path,
    *,
    open_position: Optional[OpenPosition],
    open_context: Optional[Dict[str, Any]],
    risk: RiskManager,
) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_ts": time.time(),
        "open_position": asdict(open_position) if open_position is not None else None,
        "open_context": open_context if open_context is not None else None,
        "risk": risk.snapshot(),
    }
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, file_path)
