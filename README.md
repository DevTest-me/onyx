# Onyx

Onyx is an on-chain intent router and agent evolution engine for the Vara Network.

It explores a simple idea: users should be able to describe what they want, and an on-chain system should route that intent to the best-fit registered agent. Each routing event can then feed back into agent scoring, discovery, and reputation.

This repository is published as a reference implementation for builders who want to study the architecture and build similar systems in the future.

## What Onyx Does

- Registers Vara Agent Network applications into an Onyx routing contract
- Lets users submit intents from a browser interface
- Matches intents to agents by category, specialization, risk level, and score
- Displays an explorer and leaderboard for registered agents
- Tracks routing activity through the Onyx program

## Architecture

```text
frontend/          Browser interface for intent routing and agent discovery
agent/             Reference runtime for syncing registry applications
contract/          Sails smart contract source for the Onyx routing program
serve_frontend.py  Minimal frontend server and registry proxy
start_render.py    Combined service entrypoint used by the hosted demo
```

## Network Context

- Network: Vara mainnet
- Onyx program ID: `0x5f95232900ba991d24b428ec8cb7358218d6a7c10f885b7b0df7f2c82dc8bd7a`
- Vara Agent Network program ID: `0x19f27f4c906a5ac230be82d907850d44c7a7fff1b4c6903f62e78e09e0b353f3`

## Repository Notes

The code is organized as a public reference. It is not a turnkey hosted service package and does not include private wallet material, deployment secrets, or operator credentials.

Sensitive runtime files are intentionally excluded from the repository:

```text
agent/.env
~/.vara-wallet/
agent/onyx_agent.log
target/
contract/target/
__pycache__/
.pytest_cache/
```

## Status

Onyx is an vara application focused on agent discovery, intent routing, and on-chain coordination patterns.
