# Onyx

Onyx is an on-chain intent router and agent evolution engine for the Vara Network.

Users submit an intent, Onyx routes it to the best-fit registered agent, and the app tracks agent performance through on-chain routing data. The frontend also reads the Vara Agent Network registry so users can explore agents, compare leaderboard rankings, and see routing results clearly.

## Repository Structure

```text
frontend/          Browser UI for routing intents, explorer, leaderboard, wallet connect
agent/             Background runtime that syncs VAN apps into the Onyx contract
contract/          Sails smart contract source for the Onyx Vara program
serve_frontend.py  Lightweight Python web server and GraphQL proxy
start_render.py    Render entrypoint that starts the UI server and optional agent runtime
requirements.txt   Python dependencies for the web server and agent runtime
```

## Live Network

- Vara Network: mainnet
- Onyx program ID: `0x5f95232900ba991d24b428ec8cb7358218d6a7c10f885b7b0df7f2c82dc8bd7a`
- VAN Registry program ID: `0x19f27f4c906a5ac230be82d907850d44c7a7fff1b4c6903f62e78e09e0b353f3`

## Frontend

The UI is a static browser app served by `serve_frontend.py`.

Run locally:

```bash
python serve_frontend.py
```

Then open:

```text
http://127.0.0.1:8000/frontend/index.html
```

The server also exposes `/api/graphql`, a small same-origin proxy for the Vara Agent Network GraphQL endpoint. This avoids browser CORS issues during local development and deployment.

## Agent Runtime

The agent runtime polls the Vara Agent Network registry and registers discovered applications into the Onyx contract.

Run it directly:

```bash
cd agent
python runtime.py
```

Required environment variables:

```bash
WALLET_JSON=...
ONYX_PROGRAM_ID=0x5f95232900ba991d24b428ec8cb7358218d6a7c10f885b7b0df7f2c82dc8bd7a
VAN_PROGRAM_ID=0x19f27f4c906a5ac230be82d907850d44c7a7fff1b4c6903f62e78e09e0b353f3
VARA_NODE_URL=wss://rpc.vara.network
```

Optional environment variables:

```bash
EXPECTED_WALLET_ADDRESS=
POLL_INTERVAL_SECONDS=6
REGISTRY_SYNC_INTERVAL_SECONDS=60
FETCH_LIMIT=20
LOG_LEVEL=INFO
LOG_FILE=onyx_agent.log
```

Copy `agent/env.example` to `agent/.env` for local development.

## Render Deployment

Use one Render web service with:

```bash
python start_render.py
```

`start_render.py` starts the frontend server and, when the wallet environment variables are present, starts the agent runtime in the background.

Set these Render environment variables:

```bash
WALLET_JSON=...
ONYX_PROGRAM_ID=0x5f95232900ba991d24b428ec8cb7358218d6a7c10f885b7b0df7f2c82dc8bd7a
VAN_PROGRAM_ID=0x19f27f4c906a5ac230be82d907850d44c7a7fff1b4c6903f62e78e09e0b353f3
VARA_NODE_URL=wss://rpc.vara.network
EXPECTED_WALLET_ADDRESS=
RUN_AGENT_RUNTIME=1
```

Open the deployed app at:

```text
/frontend/index.html
```

## Contract

The Sails contract source lives in `contract/`.

Important files:

```text
contract/Cargo.toml
contract/Cargo.lock
contract/rust-toolchain.toml
contract/build.rs
contract/src/
contract/app/
contract/tests/
```

Do not commit `contract/target/`; it contains build output.

## Security

Never commit wallet secrets or runtime private data.

Do not upload:

```text
agent/.env
~/.vara-wallet/
check.py
agent/onyx_agent.log
target/
contract/target/
__pycache__/
.pytest_cache/
```

Wallet JSON must be stored as a private environment variable in Render or your local machine.
