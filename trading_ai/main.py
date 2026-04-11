from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.config import LLMProviderName, Settings, load_settings, memory_persist_path
from trading_ai.core.agent import (
    Decision,
    TradingAgent,
    apply_confidence_floor,
    format_matched_trades_log,
)
from trading_ai.core.execution import (
    Broker,
    ExecutionService,
    MarketSnapshot,
    OpenPosition,
    PaperBroker,
)
from trading_ai.core.market_features import extract_features, infer_setup_tag
from trading_ai.core.memory import MemoryEngine, MemoryNote, MemoryRecord
from trading_ai.core.patterns import (
    PatternBook,
    apply_pattern_confidence_boost,
    build_pattern_analysis_for_prompt,
    parse_memory_document_to_row,
    passes_pattern_execution_gate,
    score_pattern,
)
from trading_ai.core.performance import PerformanceTracker
from trading_ai.core.performance_monitor import PerformanceMonitor
from trading_ai.core.runtime_state import load_runtime_positions_state, save_runtime_state
from trading_ai.core.portfolio_intelligence import (
    build_portfolio_votes,
    classify_regime,
    fuse_portfolio_votes,
    parse_recall_actions_for_diag,
)
from trading_ai.core.correlation_engine import CorrelationEngine, active_strategy_keys_from_registry
from trading_ai.core.strategy import RiskManager, evaluate_outcome
from trading_ai.core.strategy_evolution import StrategyRegistry, build_strategy_key
from trading_ai.integrations.ctrader import CTraderBroker, CTraderConfig
from trading_ai.integrations.ctrader_dexter_worker import CTraderDexterWorkerBroker
from trading_ai.integrations.failover import FailoverProvider
from trading_ai.integrations.mimo import MiMoProvider
from trading_ai.integrations.openai_adapter import OpenAIProvider
from trading_ai.utils.logger import get_logger

log = get_logger(__name__)


def build_llm(settings: Settings):
    if settings.llm_provider is LLMProviderName.OPENAI:
        key = settings.openai_api_key or ""
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        models = [str(settings.openai_model).strip()]
        for raw in str(settings.openai_fallback_models or "").split(","):
            model = str(raw).strip()
            if model and model not in models:
                models.append(model)
        providers = [
            (
                f"openai:{model}",
                OpenAIProvider(
                    api_key=key,
                    model=model,
                    base_url=settings.openai_base_url,
                    timeout_sec=settings.llm_timeout_sec,
                    max_retries=settings.llm_max_retries,
                    max_tokens=settings.llm_max_tokens,
                ),
            )
            for model in models
        ]
        log.info("LLM failover chain: %s", [label for label, _ in providers])
        return FailoverProvider(providers) if len(providers) > 1 else providers[0][1]
    if settings.llm_provider is LLMProviderName.MIMO:
        key = settings.mimo_api_key or ""
        if not key:
            raise RuntimeError("MIMO_API_KEY is required when LLM_PROVIDER=mimo")
        return MiMoProvider(
            api_key=key,
            model=settings.mimo_model,
            base_url=settings.mimo_base_url,
            timeout_sec=settings.llm_timeout_sec,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
        )
    if settings.llm_provider is LLMProviderName.LOCAL:
        models = [str(settings.local_model).strip()]
        for raw in str(settings.local_fallback_models or "").split(","):
            model = str(raw).strip()
            if model and model not in models:
                models.append(model)
        providers = [
            (
                f"local:{model}",
                OpenAIProvider(
                    api_key=settings.local_api_key,
                    model=model,
                    base_url=settings.local_base_url,
                    timeout_sec=settings.llm_timeout_sec,
                    max_retries=settings.llm_max_retries,
                    max_tokens=settings.llm_max_tokens,
                ),
            )
            for model in models
        ]
        log.info("Local LLM failover chain: %s", [label for label, _ in providers])
        return FailoverProvider(providers) if len(providers) > 1 else providers[0][1]
    raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")


