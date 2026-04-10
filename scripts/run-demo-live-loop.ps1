param(
    [double]$Interval
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:LIVE_EXECUTION_ENABLED = "true"
$env:DRY_RUN = "false"
$env:LLM_PROVIDER = "local"
$env:LOCAL_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
$env:LOCAL_MODEL_NAME = "qwen2.5:1.5b"
$env:LOCAL_API_KEY = "ollama"
$env:LOCAL_FALLBACK_MODELS = "qwen2.5:0.5b,gemma3:1b-it-qat"
$env:LLM_TIMEOUT_SEC = "25"
$env:LLM_MAX_TOKENS = "120"
$env:SIMILAR_TRADES_TOP_K = "2"
$env:MEMORY_WAKEUP_TOP_K = "2"
$env:MEMORY_NOTE_TOP_K = "2"
$env:LLM_MAX_RETRIES = "1"
$env:LLM_FALLBACK_ENABLED = "true"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:CTRADER_WORKER_DEBUG = "1"
$env:CTRADER_QUOTE_SOURCE = "dexter_reference"
$env:HARD_FILTER_MIN_CLOSES = "0"

if ($PSBoundParameters.ContainsKey("Interval")) {
    & (Join-Path $PSScriptRoot "start-loop.ps1") -Interval $Interval -NoDryRun
} else {
    & (Join-Path $PSScriptRoot "start-loop.ps1") -NoDryRun
}
