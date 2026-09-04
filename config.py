"""Loads settings from .env into one place so nothing else has to touch os.environ."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
    ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "nvidia")
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))

    # Default watchlist of large, liquid names - good signal-to-noise for a demo.
    # TSM = TSMC's US-listed ADR (there's no separate "TSMC" ticker on US exchanges).
    DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "TSM", "AMZN"]

    # Only place an order if the LLM's confidence is at least this high.
    # Below this, the symbol still shows up in the recommendation table as watch-only.
    MIN_CONFIDENCE_TO_TRADE = 0.6

    # Max % of paper cash to risk on a single BUY (simple position sizing).
    MAX_CASH_PCT_PER_TRADE = 0.05

    # Hard stop-loss floor (as % P/L): if a held position's real-time
    # unrealized_plpc drops to this loss level or below, force a SELL regardless
    # of what the LLM says. The model's own discretionary SELL is in addition
    # to this floor. A value of None disables the hard stop.
    HARD_STOP_LOSS_PCT = -8.0

    # Dashboard goal-of-the-day: the progress bar is capped at 100% and the
    # amber stop-warning badge lights up when a position is within this many
    # percentage points of the hard stop-loss threshold.
    GOAL_STATE_PATH = "goal_state.json"
    BASELINE_PATH = "baseline.json"
    STOP_WARNING_BUFFER_PCT = 2.0

    _PLACEHOLDER_MARKERS = ("your_", "_here", "changeme", "xxx", "example")

    @classmethod
    def _is_placeholder(cls, value) -> bool:
        if not value:
            return True
        v = str(value).strip().lower()
        return any(marker in v for marker in cls._PLACEHOLDER_MARKERS)

    @classmethod
    def validate(cls):
        needed = {
            "ALPACA_API_KEY": "Add your Alpaca paper API key (sign up at alpaca.markets).",
            "ALPACA_SECRET_KEY": "Add your Alpaca paper secret key.",
            "LLM_API_KEY": "Add your LLM API key (NVIDIA NIM or OpenAI) so the agent can decide.",
        }
        missing = [name for name in needed if cls._is_placeholder(getattr(cls, name))]
        if missing:
            lines = ["[config] Missing or placeholder .env value(s): " + ", ".join(missing) + "."]
            for name in missing:
                lines.append(f"  - {name}: {needed[name]}")
            lines.append("Edit .env (copy .env.example to .env if you haven't).")
            raise SystemExit("\n".join(lines))