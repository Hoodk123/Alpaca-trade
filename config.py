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
