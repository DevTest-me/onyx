"""
config.py — Load all configuration from environment / .env file.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Any

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── Vara ───────────────────────────────────────────────────────────────
    vara_node_url: str       = ""
    onyx_program_id: str     = ""
    van_program_id: str      = ""
    wallet_json: Dict[str, Any] = field(default_factory=dict)

    # ── Timing ────────────────────────────────────────────────────────────
    poll_interval_seconds: float           = 6.0
    registry_sync_interval_seconds: float  = 60.0
    fetch_limit: int                       = 20

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_file: str  = "onyx_agent.log"


def load_config() -> Config:
    def _req(key: str) -> str:
        val = os.getenv(key, "").strip()
        if not val:
            raise RuntimeError(
                f"Missing required env var: {key}\n"
                f"Copy .env.example to .env and fill it in."
            )
        return val

    def _opt(key: str, default: str = "") -> str:
        return os.getenv(key, default).strip()

    def _float(key: str, default: float) -> float:
        try:
            return float(os.getenv(key, str(default)))
        except ValueError:
            return default

    def _int(key: str, default: int) -> int:
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            return default

    wallet_json = json.loads(_req("WALLET_JSON"))

    return Config(
        vara_node_url            = _opt("VARA_NODE_URL", "wss://rpc.vara.network"),
        onyx_program_id          = _req("ONYX_PROGRAM_ID"),
        van_program_id           = _req("VAN_PROGRAM_ID"),
        wallet_json              = wallet_json,
        poll_interval_seconds    = _float("POLL_INTERVAL_SECONDS", 6.0),
        registry_sync_interval_seconds = _float("REGISTRY_SYNC_INTERVAL_SECONDS", 60.0),
        fetch_limit              = _int("FETCH_LIMIT", 20),
        log_level                = _opt("LOG_LEVEL", "INFO"),
        log_file                 = _opt("LOG_FILE", "onyx_agent.log"),
    )