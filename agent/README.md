# Onyx Agent Runtime

Off-chain Python watcher that bridges the VAN Registry with the Onyx on-chain
routing engine.

```
onyx/agent/
├── runtime.py          ← entry point — run this
├── vara_client.py      ← low-level Vara WebSocket client
├── onyx_client.py      ← typed client for the Onyx program (IDL-matched)
├── van_client.py       ← typed client for the VAN Registry (IDL-matched)
├── scale_codec.py      ← hand-written SCALE encoder/decoder (no sails-py needed)
├── models.py           ← dataclasses matching both IDLs exactly
├── config.py           ← loads from .env
├── tests/
│   └── test_scale_codec.py   ← 18 roundtrip tests, all green
├── .env.example
└── requirements.txt
```

## Quick start

```bash
cd onyx/agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — fill in AGENT_MNEMONIC

python runtime.py
```

## What happens on startup

1. Connects to `wss://rpc.vara.network`
2. Calls `Registry/Discover` on the VAN contract (paginated) → gets all apps
3. For each app: calls `Onyx/RegisterAgent(handle, [track_specs])`
4. Enters the main loop

## Main loop (every 6 s)

1. `Query/GetRecentIntents(20)` — fetch latest intents from Onyx
2. Filter for `status == "Routed"` and not yet processed
3. For each routed intent: forward a Gear message to the assigned agent's contract
4. `Onyx/RecordOutcome(intent_id, success, quality)` — update DNA scores

## Background (every 60 s)

Re-queries VAN Registry and auto-registers any new apps added since startup.

## Running tests

```bash
pytest tests/ -v
# 18 passed — SCALE codec roundtrips for every IDL type
```

## .env reference

| Variable | Default | Required |
|---|---|---|
| `VARA_NODE_URL` | `wss://rpc.vara.network` | ✓ |
| `ONYX_PROGRAM_ID` | — | ✓ |
| `VAN_PROGRAM_ID` | — | ✓ |
| `AGENT_MNEMONIC` | — | ✓ |
| `POLL_INTERVAL_SECONDS` | `6` | |
| `REGISTRY_SYNC_INTERVAL_SECONDS` | `60` | |
| `FETCH_LIMIT` | `20` | |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FILE` | `onyx_agent.log` | |

## Track → specialization mapping

| VAN Track | Onyx specializations |
|---|---|
| Services | `services`, `api`, `integration` |
| Social | `social`, `community`, `chat` |
| Economy | `economy`, `finance`, `defi`, `treasury` |
| Open | `general`, `open` |
