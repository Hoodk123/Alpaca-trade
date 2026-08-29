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
    LLM_MODEL = os.getenv("LLM_MODEL", "meta/llama-3.3-70b-instruct")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")

    @classmethod
    def validate(cls):
        missing = [
            name for name, val in [
                ("ALPACA_API_KEY", cls.ALPACA_API_KEY),
                ("ALPACA_SECRET_KEY", cls.ALPACA_SECRET_KEY),
                ("LLM_API_KEY", cls.LLM_API_KEY),
            ] if not val
        ]
        if missing:
            raise ValueError(
                f"Missing required .env values: {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )
