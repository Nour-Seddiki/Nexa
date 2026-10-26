"""Settings. Everything here can be overridden by an environment variable."""

from __future__ import annotations

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent


def _str(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


def _num(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    return int(_num(name, default))


def _flag(name: str, default: bool) -> bool:
    return _str(name, "1" if default else "0").lower() not in ("0", "false", "no", "off")


# ---- model ----------------------------------------------------------------
MODEL = _str("NEXA_MODEL", "qwen3.5:9b")
HOST = _str("NEXA_HOST", "http://127.0.0.1:11434").rstrip("/")
NUM_CTX = _int("NEXA_NUM_CTX", 16384)
TEMPERATURE = _num("NEXA_TEMPERATURE", 0.3)
THINK = _flag("NEXA_THINK", True)          # Qwen3.5 reasons before picking a tool
TIMEOUT = _int("NEXA_TIMEOUT", 600)        # seconds for one /api/chat call
MAX_STEPS = _int("NEXA_MAX_STEPS", 12)     # tool round-trips per user message
HISTORY_TURNS = _int("NEXA_HISTORY", 40)   # messages kept after the system prompt

# ---- money ----------------------------------------------------------------
CURRENCY = _str("NEXA_CURRENCY", "EUR")
SYMBOL = {"EUR": "€", "USD": "$", "GBP": "£"}.get(CURRENCY, CURRENCY + " ")
MAX_ORDER = _num("NEXA_MAX_ORDER", 400.0)   # per purchase/booking, hard refusal above
DAILY_CAP = _num("NEXA_DAILY_CAP", 1200.0)  # total committed in one calendar day

# ---- paths ----------------------------------------------------------------
DATA_DIR = Path(_str("NEXA_DATA", str(ROOT / "data")))
STATE_FILE = DATA_DIR / "state.json"
FIXTURES = Path(_str("NEXA_FIXTURES", str(PKG / "fixtures")))


def money(amount: float) -> str:
    """Format an amount the way every receipt and tool result prints it."""
    return f"{SYMBOL}{amount:,.2f}"
