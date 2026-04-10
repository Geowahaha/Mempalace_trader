from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Protocol, runtime_checkable

from trading_ai.utils.logger import get_logger

log = get_logger(__name__)


Side = Literal["BUY", "SELL"]
Action = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    bid: float
    ask: float
    mid: float
    spread: float
    ts_unix: float
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_prompt_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread": self.spread,
            "ts_unix": self.ts_unix,
            **self.extra,
        }


@dataclass(slots=True)
class TradeResult:
    order_id: str
    symbol: str
    side: Side
    volume: float
    entry_price: float
    executed: bool
    dry_run: bool
    message: str
    position_id: Optional[str] = None
    ts_unix: float = field(default_factory=time.time)
    raw_response: Optional[Dict[str, Any]] = None


@dataclass(slots=True)
class CloseDetail:
    symbol: str
    side: Side
    volume: float
    entry_price: float
    exit_price: float
    pnl: float

    def notional_approx(self) -> float:
        return abs(self.volume * self.entry_price)


@dataclass(slots=True)
class CloseResult:
    symbol: str
    side: Side
    volume: float
    exit_price: float
    closed: bool
    dry_run: bool
    message: str
    position_id: Optional[str] = None
    ts_unix: float = field(default_factory=time.time)
    raw_response: Optional[Dict[str, Any]] = None


@dataclass(slots=True)
class ExecutionOutcome:
    """Result of an execute_trade call: optional close of prior leg + new trade metadata."""

    trade: TradeResult
    close: Optional[CloseDetail] = None


@dataclass(slots=True)
class OpenPosition:
    order_id: str
    symbol: str
    side: Side
    volume: float
    entry_price: float
    position_id: Optional[str]
    opened_ts: float


@runtime_checkable
class Broker(Protocol):
    """Swap implementations: cTrader, paper, MetaTrader, etc."""

    async def get_market_data(self, symbol: str) -> MarketSnapshot: ...

    async def execute_trade(
        self,
        *,
        symbol: str,
        side: Action,
        volume: float,
        decision_reason: str,
        dry_run: bool,
    ) -> TradeResult: ...

    async def close_position(
        self,
        *,
        symbol: str,
        position: OpenPosition,
        reason: str,
        dry_run: bool,
    ) -> CloseResult: ...


class ExecutionService:
    """
    Per-symbol book: at most one open position per symbol; no duplicate same-side adds.
    Opposite signal closes existing first. If close fails in live mode, it will not open
    the opposite leg, which prevents accidental hedging or doubling exposure.
    """

    def __init__(self, broker: Broker) -> None:
        self._broker = broker
        self._positions: Dict[str, OpenPosition] = {}

    @property
    def positions(self) -> Dict[str, OpenPosition]:
        return dict(self._positions)

    def open_position_for(self, symbol: str) -> Optional[OpenPosition]:
        return self._positions.get(symbol)

    def restore_position(self, position: OpenPosition) -> None:
        self._positions[position.symbol] = position

    async def get_market_data(self, symbol: str) -> MarketSnapshot:
        return await self._broker.get_market_data(symbol)

    async def execute_trade(
        self,
        *,
        symbol: str,
        action: Action,
        volume: float,
        decision_reason: str,
        dry_run: bool,
    ) -> ExecutionOutcome:
        if action == "HOLD":
            log.info("Execution: HOLD - no order sent")
            return ExecutionOutcome(
                trade=TradeResult(
                    order_id="hold",
                    symbol=symbol,
                    side="BUY",
                    volume=0.0,
                    entry_price=0.0,
                    executed=False,
                    dry_run=dry_run,
                    message="hold",
                )
            )

        existing = self._positions.get(symbol)
        if existing is not None and existing.side == action:
            log.info(
                "Position control: skip %s %s - already open (one position per symbol)",
                action,
                symbol,
            )
            return ExecutionOutcome(
                trade=TradeResult(
                    order_id="skip_same_side",
                    symbol=symbol,
                    side=action,
                    volume=0.0,
                    entry_price=existing.entry_price,
                    executed=False,
                    dry_run=dry_run,
                    message="skip_same_side_open",
                    position_id=existing.position_id,
                    raw_response={"reason": decision_reason},
                )
            )

        close: Optional[CloseDetail] = None
        if existing is not None:
            prev = existing
            close_result = await self._broker.close_position(
                symbol=prev.symbol,
                position=prev,
                reason=f"flip_to_{action}:{decision_reason}",
                dry_run=dry_run,
            )
            if not close_result.closed and not dry_run:
                log.error(
                    "Position control: failed to close %s %s before %s: %s",
                    prev.side,
                    prev.symbol,
                    action,
                    close_result.message,
                )
                return ExecutionOutcome(
                    trade=TradeResult(
                        order_id="close_failed",
                        symbol=symbol,
                        side=action,
                        volume=0.0,
                        entry_price=prev.entry_price,
                        executed=False,
                        dry_run=False,
                        message=f"close_failed:{close_result.message}",
                        position_id=prev.position_id,
                        raw_response=close_result.raw_response,
                    )
                )
            sign = 1.0 if prev.side == "BUY" else -1.0
            realized = sign * (close_result.exit_price - prev.entry_price) * prev.volume
            close = CloseDetail(
                symbol=prev.symbol,
                side=prev.side,
                volume=prev.volume,
                entry_price=prev.entry_price,
                exit_price=close_result.exit_price,
                pnl=realized,
            )
            log.info(
                "Closed position %s %s @ %.5f -> %.5f PnL~%.5f",
                prev.side,
                prev.symbol,
                prev.entry_price,
                close_result.exit_price,
                realized,
            )
            del self._positions[symbol]

        tr = await self._broker.execute_trade(
            symbol=symbol,
            side=action,
            volume=volume,
            decision_reason=decision_reason,
            dry_run=dry_run,
        )
        if tr.executed or dry_run:
            act: Side = action  # type: ignore[assignment]
            self._positions[symbol] = OpenPosition(
                order_id=tr.order_id,
                symbol=tr.symbol,
                side=act,
                volume=tr.volume,
                entry_price=tr.entry_price,
                position_id=tr.position_id,
                opened_ts=tr.ts_unix,
            )
        return ExecutionOutcome(trade=tr, close=close)

    def force_flat(self) -> None:
        self._positions.clear()


class PaperBroker:
    """Deterministic paper broker for integration tests and safe dry-run."""

    def __init__(self, symbol: str, seed_mid: float = 2650.0) -> None:
        self._symbol = symbol
        self._mid = seed_mid

    def _bump(self) -> None:
        self._mid *= 1.0 + (time.time() % 17 - 8.5) * 1e-5

    async def get_market_data(self, symbol: str) -> MarketSnapshot:
        self._bump()
        spread = max(self._mid * 1e-5, 0.05)
        bid = self._mid - spread / 2
        ask = self._mid + spread / 2
        return MarketSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            mid=self._mid,
            spread=spread,
            ts_unix=time.time(),
            extra={"venue": "paper"},
        )

    async def execute_trade(
        self,
        *,
        symbol: str,
        side: Action,
        volume: float,
        decision_reason: str,
        dry_run: bool,
    ) -> TradeResult:
        m = await self.get_market_data(symbol)
        entry = m.ask if side == "BUY" else m.bid
        oid = str(uuid.uuid4())
        if dry_run:
            return TradeResult(
                order_id=oid,
                symbol=symbol,
                side=side,
                volume=volume,
                entry_price=entry,
                executed=False,
                dry_run=True,
                message=f"dry_run: would {side} {volume} @ {entry:.5f}",
                position_id=oid,
                raw_response={"reason": decision_reason},
            )
        return TradeResult(
            order_id=oid,
            symbol=symbol,
            side=side,
            volume=volume,
            entry_price=entry,
            executed=True,
            dry_run=False,
            message=f"paper_fill {side} {volume} @ {entry:.5f}",
            position_id=oid,
            raw_response={"reason": decision_reason},
        )

    async def close_position(
        self,
        *,
        symbol: str,
        position: OpenPosition,
        reason: str,
        dry_run: bool,
    ) -> CloseResult:
        m = await self.get_market_data(symbol)
        exit_price = m.bid if position.side == "BUY" else m.ask
        return CloseResult(
            symbol=symbol,
            side=position.side,
            volume=position.volume,
            exit_price=exit_price,
            closed=True,
            dry_run=dry_run,
            message="paper_close",
            position_id=position.position_id,
            raw_response={"reason": reason},
        )
