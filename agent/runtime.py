"""
runtime.py — Onyx Agent Runtime
================================
Startup:
  1. Connect to wss://rpc.vara.network
  2. Fetch all apps from VAN Registry (Registry/Discover)
  3. Register each one in Onyx (Onyx/RegisterAgent)

Main loop (every 6 s):
  1. Query/GetRecentIntents(20) from Onyx
  2. Find any with status == "Routed"
  3. For each routed intent — forward call to assigned agent's contract
  4. Onyx/RecordOutcome(intent_id, success, quality)

Background (every 60 s):
  Re-sync VAN Registry → auto-register any new apps.

Usage:
    python runtime.py
"""

import asyncio
import logging
import signal
import sys
import time
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Set

from config import load_config
from models import Application, Intent
from onyx_client import OnyxClient
from van_client import VanClient
from vara_client import VaraClient, VaraClientError

# ── Logging setup ─────────────────────────────────────────────────────────────
def setup_logging(level: str, log_file: str) -> None:
    fmt = "%(asctime)s  %(levelname)-8s  %(name)-22s  %(message)s"
    handlers = [
        logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')),
        logging.FileHandler(log_file, encoding='utf-8'),
    ]
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)

log = logging.getLogger("onyx.runtime")

# ── Thread pool (substrate-interface is sync) ─────────────────────────────────
EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="onyx-worker")


