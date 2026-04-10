from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderName(str, Enum):
    OPENAI = "openai"
    MIMO = "mimo"
    LOCAL = "local"


class Settings(BaseSettings):
    """Environment-driven configuration. Use `.env` in project root or export vars."""

    model_config = SettingsConfigDict(
        env_file=(
            str(Path(__file__).resolve().with_name(".env")),
            ".env",
        ),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # --- Runtime ---
    instance_name: str = Field(default="mempalac", validation_alias="INSTANCE_NAME")
    symbol: str = Field(default="XAUUSD", description="Default trading symbol")
    dry_run: bool = Field(default=True, validation_alias="DRY_RUN")
    live_execution_enabled: bool = Field(
        default=False,
        validation_alias="LIVE_EXECUTION_ENABLED",
        description="Explicit safety gate: required in addition to DRY_RUN=false before live orders are allowed.",
    )
    loop_interval_sec: float = Field(default=30.0, validation_alias="LOOP_INTERVAL_SEC")
    data_dir: Path = Field(default=Path("./data"), validation_alias="DATA_DIR")
    runtime_state_path: Path = Field(
        default=Path("./data/runtime_state.json"),
        validation_alias="RUNTIME_STATE_PATH",
        description="Crash-recovery state for the active Mempalac process.",
    )
    strategy_registry_path: Path = Field(
        default=Path("./data/strategy_registry.json"),
        validation_alias="STRATEGY_REGISTRY_PATH",
        description="JSON persistence for StrategyRegistry (runtime evolution).",
    )

    # --- LLM ---
    llm_provider: LLMProviderName = Field(
        default=LLMProviderName.OPENAI, validation_alias="LLM_PROVIDER"
    )
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    openai_base_url: Optional[str] = Field(default=None, validation_alias="OPENAI_BASE_URL")
    openai_fallback_models: str = Field(
        default="",
        validation_alias="OPENAI_FALLBACK_MODELS",
        description="Comma-separated failover models for the same OpenAI-compatible endpoint.",
    )

    mimo_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("MIMO_API_KEY", "MIMO_API"),
    )
    mimo_base_url: str = Field(
        default="https://api.xiaomimimo.com/v1",
        validation_alias="MIMO_BASE_URL",
    )
    mimo_model: str = Field(default="mimo-v2-pro", validation_alias="MIMO_MODEL")

    local_base_url: str = Field(
        default="http://127.0.0.1:11434/v1", validation_alias="LOCAL_LLM_BASE_URL"
    )
    local_model: str = Field(default="qwen2.5", validation_alias="LOCAL_MODEL_NAME")
    local_api_key: str = Field(default="ollama", validation_alias="LOCAL_API_KEY")
    local_fallback_models: str = Field(
        default="",
        validation_alias="LOCAL_FALLBACK_MODELS",
        description="Comma-separated failover models for the same local OpenAI-compatible endpoint.",
    )

    llm_timeout_sec: float = Field(default=120.0, validation_alias="LLM_TIMEOUT_SEC")
    llm_max_retries: int = Field(default=4, validation_alias="LLM_MAX_RETRIES")
    llm_max_tokens: int = Field(default=256, validation_alias="LLM_MAX_TOKENS")
    llm_fallback_enabled: bool = Field(
        default=True,
        validation_alias="LLM_FALLBACK_ENABLED",
        description="Use a conservative heuristic fallback when the configured LLM is unavailable.",
    )

    # --- Memory ---
    memory_backend: Literal["chroma", "mempalace_chroma"] = Field(
        default="chroma", validation_alias="MEMORY_BACKEND"
    )
    chroma_path: Path = Field(
        default=Path("./data/chroma_trading"),
        validation_alias="CHROMA_PATH",
    )
    mempalace_chroma_path: Optional[Path] = Field(
        default=None,
        validation_alias="MEMPALACE_CHROMA_PATH",
        description="If set, uses existing MemPalace Chroma directory (read/write with care).",
    )
    memory_collection: str = Field(
        default="trading_experiences", validation_alias="MEMORY_COLLECTION"
    )
    recall_top_k: int = Field(default=8, validation_alias="RECALL_TOP_K")
    memory_score_weight: float = Field(
        default=0.35,
        validation_alias="MEMORY_SCORE_WEIGHT",
        description="Blend: final_rank = (1-w)*similarity + w*normalized_memory_score",
    )
    memory_wakeup_top_k: int = Field(
        default=6,
        validation_alias="MEMORY_WAKEUP_TOP_K",
        description="MemPalace-style wake-up context size for each decision cycle.",
    )
    memory_note_top_k: int = Field(
        default=6,
        validation_alias="MEMORY_NOTE_TOP_K",
        description="Maximum non-trade palace notes injected into wake-up context.",
    )
    memory_room_guard_enabled: bool = Field(
        default=True,
        validation_alias="MEMORY_ROOM_GUARD_ENABLED",
        description="Use winner/danger/anti-pattern room intelligence in the live decision loop.",
    )
    memory_room_guard_block_anti: bool = Field(
        default=True,
        validation_alias="MEMORY_ROOM_GUARD_BLOCK_ANTI",
        description="Force HOLD when the current room is classified as an anti-pattern.",
    )

    # --- Risk ---
    max_trades_per_session: int = Field(
        default=50, validation_alias="MAX_TRADES_PER_SESSION"
    )
    max_consecutive_losses: int = Field(
        default=5, validation_alias="MAX_CONSECUTIVE_LOSSES"
    )
    neutral_pnl_threshold: float = Field(
        default=1e-4,
        validation_alias="NEUTRAL_PNL_THRESHOLD",
        description="Relative return below this (absolute) counts as neutral score.",
    )
    min_trade_confidence: float = Field(
        default=0.65,
        validation_alias="MIN_TRADE_CONFIDENCE",
        description="Below this confidence, action is forced to HOLD after LLM response.",
    )
    entry_loss_streak_block: int = Field(
        default=3,
        validation_alias="ENTRY_LOSS_STREAK_BLOCK",
        description="Block new entries when consecutive_losses >= this value (still below full risk halt).",
    )
    price_history_max: int = Field(
        default=96,
        validation_alias="PRICE_HISTORY_MAX",
        description="Rolling mids retained for feature extraction.",
    )
    similar_trades_top_k: int = Field(
        default=5,
        validation_alias="SIMILAR_TRADES_TOP_K",
        description="Top-K similar memories for the agent prompt.",
    )

    pattern_min_win_rate: float = Field(
        default=0.55,
        validation_alias="PATTERN_MIN_WIN_RATE",
        description="Hard block entries when historical pattern win_rate is below this.",
    )
    pattern_min_sample_size: int = Field(
        default=5,
        validation_alias="PATTERN_MIN_SAMPLE_SIZE",
        description="Hard block entries when pattern bucket has fewer closed trades.",
    )
    pattern_boost_min_win_rate: float = Field(
        default=0.65,
        validation_alias="PATTERN_BOOST_MIN_WIN_RATE",
    )
    pattern_boost_min_sample: int = Field(
        default=10,
        validation_alias="PATTERN_BOOST_MIN_SAMPLE",
    )
    pattern_confidence_boost_delta: float = Field(
        default=0.1,
        validation_alias="PATTERN_CONFIDENCE_BOOST_DELTA",
    )
    pattern_confidence_cap: float = Field(
        default=0.95,
        validation_alias="PATTERN_CONFIDENCE_CAP",
    )
    pattern_gate_strict: bool = Field(
        default=False,
        validation_alias="PATTERN_GATE_STRICT",
        description=(
            "If True: block entries when no historical pattern bucket exists (cold start never trades). "
            "If False: allow unknown buckets so the first positions can fire; existing buckets still "
            "respect min win-rate and sample size."
        ),
    )

    hard_filter_min_closes: int = Field(
        default=12,
        ge=0,
        validation_alias="HARD_FILTER_MIN_CLOSES",
        description=(
            "Require at least this many closes in features before volatility/trend hard-vetoes apply. "
            "Paper/prime loop starts with RANGE/LOW — set 0 to always apply vetoes."
        ),
    )

    # --- cTrader (names aligned with Dexter Pro — paste from .env.local) ---
    ctrader_client_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "CTRADER_OPENAPI_CLIENT_ID",
            "CTRADER_CLIENT_ID",
            "OpenAPI_ClientID",
            "Client ID",
        ),
    )
    ctrader_client_secret: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "CTRADER_OPENAPI_CLIENT_SECRET",
            "CTRADER_CLIENT_SECRET",
            "OpenAPI_Secreat",
            "OpenAPI_Secret",
            "Secret",
        ),
    )
    ctrader_redirect_uri: str = Field(
        default="http://localhost",
        validation_alias=AliasChoices(
            "CTRADER_OPENAPI_REDIRECT_URI",
        ),
    )
    ctrader_access_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "CTRADER_OPENAPI_ACCESS_TOKEN",
            "CTRADER_ACCESS_TOKEN",
            "OpenAPI_Access_token_API_key",
            "OpenAPI_Access_token_API_key2",
            "OpenAPI_Access_token_API_key3",
            "new_Accesstoken",
            "new_Access_token",
        ),
    )
    ctrader_refresh_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "CTRADER_OPENAPI_REFRESH_TOKEN",
            "CTRADER_REFRESH_TOKEN",
            "OpenAPI_Refresh_token_API_key",
            "OpenAPI_Refresh_token_API_key2",
            "OpenAPI_Refresh_token_API_key3",
            "new_Refresh_token",
        ),
    )
    ctrader_account_id: Optional[str] = Field(default=None, validation_alias="CTRADER_ACCOUNT_ID")
    ctrader_account_login: Optional[str] = Field(
        default=None, validation_alias="CTRADER_ACCOUNT_LOGIN"
    )
    ctrader_demo: bool = Field(
        default=True,
        validation_alias=AliasChoices("CTRADER_USE_DEMO", "CTRADER_DEMO"),
    )
    ctrader_enabled: bool = Field(default=False, validation_alias="CTRADER_ENABLED")

    ctrader_dexter_worker: bool = Field(
        default=False,
        validation_alias="CTRADER_DEXTER_WORKER",
        description=(
            "True: route orders through Dexter ops/ctrader_execute_once.py (real Open API). "
            "Quotes for the AI loop stay on PaperBroker unless you add another feed."
        ),
    )
    ctrader_worker_script: Optional[Path] = Field(
        default=None,
        validation_alias="CTRADER_WORKER_SCRIPT",
        description="Path to dexter_pro_v3_fixed/ops/ctrader_execute_once.py",
    )
    ctrader_worker_python: Optional[str] = Field(
        default=None,
        validation_alias="CTRADER_WORKER_PYTHON",
        description="Python exe with ctrader_open_api + Twisted (use Dexter venv if needed).",
    )
    ctrader_worker_timeout_sec: int = Field(
        default=120,
        ge=20,
        le=600,
        validation_alias="CTRADER_WORKER_TIMEOUT_SEC",
    )
    ctrader_quote_source: Literal["auto", "paper", "dexter_capture", "dexter_reference"] = Field(
        default="auto",
        validation_alias="CTRADER_QUOTE_SOURCE",
        description=(
            "Quote source for the loop when using Dexter worker. "
            "'auto' prefers Dexter capture_market then Dexter-style reference quote; "
            "'paper' keeps synthetic quotes."
        ),
    )
    ctrader_quote_cache_ttl_sec: float = Field(
        default=2.0,
        ge=0.0,
        le=60.0,
        validation_alias="CTRADER_QUOTE_CACHE_TTL_SEC",
    )
    ctrader_capture_duration_sec: int = Field(
        default=3,
        ge=1,
        le=15,
        validation_alias="CTRADER_CAPTURE_DURATION_SEC",
    )
    ctrader_reference_quote_fallback_enabled: bool = Field(
        default=True,
        validation_alias="CTRADER_REFERENCE_QUOTE_FALLBACK_ENABLED",
        description=(
            "Fallback to Dexter-style external reference quote when cTrader capture_market is unavailable. "
            "For XAUUSD this uses Stooq spot CSV without importing Dexter or touching Dexter state."
        ),
    )
    ctrader_reference_quote_spread: float = Field(
        default=0.10,
        ge=0.0,
        le=10.0,
        validation_alias="CTRADER_REFERENCE_QUOTE_SPREAD",
        description="Synthetic bid/ask spread around Dexter-style reference mid when only mid price is available.",
    )
    ctrader_reference_quote_timeout_sec: float = Field(
        default=8.0,
        ge=1.0,
        le=30.0,
        validation_alias="CTRADER_REFERENCE_QUOTE_TIMEOUT_SEC",
    )
    ctrader_worker_volume_scale: int = Field(
        default=100,
        ge=1,
        le=10_000_000,
        validation_alias="CTRADER_WORKER_VOLUME_SCALE",
        description="fixed_volume=int(round(DEFAULT_VOLUME*scale)). 100 => 0.01 lot -> 1.",
    )

    default_volume: float = Field(default=0.01, validation_alias="DEFAULT_VOLUME")

    # --- Strategy evolution v2 (GPT-style; off by default — cuts frequency & adds gates) ---
    strategy_evolution_v2_enabled: bool = Field(
        default=False,
        validation_alias="STRATEGY_EVOLUTION_V2_ENABLED",
        description="When True: global top-N gate, capital weighting, per-loop ranking decay.",
    )
    strategy_global_top_n: int = Field(
        default=10,
        validation_alias="STRATEGY_GLOBAL_TOP_N",
        description="Mature strategies must rank in top N by ranking_score (exploration bypass below threshold).",
    )
    strategy_exploration_max_trades: int = Field(
        default=5,
        validation_alias="STRATEGY_EXPLORATION_MAX_TRADES",
        description="Bypass global rank filter while trades < this (per strategy key).",
    )
    strategy_aging_enabled: bool = Field(
        default=False,
        validation_alias="STRATEGY_AGING_ENABLED",
    )
    strategy_aging_factor: float = Field(
        default=0.995,
        validation_alias="STRATEGY_AGING_FACTOR",
        ge=0.5,
        lt=1.0,
        description="Per-loop multiplier on ranking_score; 1.0 disables (handled as skip in code).",
    )
    strategy_capital_weighting_enabled: bool = Field(
        default=False,
        validation_alias="STRATEGY_CAPITAL_WEIGHTING_ENABLED",
    )
    strategy_capital_pool: int = Field(
        default=5,
        validation_alias="STRATEGY_CAPITAL_POOL",
        ge=1,
        description="Top-K strategies by ranking_score for volume share denominator.",
    )
    strategy_capital_mult_min: float = Field(
        default=0.25,
        validation_alias="STRATEGY_CAPITAL_MULT_MIN",
    )
    strategy_capital_mult_max: float = Field(
        default=2.0,
        validation_alias="STRATEGY_CAPITAL_MULT_MAX",
    )

    # --- Portfolio intelligence (GPT-style fusion; off by default) ---
    portfolio_intelligence_enabled: bool = Field(
        default=False,
        validation_alias="PORTFOLIO_INTELLIGENCE_ENABLED",
        description="When True: fuse LLM + memory + structure (can add extra HOLDs).",
    )
    portfolio_weight_llm: float = Field(
        default=1.0,
        validation_alias="PORTFOLIO_WEIGHT_LLM",
        ge=0.0,
    )
    portfolio_weight_memory: float = Field(
        default=0.55,
        validation_alias="PORTFOLIO_WEIGHT_MEMORY",
        ge=0.0,
    )
    portfolio_weight_structure: float = Field(
        default=0.35,
        validation_alias="PORTFOLIO_WEIGHT_STRUCTURE",
        ge=0.0,
    )
    portfolio_tie_margin: float = Field(
        default=0.08,
        validation_alias="PORTFOLIO_TIE_MARGIN",
        ge=0.0,
        le=0.45,
        description="Required relative edge of buy_mass vs sell_mass to take a side.",
    )
    portfolio_llm_anchor_confidence: float = Field(
        default=0.0,
        validation_alias="PORTFOLIO_LLM_ANCHOR_CONFIDENCE",
        ge=0.0,
        le=1.0,
        description="If >0 and LLM confidence >= this, fusion cannot flip direction (only align/HOLD).",
    )

    # --- Correlation engine (GPT-style; requires portfolio fusion to matter) ---
    correlation_engine_enabled: bool = Field(
        default=False,
        validation_alias="CORRELATION_ENGINE_ENABLED",
        description="When True + portfolio on: penalize redundant strategy_keys in fusion.",
    )
    strategy_correlation_path: Path = Field(
        default=Path("./data/strategy_correlation.json"),
        validation_alias="STRATEGY_CORRELATION_PATH",
    )
    correlation_max_history: int = Field(
        default=100,
        validation_alias="CORRELATION_MAX_HISTORY",
        ge=5,
        le=5000,
    )
    correlation_min_samples: int = Field(
        default=10,
        validation_alias="CORRELATION_MIN_SAMPLES",
        ge=5,
        le=500,
        description="Minimum overlapping closes for matrix + penalty/diversity.",
    )
    correlation_penalty_mid: float = Field(
        default=0.3,
        validation_alias="CORRELATION_PENALTY_MID",
        ge=0.0,
        le=1.0,
        description="Extra penalty mass when Pearson r > CORRELATION_PENALTY_MID_THRESHOLD.",
    )
    correlation_penalty_high: float = Field(
        default=0.5,
        validation_alias="CORRELATION_PENALTY_HIGH",
        ge=0.0,
        le=1.0,
        description="Extra penalty when r > CORRELATION_PENALTY_HIGH_THRESHOLD.",
    )
    correlation_penalty_mid_threshold: float = Field(
        default=0.8,
        validation_alias="CORRELATION_PENALTY_MID_THRESHOLD",
        ge=0.0,
        lt=1.0,
    )
    correlation_penalty_high_threshold: float = Field(
        default=0.9,
        validation_alias="CORRELATION_PENALTY_HIGH_THRESHOLD",
        ge=0.0,
        lt=1.0,
    )
    correlation_max_penalty: float = Field(
        default=0.7,
        validation_alias="CORRELATION_MAX_PENALTY",
        ge=0.0,
        le=1.0,
    )
    correlation_diversity_bonus: float = Field(
        default=0.1,
        validation_alias="CORRELATION_DIVERSITY_BONUS",
        ge=0.0,
        le=0.5,
    )
    correlation_diversity_threshold: float = Field(
        default=0.2,
        validation_alias="CORRELATION_DIVERSITY_THRESHOLD",
        ge=0.0,
        lt=1.0,
        description="Bonus only if r < this vs all eligible peers.",
    )

    # --- Performance monitor (measurement only; safe to leave on) ---
    performance_monitor_enabled: bool = Field(
        default=True,
        validation_alias="PERFORMANCE_MONITOR_ENABLED",
    )
    performance_log_interval: int = Field(
        default=50,
        validation_alias="PERFORMANCE_LOG_INTERVAL",
        ge=1,
        le=1_000_000,
        description="Every N loop cycles print [PERFORMANCE SUMMARY].",
    )
    performance_alert_max_drawdown: float = Field(
        default=0.0,
        validation_alias="PERFORMANCE_ALERT_MAX_DRAWDOWN",
        ge=0.0,
        description="If >0, WARN when max drawdown (PnL units) exceeds this.",
    )
    performance_alert_selectivity_min: float = Field(
        default=0.03,
        validation_alias="PERFORMANCE_ALERT_SELECTIVITY_MIN",
        ge=0.0,
        le=1.0,
        description="WARN when opens/LLM-intents below this after min intents.",
    )
    performance_alert_min_llm_intents: int = Field(
        default=40,
        validation_alias="PERFORMANCE_ALERT_MIN_LLM_INTENTS",
        ge=5,
        le=500_000,
    )

    # --- API server (optional) ---
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8080, validation_alias="API_PORT")

    @model_validator(mode="after")
    def _normalize_paths(self) -> "Settings":
        base = self.data_dir.expanduser()
        if not base.is_absolute():
            base = base.resolve()
        self.data_dir = base
        self.runtime_state_path = _resolve_path(self.runtime_state_path, base)
        self.strategy_registry_path = _resolve_path(self.strategy_registry_path, base)
        self.strategy_correlation_path = _resolve_path(self.strategy_correlation_path, base)
        self.chroma_path = _resolve_path(self.chroma_path, base)
        if self.mempalace_chroma_path is not None:
            self.mempalace_chroma_path = _resolve_external_path(self.mempalace_chroma_path)
        if self.ctrader_worker_script is not None:
            self.ctrader_worker_script = _resolve_external_path(self.ctrader_worker_script)
        return self


def _resolve_external_path(path: Path) -> Path:
    p = Path(path).expanduser()
    return p.resolve() if p.is_absolute() else p.resolve()


def _resolve_path(path: Path, data_dir: Path) -> Path:
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    parts = p.parts
    if parts[:1] == ("data",):
        return (data_dir / Path(*parts[1:])).resolve()
    return (data_dir / p).resolve()


def load_settings() -> Settings:
    return Settings()


def memory_persist_path(settings: Settings) -> Path:
    if settings.memory_backend == "mempalace_chroma" and settings.mempalace_chroma_path:
        return Path(settings.mempalace_chroma_path)
    return Path(settings.chroma_path)