def _safe_int_account(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(float(str(raw).strip()))
    except ValueError:
        return None


def _enforce_live_safety(settings: Settings) -> None:
    if settings.dry_run:
        return
    if not settings.live_execution_enabled:
        raise RuntimeError(
            "Refusing live execution: set LIVE_EXECUTION_ENABLED=true explicitly after validating "
            "the Mempalac runtime on this VM."
        )
    if settings.ctrader_dexter_worker and settings.ctrader_quote_source == "paper":
        raise RuntimeError(
            "Refusing live execution with CTRADER_QUOTE_SOURCE=paper. "
            "Use CTRADER_QUOTE_SOURCE=auto or dexter_capture so live learning uses real broker quotes."
        )


def build_broker(settings: Settings) -> Broker:
    quote = PaperBroker(settings.symbol)
    if settings.ctrader_dexter_worker and settings.ctrader_account_id:
        try:
            return CTraderDexterWorkerBroker(settings, quote_broker=quote)
        except Exception as exc:
            if settings.live_execution_enabled and not settings.dry_run:
                raise RuntimeError(f"CTraderDexterWorkerBroker unavailable: {exc}") from exc
            log.exception("CTraderDexterWorkerBroker unavailable: %s - PaperBroker", exc)
            return quote
    if settings.ctrader_enabled and settings.ctrader_client_id and settings.ctrader_client_secret:
        if settings.live_execution_enabled and not settings.dry_run:
            raise RuntimeError(
                "Native integrations/ctrader.py is still a stub transport. "
                "Use CTRADER_DEXTER_WORKER=1 for real execution or keep DRY_RUN=true."
            )
        cfg = CTraderConfig(
            client_id=settings.ctrader_client_id,
            client_secret=settings.ctrader_client_secret,
            access_token=settings.ctrader_access_token,
            refresh_token=settings.ctrader_refresh_token,
            redirect_uri=settings.ctrader_redirect_uri,
            demo=settings.ctrader_demo,
            account_id=_safe_int_account(settings.ctrader_account_id),
            account_login=settings.ctrader_account_login,
        )
        return CTraderBroker(cfg)
    if settings.live_execution_enabled and not settings.dry_run:
        raise RuntimeError(
            "Live execution requested but no live-capable broker is configured. "
            "Enable CTRADER_DEXTER_WORKER with CTRADER_ACCOUNT_ID and Dexter worker access."
        )
    log.info("cTrader disabled or incomplete credentials - using PaperBroker")
    return quote


def build_memory(settings: Settings) -> MemoryEngine:
    return MemoryEngine(
        persist_path=memory_persist_path(settings),
        collection_name=settings.memory_collection,
        score_weight=settings.memory_score_weight,
    )


async def smoke_ctrader_worker(settings: Settings) -> None:
    """Single BUY via Dexter `ctrader_execute_once` - verifies broker wiring without the LLM loop."""
    broker = build_broker(settings)
    if not isinstance(broker, CTraderDexterWorkerBroker):
        log.error(
            "smoke-worker needs CTraderDexterWorkerBroker. "
            "Set CTRADER_DEXTER_WORKER=1 and CTRADER_ACCOUNT_ID; fix CTRADER_WORKER_SCRIPT if needed. "
            "Got broker type: %s",
            type(broker).__name__,
        )
        raise SystemExit(2)
    execution = ExecutionService(broker)
    log.info(
        "smoke-worker: BUY %s volume=%s dry_run=%s",
        settings.symbol,
        settings.default_volume,
        settings.dry_run,
    )
    outcome = await execution.execute_trade(
        symbol=settings.symbol,
        action="BUY",
        volume=float(settings.default_volume),
        decision_reason="mempalac_smoke_worker",
        dry_run=settings.dry_run,
    )
    tr = outcome.trade
    log.info(
        "smoke-worker done executed=%s dry_run=%s order_id=%s message=%s",
        tr.executed,
        tr.dry_run,
        tr.order_id,
        tr.message,
    )
    if tr.raw_response is not None:
        log.info("smoke-worker raw_response=%s", json.dumps(tr.raw_response, ensure_ascii=True)[:4000])
    if not settings.dry_run and not tr.executed:
        raise SystemExit(1)


def _journal_structured(
    market: MarketSnapshot,
    features: Dict[str, Any],
    decision: Decision,
    settings: Settings,
    setup_tag: str,
    *,
    strategy_key: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload = {
        "market": market.as_prompt_dict(),
        "features": features,
        "decision": {
            "action": decision.action,
            "confidence": decision.confidence,
            "reason": decision.reason,
        },
        "setup_tag": setup_tag,
        "strategy_key": strategy_key,
        "runtime": {"dry_run": settings.dry_run, "provider": str(settings.llm_provider)},
        "extra": extra or {},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _hydrate_pattern_book(memory: MemoryEngine, book: PatternBook) -> None:
    rows: List[Dict[str, Any]] = []
    for item in memory.list_all_structured_experiences():
        r = parse_memory_document_to_row(item["document"], item["metadata"])
        if r:
            rows.append(r)
    book.hydrate_from_rows(rows)


def _hard_market_filters(
    features: Dict[str, Any],
    risk: RiskManager,
    settings: Settings,
    action: str = "",
) -> Optional[str]:
    sample_len = int(features.get("sample_closes_len") or 0)
    trend = str(features.get("trend_direction") or "")
    if action == "BUY" and trend == "DOWN":
        return "action_trend_mismatch_BUY_vs_DOWN"
    if action == "SELL" and trend == "UP":
        return "action_trend_mismatch_SELL_vs_UP"
    if settings.hard_filter_min_closes > 0 and sample_len < settings.hard_filter_min_closes:
        return None
    if str(features.get("volatility")) == "LOW":
        return "volatility_LOW"
    if str(features.get("trend_direction")) == "RANGE":
        return "trend_RANGE"
    if bool((features.get("structure") or {}).get("consolidation")):
        return "structure_consolidation"
    if risk.consecutive_losses >= settings.entry_loss_streak_block:
        return f"loss_streak_{risk.consecutive_losses}>={settings.entry_loss_streak_block}"
    return None


async def _estimate_account_equity(broker: Broker, settings: Settings) -> float:
    getter = getattr(broker, "get_account_equity", None)
    if callable(getter):
        try:
            equity = await asyncio.to_thread(getter)
            if equity is not None and float(equity) > 0:
                return float(equity)
        except Exception as exc:
            log.warning("Risk cap equity probe failed: %s", exc)
    return float(settings.risk_equity_fallback_usd)


async def _cap_trade_volume_for_exposure(
    *,
    broker: Broker,
    execution: ExecutionService,
    settings: Settings,
    symbol: str,
    action: str,
    requested_volume: float,
    confidence: float,
) -> tuple[float, str]:
    if action not in ("BUY", "SELL"):
        return 0.0, "not_entry"

    side_positions = [p for p in execution.positions_for(symbol) if p.side == action]
    if side_positions and not settings.pyramiding_enabled:
        return 0.0, "pyramiding_disabled"
    if side_positions and confidence < settings.pyramid_add_min_confidence:
        return 0.0, f"pyramid_confidence_low:{confidence:.3f}<{settings.pyramid_add_min_confidence:.3f}"
    if len(side_positions) >= settings.pyramid_max_positions_per_side:
        return 0.0, f"pyramid_position_cap:{len(side_positions)}>={settings.pyramid_max_positions_per_side}"

    equity = await _estimate_account_equity(broker, settings)
    equity_cap = max(0.0, equity / 1000.0 * float(settings.risk_max_lot_per_1000_equity))
    hard_cap = float(settings.risk_max_total_lot_per_symbol)
    cap = min(hard_cap, equity_cap) if hard_cap > 0 else equity_cap
    current = execution.total_volume(symbol, action) if side_positions else 0.0
    remaining = max(0.0, cap - current)
    min_lot = max(0.0, float(settings.risk_min_order_lot))

    if remaining + 1e-12 < min_lot:
        return 0.0, f"exposure_cap_full:current={current:.4f}:cap={cap:.4f}:equity={equity:.2f}"

    allowed = min(float(requested_volume), remaining)
    if allowed + 1e-12 < min_lot:
        return 0.0, f"order_below_min_lot:allowed={allowed:.4f}:min={min_lot:.4f}"
    if allowed < float(requested_volume):
        return allowed, f"volume_capped:{requested_volume:.4f}->{allowed:.4f}:cap={cap:.4f}:equity={equity:.2f}"
    return float(requested_volume), f"volume_ok:current={current:.4f}:cap={cap:.4f}:equity={equity:.2f}"


async def _reconcile_open_positions_from_broker(
    broker: Broker,
    settings: Settings,
) -> list[OpenPosition]:
    runner = getattr(broker, "_run_worker", None)
    if not callable(runner):
        return []
    account_id = _safe_int_account(settings.ctrader_account_id)
    if not account_id:
        return []
    payload = {
        "account_id": int(account_id),
        "symbol": settings.symbol,
        "lookback_hours": 72,
        "max_rows": 100,
    }
    try:
        data = await asyncio.to_thread(runner, "reconcile", payload)
    except Exception as exc:
        log.warning("Startup broker reconcile failed: %s", exc)
        return []
    if not bool(data.get("ok")):
        log.warning(
            "Startup broker reconcile returned status=%s message=%s",
            data.get("status"),
            str(data.get("message") or "")[:220],
        )
        return []
    scale = max(1, int(settings.ctrader_worker_volume_scale))

    # Dexter worker order volume uses DEFAULT_VOLUME * scale, but cTrader reconcile
    # returns open-position volume in a centi-unit of that worker payload.
    # Example seen in production demo:
    #   requested DEFAULT_VOLUME=0.01 -> worker volume=1 -> reconcile raw volume=100
    # Convert back into Mempalace lot-sized units so exposure caps and pyramiding
    # continue to operate on the same unit used at order entry time.
    broker_to_lot_divisor = float(scale * 100)
    positions: list[OpenPosition] = []
    for row in list(data.get("positions") or []):
        try:
            symbol = str(row.get("symbol") or "").upper().strip()
            if symbol != settings.symbol.upper():
                continue
            direction = str(row.get("direction") or "").strip().lower()
            side = "BUY" if direction == "long" else "SELL" if direction == "short" else ""
            if not side:
                continue
            raw_volume = float(row.get("volume") or 0.0)
            if raw_volume <= 0:
                continue
            opened_ms = float(row.get("open_timestamp_ms") or row.get("updated_timestamp_ms") or 0.0)
            positions.append(
                OpenPosition(
                    order_id=str(row.get("position_id") or row.get("order_id") or f"reconcile_{len(positions)+1}"),
                    symbol=symbol,
                    side=side,  # type: ignore[arg-type]
                    volume=raw_volume / broker_to_lot_divisor,
                    entry_price=float(row.get("entry_price") or 0.0),
                    position_id=str(row.get("position_id") or "") or None,
                    opened_ts=(opened_ms / 1000.0) if opened_ms > 0 else 0.0,
                )
            )
        except Exception as exc:
            log.warning("Skipping startup reconciled position row=%s err=%s", row, exc)
    return positions


async def learning_loop(settings: Settings) -> None:
    memory = build_memory(settings)
    broker = build_broker(settings)
    execution = ExecutionService(broker)
    llm = build_llm(settings)
    agent = TradingAgent(llm, settings)
    risk = RiskManager(
        max_trades_per_session=settings.max_trades_per_session,
        max_consecutive_losses=settings.max_consecutive_losses,
        neutral_rel_threshold=settings.neutral_pnl_threshold,
    )
    perf = PerformanceTracker()
    price_history: List[float] = []
    pattern_book = PatternBook()
    _hydrate_pattern_book(memory, pattern_book)

    registry = StrategyRegistry(Path(settings.strategy_registry_path))
    if len(registry.snapshot()) == 0 and memory.count() > 0:
        hist: List[Dict[str, Any]] = []
        for item in memory.list_all_structured_experiences():
            r = parse_memory_document_to_row(item["document"], item["metadata"])
            if r:
                hist.append(r)
        if hist:
            registry.hydrate_from_closed_trades(hist)

    correlation: Optional[CorrelationEngine] = None
    if settings.correlation_engine_enabled:
        correlation = CorrelationEngine(
            Path(settings.strategy_correlation_path),
            max_len=settings.correlation_max_history,
            min_samples_matrix=settings.correlation_min_samples,
            penalty_mid_threshold=settings.correlation_penalty_mid_threshold,
            penalty_high_threshold=settings.correlation_penalty_high_threshold,
            penalty_mid=settings.correlation_penalty_mid,
            penalty_high=settings.correlation_penalty_high,
            max_penalty=settings.correlation_max_penalty,
            diversity_threshold=settings.correlation_diversity_threshold,
            diversity_bonus=settings.correlation_diversity_bonus,
        )

    perf_mon = PerformanceMonitor(
        log_interval_cycles=settings.performance_log_interval,
        alert_max_drawdown=settings.performance_alert_max_drawdown,
        alert_selectivity_min=settings.performance_alert_selectivity_min,
        alert_min_llm_intents=settings.performance_alert_min_llm_intents,
    )

    open_contexts: List[Dict[str, Any]] = []
    restored_positions, restored_contexts, restored_risk = load_runtime_positions_state(settings.runtime_state_path)
    if restored_positions:
        execution.restore_positions(restored_positions)
        open_contexts = list(restored_contexts)
        while open_contexts and len(open_contexts) > len(restored_positions):
            open_contexts.pop(0)
        while open_contexts and len(open_contexts) < len(restored_positions):
            open_contexts.insert(0, dict(open_contexts[0]))
        latest = restored_positions[-1]
        log.warning(
            "Restored %s open position(s) from runtime state; latest=%s %s volume=%s position_id=%s",
            len(restored_positions),
            latest.side,
            latest.symbol,
            latest.volume,
            latest.position_id,
        )
    else:
        broker_positions = await _reconcile_open_positions_from_broker(broker, settings)
        if broker_positions:
            execution.restore_positions(broker_positions)
            latest = broker_positions[-1]
            log.warning(
                "Reconciled %s open position(s) from broker at startup; latest=%s %s volume=%s position_id=%s",
                len(broker_positions),
                latest.side,
                latest.symbol,
                latest.volume,
                latest.position_id,
            )
    if restored_risk:
        risk.restore(restored_risk)
        log.info("Restored runtime risk state: %s", risk.snapshot())

    def persist_runtime_state() -> None:
        save_runtime_state(
            settings.runtime_state_path,
            open_position=execution.open_position_for(settings.symbol),
            open_context=open_contexts[-1] if open_contexts else None,
            open_positions=execution.positions_for(settings.symbol),
            open_contexts=open_contexts,
            risk=risk,
        )

    persist_runtime_state()
    log.info(
        "Learning loop started instance=%s symbol=%s dry_run=%s live_execution=%s memory_count=%s patterns=%s strategies=%s",
        settings.instance_name,
        settings.symbol,
        settings.dry_run,
        settings.live_execution_enabled,
        memory.count(),
        len(pattern_book.patterns_dict()),
        len(registry.snapshot()),
    )

    while True:
        try:
            if risk.halted:
                log.error("Stopped: %s", risk.halt_reason)
                await asyncio.sleep(settings.loop_interval_sec)
                continue

            if (
                settings.strategy_evolution_v2_enabled
                and settings.strategy_aging_enabled
            ):
                registry.apply_aging(settings.strategy_aging_factor)

            if correlation is not None:
                correlation.start_cycle()

            market = await execution.get_market_data(settings.symbol)
            price_history.append(float(market.mid))
            if len(price_history) > settings.price_history_max:
                price_history = price_history[-settings.price_history_max :]

            md: Dict[str, Any] = {**market.as_prompt_dict(), "price_history": list(price_history)}
            features = extract_features(md)

            similar = memory.recall_similar_trades(
                features,
                symbol=settings.symbol,
                top_k=settings.similar_trades_top_k,
            )

            risk_state = {
                "can_trade": risk.can_trade(),
                "halted": risk.halted,
                "consecutive_losses": risk.consecutive_losses,
                "max_consecutive_losses_halt": settings.max_consecutive_losses,
                "entry_loss_streak_block": settings.entry_loss_streak_block,
                "trades_executed_session": risk.trades_executed,
                "min_confidence_required": settings.min_trade_confidence,
            }

            patterns_live = pattern_book.patterns_dict()
            pattern_analysis = build_pattern_analysis_for_prompt(features, patterns_live)

            pre_llm_veto = _hard_market_filters(features, risk, settings)
            if pre_llm_veto:
                decision = Decision(
                    action="HOLD",
                    confidence=0.0,
                    reason=f"pre_llm_hard_filter:{pre_llm_veto}",
                    raw={"pre_llm_hard_filter": pre_llm_veto},
                )
            else:
                wake_up_context = memory.build_wake_up_context(
                    symbol=settings.symbol,
                    session=str(features.get("session") or "") or None,
                    top_k=settings.memory_wakeup_top_k,
                    note_top_k=settings.memory_note_top_k,
                )
                decision = await agent.decide(
                    market,
                    features,
                    similar_trades=similar,
                    risk_state=risk_state,
                    pattern_analysis=pattern_analysis,
                    wake_up_context=wake_up_context,
                )
            if settings.performance_monitor_enabled:
                perf_mon.update_on_signal(decision)

            veto = _hard_market_filters(features, risk, settings, decision.action)
            if veto and not decision.reason.startswith("pre_llm_hard_filter:"):
                decision = Decision(
                    action="HOLD",
                    confidence=0.0,
                    reason=f"{decision.reason}|hard_filter:{veto}",
                    raw=dict(decision.raw),
                )
                decision = apply_confidence_floor(decision, settings.min_trade_confidence)

            if (
                settings.portfolio_intelligence_enabled
                and decision.action in ("BUY", "SELL")
            ):
                regime = classify_regime(features)
                llm_a = decision.action
                llm_c = float(decision.confidence)
                votes = build_portfolio_votes(
                    llm_action=llm_a,
                    llm_confidence=llm_c,
                    features=features,
                    similar_hits=similar,
                    cfg_weights={
                        "llm": settings.portfolio_weight_llm,
                        "memory": settings.portfolio_weight_memory,
                        "structure": settings.portfolio_weight_structure,
                    },
                )
                pen_f = 0.0
                bon_f = 0.0
                corr_pen_meta: Dict[str, Any] = {}
                corr_div_meta: Dict[str, Any] = {}
                matrix_snapshot: Dict[str, float] = {}
                if settings.correlation_engine_enabled and correlation is not None:
                    cand_sk = build_strategy_key(
                        features,
                        infer_setup_tag(features, llm_a),
                    )
                    matrix_snapshot = correlation.get_correlation_matrix_cached()
                    act_keys = active_strategy_keys_from_registry(registry.snapshot(), min_trades=1)
                    pen_f, corr_pen_meta = correlation.get_correlation_penalty(
                        cand_sk,
                        act_keys,
                        matrix=matrix_snapshot,
                    )
                    bon_f, corr_div_meta = correlation.get_diversity_bonus(
                        cand_sk,
                        act_keys,
                        matrix=matrix_snapshot,
                    )
                    log.info(
                        "Correlation L5: key=%s penalty=%.3f bonus=%.3f top_pairs=%s",
                        cand_sk,
                        pen_f,
                        bon_f,
                        correlation.top_correlation_pairs(matrix_snapshot, limit=8),
                    )
                    if settings.performance_monitor_enabled:
                        perf_mon.note_correlation_penalty(pen_f)

                fused = fuse_portfolio_votes(
                    votes,
                    regime=regime,
                    tie_margin=settings.portfolio_tie_margin,
                    llm_anchor_confidence=settings.portfolio_llm_anchor_confidence,
                    llm_original_action=llm_a,
                    llm_original_confidence=llm_c,
                    correlation_penalty=pen_f,
                    diversity_bonus=bon_f,
                )
                fused.diag["recall_digest"] = parse_recall_actions_for_diag(similar)
                if settings.correlation_engine_enabled:
                    fused.diag["correlation_penalty_detail"] = corr_pen_meta
                    fused.diag["correlation_diversity_detail"] = corr_div_meta

                conf_cap = min(0.95, fused.confidence) if fused.action != "HOLD" else fused.confidence
                if fused.action != decision.action or abs(conf_cap - float(decision.confidence)) > 1e-5:
                    reason = f"{decision.reason}|portfolio:{fused.reason_detail}|regime={fused.regime}"
                    if pen_f > 0 or bon_f > 0:
                        reason += f"|corr_p={pen_f:.2f}_b={bon_f:.2f}"
                    decision = Decision(
                        action=fused.action,
                        confidence=conf_cap,
                        reason=reason,
                        raw={**dict(decision.raw), "portfolio_fusion": fused.diag},
                    )
                    decision = apply_confidence_floor(decision, settings.min_trade_confidence)
                log.info(
                    "Portfolio L4+L5: regime=%s result=%s conf=%.3f masses buy=%.4f sell=%.4f corr=%s",
                    regime,
                    decision.action,
                    decision.confidence,
                    fused.buy_mass,
                    fused.sell_mass,
                    fused.diag.get("correlation"),
                )

            matched_log = format_matched_trades_log(similar)
            setup_eval = infer_setup_tag(features, decision.action) if decision.action in ("BUY", "SELL") else ""
            if decision.action in ("BUY", "SELL"):
                ok_pat, pat_reason, pat_stat = passes_pattern_execution_gate(
                    features,
                    patterns_live,
                    setup_eval,
                    min_win_rate=settings.pattern_min_win_rate,
                    min_sample_size=settings.pattern_min_sample_size,
                    strict_unknown=settings.pattern_gate_strict,
                )
                ps = score_pattern(features, patterns_live, setup_eval)
                conf_before_pat = decision.confidence
                if not ok_pat:
                    decision = Decision(
                        action="HOLD",
                        confidence=decision.confidence,
                        reason=f"{decision.reason}|pattern_block:{pat_reason}",
                        raw=dict(decision.raw),
                    )
                    log.info(
                        "pattern HARD HOLD: key=%s matched=%s win_rate=%s n=%s reason=%s",
                        ps.matched_key,
                        ps.matched,
                        (pat_stat or {}).get("win_rate"),
                        (pat_stat or {}).get("count"),
                        pat_reason,
                    )
                else:
                    new_conf, boosted = apply_pattern_confidence_boost(
                        decision.confidence,
                        pat_stat or {},
                        boost_min_win_rate=settings.pattern_boost_min_win_rate,
                        boost_min_sample=settings.pattern_boost_min_sample,
                        delta=settings.pattern_confidence_boost_delta,
                        cap=settings.pattern_confidence_cap,
                    )
                    if boosted:
                        decision = Decision(
                            action=decision.action,
                            confidence=new_conf,
                            reason=f"{decision.reason}|pattern_boost",
                            raw=dict(decision.raw),
                        )
                        decision = apply_confidence_floor(decision, settings.min_trade_confidence)
                    log.info(
                        "pattern OK: key=%s win_rate=%s n=%s model_boost=%.4f prob=%.3f | conf %.3f -> %.3f (boosted=%s)",
                        ps.matched_key,
                        (pat_stat or {}).get("win_rate"),
                        (pat_stat or {}).get("count"),
                        ps.confidence_boost,
                        ps.success_probability,
                        conf_before_pat,
                        decision.confidence,
                        boosted,
                    )

                if decision.action in ("BUY", "SELL"):
                    evolution_key = build_strategy_key(features, setup_eval)
                    snap = registry.snapshot()
                    log.info(
                        "strategy evolution: key=%s allowed=%s stats=%s",
                        evolution_key,
                        registry.is_strategy_allowed(evolution_key),
                        snap.get(evolution_key),
                    )
                    if not registry.is_strategy_allowed(evolution_key):
                        decision = Decision(
                            action="HOLD",
                            confidence=decision.confidence,
                            reason=f"{decision.reason}|strategy_disabled:{evolution_key}",
                            raw=dict(decision.raw),
                        )
                        log.warning(
                            "StrategyRegistry: key suppressed (low win rate / negative pnl) - HOLD %s",
                            evolution_key,
                        )
                    else:
                        conf_before_evo = decision.confidence
                        evo_boost = registry.get_strategy_boost(evolution_key)
                        if evo_boost > 0.0:
                            new_c = min(0.95, decision.confidence + evo_boost)
                            decision = Decision(
                                action=decision.action,
                                confidence=new_c,
                                reason=f"{decision.reason}|evolution_boost",
                                raw=dict(decision.raw),
                            )
                            decision = apply_confidence_floor(decision, settings.min_trade_confidence)
                            log.info(
                                "StrategyRegistry boost: key=%s +%.2f | conf %.3f -> %.3f",
                                evolution_key,
                                evo_boost,
                                conf_before_evo,
                                decision.confidence,
                            )

                    if (
                        settings.strategy_evolution_v2_enabled
                        and decision.action in ("BUY", "SELL")
                    ):
                        ok_rank, rank_reason = registry.passes_global_rank(
                            evolution_key,
                            top_n=settings.strategy_global_top_n,
                            exploration_max_trades=settings.strategy_exploration_max_trades,
                        )
                        if not ok_rank:
                            decision = Decision(
                                action="HOLD",
                                confidence=decision.confidence,
                                reason=f"{decision.reason}|global_rank:{rank_reason}",
                                raw=dict(decision.raw),
                            )
                            log.info(
                                "StrategyEvolution v2: global HOLD key=%s reason=%s top=%s explore_max=%s",
                                evolution_key,
                                rank_reason,
                                settings.strategy_global_top_n,
                                settings.strategy_exploration_max_trades,
                            )
                        else:
                            tops = registry.get_top_strategies(
                                min(5, settings.strategy_global_top_n),
                                active_only=True,
                                min_trades=1,
                            )
                            log.info(
                                "StrategyEvolution v2: rank OK key=%s (%s) top_sample=%s",
                                evolution_key,
                                rank_reason,
                                [(k, round(s.ranking_score, 5)) for k, s in tops],
                            )

                    if settings.memory_room_guard_enabled and decision.action in ("BUY", "SELL"):
                        room_guard = memory.get_room_guardrail(
                            symbol=settings.symbol,
                            session=str(features.get("session") or ""),
                            setup_tag=setup_eval,
                            trend_direction=str(features.get("trend_direction") or ""),
                            volatility=str(features.get("volatility") or ""),
                            strategy_key=evolution_key,
                        )
                        raw = dict(decision.raw)
                        raw["memory_room_guard"] = room_guard
                        if room_guard.get("blocked") and settings.memory_room_guard_block_anti:
                            decision = Decision(
                                action="HOLD",
                                confidence=decision.confidence,
                                reason=f"{decision.reason}|memory_guard:anti_pattern:{room_guard.get('room')}",
                                raw=raw,
                            )
                        else:
                            delta = float(room_guard.get("confidence_delta") or 0.0)
                            if delta != 0.0:
                                decision = Decision(
                                    action=decision.action,
                                    confidence=max(0.0, min(0.95, decision.confidence + delta)),
                                    reason=f"{decision.reason}|memory_guard:{room_guard.get('room')}",
                                    raw=raw,
                                )
                                decision = apply_confidence_floor(decision, settings.min_trade_confidence)
                        log.info(
                            "Memory room guard: room=%s blocked=%s caution=%s delta=%.3f notes=%s",
                            room_guard.get("room"),
                            room_guard.get("blocked"),
                            room_guard.get("caution"),
                            float(room_guard.get("confidence_delta") or 0.0),
                            len(list(room_guard.get("supporting_notes") or [])),
                        )

            log.info(
                "features=%s | decision=%s | conf=%.3f | reason=%s | matched=%s | pa=%s",
                json.dumps(features, ensure_ascii=False, sort_keys=True),
                decision.action,
                decision.confidence,
                decision.reason,
                matched_log,
                json.dumps(
                    {
                        "matched_pattern": pattern_analysis.get("matched_pattern"),
                        "win_rate": pattern_analysis.get("win_rate"),
                        "sample_size": pattern_analysis.get("sample_size"),
                    },
                    ensure_ascii=False,
                ),
            )

            if not risk.can_trade() and decision.action in ("BUY", "SELL"):
                log.warning("Risk block - overriding %s to HOLD", decision.action)
                decision = Decision(
                    action="HOLD",
                    confidence=0.0,
                    reason="risk_block_session",
                    raw=decision.raw,
                )

            trade_volume = float(settings.default_volume)
            if (
                decision.action in ("BUY", "SELL")
                and settings.strategy_evolution_v2_enabled
                and settings.strategy_capital_weighting_enabled
            ):
                st_exec = infer_setup_tag(features, decision.action)
                sk_exec = build_strategy_key(features, st_exec)
                mult = registry.get_position_size_multiplier(
                    sk_exec,
                    pool=settings.strategy_capital_pool,
                    clamp_min=settings.strategy_capital_mult_min,
                    clamp_max=settings.strategy_capital_mult_max,
                )
                trade_volume = max(1e-9, float(settings.default_volume) * mult)
                log.info(
                    "StrategyEvolution v2: volume key=%s mult=%.4f base=%s -> vol=%.6f",
                    sk_exec,
                    mult,
                    settings.default_volume,
                    trade_volume,
                )
            if decision.action in ("BUY", "SELL"):
                capped_volume, cap_reason = await _cap_trade_volume_for_exposure(
                    broker=broker,
                    execution=execution,
                    settings=settings,
                    symbol=settings.symbol,
                    action=decision.action,
                    requested_volume=trade_volume,
                    confidence=float(decision.confidence),
                )
                if capped_volume <= 0:
                    log.warning(
                        "Exposure cap - overriding %s to HOLD: %s",
                        decision.action,
                        cap_reason,
                    )
                    decision = Decision(
                        action="HOLD",
                        confidence=0.0,
                        reason=f"{decision.reason}|exposure_cap:{cap_reason}",
                        raw={**dict(decision.raw), "exposure_cap": cap_reason},
                    )
                    trade_volume = 0.0
                else:
                    if abs(capped_volume - trade_volume) > 1e-9:
                        log.info(
                            "Exposure cap adjusted volume %s %s: %.4f -> %.4f (%s)",
                            settings.symbol,
                            decision.action,
                            trade_volume,
                            capped_volume,
                            cap_reason,
                        )
                    trade_volume = capped_volume

            outcome = await execution.execute_trade(
                symbol=settings.symbol,
                action=decision.action,
                volume=trade_volume,
                decision_reason=decision.reason,
                dry_run=settings.dry_run,
            )

            closed_positions = list(outcome.closes or ([] if outcome.close is None else [outcome.close]))
            if closed_positions:
                closed_contexts = list(open_contexts[: len(closed_positions)])
                if len(closed_contexts) < len(closed_positions):
                    log.warning(
                        "Close context mismatch: closes=%s contexts=%s for %s",
                        len(closed_positions),
                        len(closed_contexts),
                        settings.symbol,
                    )
                for idx, close in enumerate(closed_positions):
                    notional = close.notional_approx()
                    tscore = evaluate_outcome(
                        close.pnl,
                        notional=notional,
                        neutral_rel_threshold=settings.neutral_pnl_threshold,
                    )
                    score_int = int(tscore)
                    close_context = closed_contexts[idx] if idx < len(closed_contexts) else None

                    if close_context is not None:
                        sk_close = str(close_context.get("strategy_key") or "")
                        record = MemoryRecord(
                            market=dict(close_context["market"]),
                            features=dict(close_context["features"]),
                            decision=dict(close_context["decision"]),
                            result={
                                "pnl": close.pnl,
                                "exit_price": close.exit_price,
                                "entry_price": close.entry_price,
                            },
                            score=score_int,
                            setup_tag=str(close_context["setup_tag"]),
                            strategy_key=sk_close,
                            journal=str(close_context["journal"]),
                            tags=list(close_context.get("tags") or []),
                        )
                        memory.store_memory(
                            record,
                            extra_metadata={
                                "trade_score": score_int,
                                "strategy_key": sk_close,
                            },
                        )
                        closed_confidence = float((close_context.get("decision") or {}).get("confidence") or 0.0)
                        current_room = str(
                            sk_close
                            or build_strategy_key(
                                dict(close_context["features"]),
                                str(close_context["setup_tag"]),
                            )
                        )
                        if score_int < 0 and closed_confidence >= 0.75:
                            memory.store_note(
                                MemoryNote(
                                    title="Overconfident loss",
                                    content=(
                                        f"Loss recorded in room={current_room} with confidence={closed_confidence:.3f} "
                                        f"pnl={float(close.pnl):.6f}. Treat as anti-pattern candidate until more evidence arrives."
                                    ),
                                    wing=f"symbol:{str(settings.symbol).lower()}",
                                    hall="hall_discoveries",
                                    room=current_room,
                                    note_type="anti_pattern_candidate",
                                    hall_type="hall_discoveries",
                                    symbol=settings.symbol,
                                    session=str(close_context["features"].get("session") or ""),
                                    setup_tag=str(close_context["setup_tag"]),
                                    strategy_key=sk_close,
                                    importance=0.9,
                                    source="trade_close",
                                    tags=["anti-pattern", "overconfident-loss", current_room],
                                )
                            )
                        elif score_int > 0 and closed_confidence < 0.55:
                            memory.store_note(
                                MemoryNote(
                                    title="Underconfident win",
                                    content=(
                                        f"Win recorded in room={current_room} with confidence={closed_confidence:.3f} "
                                        f"pnl={float(close.pnl):.6f}. This may be an opportunity room worth promoting."
                                    ),
                                    wing=f"symbol:{str(settings.symbol).lower()}",
                                    hall="hall_discoveries",
                                    room=current_room,
                                    note_type="opportunity_candidate",
                                    hall_type="hall_discoveries",
                                    symbol=settings.symbol,
                                    session=str(close_context["features"].get("session") or ""),
                                    setup_tag=str(close_context["setup_tag"]),
                                    strategy_key=sk_close,
                                    importance=0.78,
                                    source="trade_close",
                                    tags=["opportunity", "underconfident-win", current_room],
                                )
                            )
                        if sk_close:
                            registry.update_strategy(
                                sk_close,
                                {"pnl": float(close.pnl), "score": score_int},
                            )
                            if settings.correlation_engine_enabled and correlation is not None:
                                correlation.update_pnl(sk_close, float(close.pnl))
                        pattern_book.append_closed_trade(
                            features=dict(close_context["features"]),
                            setup_tag=str(close_context["setup_tag"]),
                            score=score_int,
                            pnl=float(close.pnl),
                        )
                    else:
                        memory.store_note(
                            MemoryNote(
                                title="Close without context",
                                content=(
                                    f"Closed {close.side} {close.symbol} without matching open context. "
                                    f"entry={close.entry_price:.5f} exit={close.exit_price:.5f} pnl={float(close.pnl):.6f}"
                                ),
                                wing="execution",
                                hall="hall_events",
                                room=f"close-without-context:{str(settings.symbol).lower()}",
                                note_type="runtime_gap",
                                hall_type="hall_events",
                                symbol=settings.symbol,
                                importance=0.7,
                                source="learning_loop",
                                tags=["runtime-gap", "close-without-context", close.side.lower()],
                            )
                        )

                    risk.on_trade_result(tscore, pnl=close.pnl)
                    perf.record_close(close.pnl, score=score_int)
                    if settings.performance_monitor_enabled:
                        perf_mon.update_on_trade(float(close.pnl), score_int)

                open_contexts = open_contexts[len(closed_positions) :]
                persist_runtime_state()

            opened = (
                decision.action in ("BUY", "SELL")
                and outcome.trade.message not in ("hold", "skip_same_side_open")
            )
            if (
                decision.action in ("BUY", "SELL")
                and not settings.dry_run
                and not outcome.trade.executed
            ):
                failure_room = f"execution:{decision.action.lower()}:{settings.symbol.lower()}"
                memory.store_note(
                    MemoryNote(
                        title="Execution failure",
                        content=(
                            f"Failed to execute {decision.action} for {settings.symbol}. "
                            f"message={outcome.trade.message} reason={decision.reason}"
                        ),
                        wing="execution",
                        hall="hall_events",
                        room=failure_room,
                        note_type="execution_failure",
                        hall_type="hall_events",
                        symbol=settings.symbol,
                        session=str(features.get("session") or ""),
                        setup_tag=setup_eval,
                        strategy_key=build_strategy_key(features, setup_eval) if setup_eval else "",
                        importance=0.82,
                        source="execution_service",
                        tags=["execution-failure", settings.symbol, decision.action.lower()],
                    )
                )
            if settings.performance_monitor_enabled:
                perf_mon.update_after_execution(
                    decision.action,
                    opened=bool(opened and (outcome.trade.executed or settings.dry_run)),
                )
            if opened and (outcome.trade.executed or settings.dry_run):
                tag = infer_setup_tag(features, decision.action)
                sk_open = build_strategy_key(features, tag)
                open_contexts.append(
                    {
                    "market": market.as_prompt_dict(),
                    "features": dict(features),
                    "decision": {
                        "action": decision.action,
                        "confidence": decision.confidence,
                        "reason": decision.reason,
                    },
                    "setup_tag": tag,
                    "strategy_key": sk_open,
                    "journal": _journal_structured(
                        market,
                        features,
                        decision,
                        settings,
                        tag,
                        strategy_key=sk_open,
                    ),
                    "tags": [settings.symbol, tag, str(features.get("session", "")), sk_open],
                    }
                )
                persist_runtime_state()

            if memory.count() > 0:
                registry.sync_promotion_hints(
                    list((memory.get_memory_intelligence() or {}).get("promotion_pipeline") or [])
                )

            log.info("performance snapshot: %s", perf.summary())
            if settings.performance_monitor_enabled:
                perf_mon.update_on_strategy(registry)
                perf_mon.tick_cycle_end()
                perf_mon.maybe_log_summary_and_alerts()

            persist_runtime_state()
        except Exception as exc:
            log.exception("Loop cycle failed: %s", exc)
            try:
                memory.store_note(
                    MemoryNote(
                        title="Loop cycle failure",
                        content=str(exc),
                        wing="execution",
                        hall="hall_events",
                        room="loop-cycle-failure",
                        note_type="runtime_failure",
                        hall_type="hall_events",
                        symbol=settings.symbol,
                        importance=0.75,
                        source="learning_loop",
                        tags=["runtime-failure", type(exc).__name__],
                    )
                )
            except Exception:
                log.exception("Failed to persist runtime failure note")
            persist_runtime_state()
        await asyncio.sleep(settings.loop_interval_sec)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mempalac autonomous trading AI engine")
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Override LOOP_INTERVAL_SEC",
    )
    p.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force dry_run on/off for this process",
    )
    p.add_argument(
        "--smoke-worker",
        action="store_true",
        help="Send one BUY through Dexter ctrader_execute_once.py then exit (no LLM loop)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    if args.interval is not None:
        settings.loop_interval_sec = args.interval
    if args.dry_run is not None:
        settings.dry_run = args.dry_run
    _enforce_live_safety(settings)
    if args.smoke_worker:
        asyncio.run(smoke_ctrader_worker(settings))
        return
    asyncio.run(learning_loop(settings))


if __name__ == "__main__":
    main()