async def run_sync(fn, *args):
    """Run a blocking call in the thread pool without blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, fn, *args)


# ── Registration helpers ───────────────────────────────────────────────────────

def _track_to_specialization(track: str) -> list:
    """Map VAN Track enum value to a sensible Onyx specializations list."""
    mapping = {
        "Services": ["services", "api", "integration"],
        "Social":   ["social", "community", "chat"],
        "Economy":  ["economy", "finance", "defi", "treasury"],
        "Open":     ["general", "open"],
    }
    return mapping.get(track, ["general"])


def _registration_key(app: Application) -> str:
    return app.program_id.strip().lower()


async def register_app(
    app: Application,
    onyx: OnyxClient,
    registered: Set[str],
) -> bool:
    """
    Register a VAN app in Onyx.  Idempotent: skips already-registered ones.
    Returns True if registered (or already was), False on error.
    """
    key = _registration_key(app)
    if not key:
        log.warning("Skipping app with empty handle: %s", app.program_id[:18] + "…")
        return False

    if key in registered:
        return True

    specs = _track_to_specialization(app.track)
    log.info(
        "Registering %-32s  handle=%-20s  track=%s  specs=%s",
        app.program_id[:18] + "…",
        app.handle,
        app.track,
        specs,
    )

    try:
        ok = await run_sync(onyx.register_agent_for, app.program_id, app.handle, specs)
        if ok:
            registered.add(key)
            log.info("  ✓ Registered: %s", app.handle)
        else:
            # RegisterAgent returns false when already registered — treat as success
            registered.add(key)
            log.debug("  ↩ Already registered (or returned false): %s", app.handle)
        return True
    except VaraClientError as exc:
        log.error("  ✗ Registration failed for %s: %s", app.handle, exc)
        return False


async def sync_van_registry(van, onyx, registered):
    log.info("Syncing VAN Registry via GraphQL...")
    try:
        apps = await run_sync(van.discover_all)
    except Exception as exc:
        log.error("VAN Registry sync failed: %s", exc)
        return 0
    
    log.info("VAN Registry returned %d apps", len(apps))
    new_count = 0
    for app in apps:
        if _registration_key(app) not in registered:
            ok = await register_app(app, onyx, registered)
            if ok:
                new_count += 1
            await asyncio.sleep(0.5)
    
    if new_count:
        log.info("Registered %d new apps", new_count)
    else:
        log.info("No new apps to register")
    return new_count


# ── Intent execution ──────────────────────────────────────────────────────────

async def execute_intent(
    intent: Intent,
    van_client: VaraClient,
) -> tuple[bool, int]:
    """
    Try to call the assigned agent's contract.
    Returns (success: bool, quality_score: int 0-100).

    In this runtime the 'execution' is a fire-and-forget message to the
    assigned agent's program_id.  The quality score is heuristic:
      - We send a minimal ping payload and treat any non-error as success.
      - Quality is derived from intent risk_level (lower risk → higher confidence).
    """
    if not intent.assigned_agent:
        log.warning("Intent #%d has no assigned_agent — cannot execute", intent.id)
        return False, 0

    # Quality heuristic: start at 85, reduce for higher risk
    base_quality = max(40, 85 - (intent.risk_level * 10))

    # Build a minimal Sails ping to the assigned agent
    # We use SubmitIntent-style payload — the agent's contract may not know us,
    # but a valid Gear message getting accepted = success signal.
    from scale_codec import sails_encode, enc_str, enc_vec, enc_u8 as _u8
    import struct

    ping_payload = sails_encode(
        "Onyx", "SubmitIntent",
        enc_str(intent.description[:128]),
        enc_vec(intent.tags, enc_str),
        enc_str(intent.category),
        struct.pack("<B", intent.risk_level),
    )

    try:
        receipt = await run_sync(
            van_client.send_message,
            intent.assigned_agent,
            ping_payload,
            10_000_000_000,   # lower gas for the forward call
            0,
        )
        log.info(
            "Intent #%d forwarded to %s  block=%s",
            intent.id,
            intent.assigned_agent[:18] + "…",
            receipt.get("block_hash", "?")[:16] + "…",
        )
        return True, base_quality
    except VaraClientError as exc:
        log.warning(
            "Intent #%d forward to %s failed: %s",
            intent.id, intent.assigned_agent[:18] + "…", exc,
        )
        # A failed forward is still a valid routing attempt — report partial quality
        return False, max(0, base_quality - 30)


# ── Main loop ─────────────────────────────────────────────────────────────────

async def main_loop() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level, cfg.log_file)

    loop = asyncio.get_running_loop()
    intent_queue = asyncio.Queue()

    log.info("=" * 48)
    log.info("      ONYX AGENT RUNTIME  v1.0.0")
    log.info("=" * 48)
    log.info("Node    : %s", cfg.vara_node_url)
    log.info("Onyx    : %s", cfg.onyx_program_id[:20] + "…")
    log.info("VAN     : %s", cfg.van_program_id[:20] + "…")

    # ── Build clients ─────────────────────────────────────────────────────
    vara = VaraClient(node_url=cfg.vara_node_url, wallet_json=cfg.wallet_json)
    try:
        await run_sync(vara.connect)
    except VaraClientError as exc:
        log.critical("Cannot connect to Vara node: %s", exc)
        sys.exit(1)

    log.info("Wallet  : %s", vara.address)

    onyx = OnyxClient(vara, cfg.onyx_program_id)
    van  = VanClient()

    # ── State ─────────────────────────────────────────────────────────────
    registered_apps: Set[str] = set()
    processed_intents: Set[int] = set()
    running = True
    last_registry_sync = 0.0

    # ── Graceful shutdown ─────────────────────────────────────────────────
    def _stop(sig, _frame):
        nonlocal running
        log.info("Signal %s received — shutting down…", signal.Signals(sig).name)
        running = False

    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _stop)

    # ── Startup: hydrate known Onyx registrations, then sync VAN apps ──────
    try:
        registered_apps = {addr.lower() for addr in await run_sync(onyx.get_all_agents)}
        log.info("Onyx already has %d registered app program IDs", len(registered_apps))
    except Exception as exc:
        log.warning("Could not read existing Onyx registrations: %s", exc)

    await sync_van_registry(van, onyx, registered_apps)
    last_registry_sync = time.monotonic()

    def _on_message(payload_hex):
         loop.call_soon_threadsafe(intent_queue.put_nowait, payload_hex)

    subscription_thread = threading.Thread(
        target=vara.subscribe_user_messages,
        args=(cfg.onyx_program_id, _on_message),
        daemon=True,
    )
    subscription_thread.start()
    log.info("Subscribed to Onyx messages")

    # ── Stats counters ────────────────────────────────────────────────────
    total_processed = 0
    total_success   = 0
    start_time      = time.monotonic()

    log.info("Entering main loop (poll every %.0fs, registry sync every %.0fs)",
             cfg.poll_interval_seconds, cfg.registry_sync_interval_seconds)

    cycle = 0
    while running:
        cycle_start = time.monotonic()

        if cycle_start - last_registry_sync >= cfg.registry_sync_interval_seconds:
            await sync_van_registry(van, onyx, registered_apps)
            last_registry_sync = time.monotonic()

        while not intent_queue.empty():
            payload_hex = await intent_queue.get()
            log.info("Incoming message: %s", payload_hex[:60] + "...")

        cycle += 1
        if cycle % 10 == 0:
            uptime = int(time.monotonic() - start_time)
            h, rem = divmod(uptime, 3600); m, s = divmod(rem, 60)
            log.info("=== Stats  apps_registered=%d  uptime=%02d:%02d:%02d",
                     len(registered_apps), h, m, s)

        await asyncio.sleep(cfg.poll_interval_seconds)

    # ── Shutdown ──────────────────────────────────────────────────────────
    await run_sync(vara.close)
    EXECUTOR.shutdown(wait=False)
    log.info(
        "Runtime stopped. Processed %d intents (%d succeeded). Apps registered: %d.",
        total_processed, total_success, len(registered_apps),
    )


if __name__ == "__main__":
    asyncio.run(main_loop())
