"""
models.py — Pure dataclasses matching both IDL schemas exactly.
No encoding logic lives here.
"""

from dataclasses import dataclass, field
from typing import List, Optional


# ── VAN Registry ──────────────────────────────────────────────────────────────

@dataclass
class Application:
    program_id: str          # actor_id as 0x-prefixed hex
    owner: str
    handle: str
    description: str
    track: str               # "Services" | "Social" | "Economy" | "Open"
    github_url: str
    skills_url: str
    idl_url: str
    registered_at: int
    season_id: int
    status: str              # "Building" | "Live" | "Submitted" | "Finalist" | "Winner"


# ── Onyx ──────────────────────────────────────────────────────────────────────

@dataclass
class Intent:
    id: int
    description: str
    tags: List[str]
    category: str
    risk_level: int          # u8
    submitter: str           # actor_id
    status: str              # "Pending" | "Routed" | "Completed" | "Failed"
    assigned_agent: Optional[str]
    submitted_at: int
    resolved_at: Optional[int]


@dataclass
class AgentDna:
    name: str
    reliability_score: int
    call_count: int
    success_count: int
    specializations: List[str]
    weighted_score: int
    mutation_count: int
    last_updated: int


@dataclass
class RoutingResult:
    intent_id: int
    assigned_agent: str
    agent_name: str
    agent_score: int


@dataclass
class RankEntry:
    agent: str
    name: str
    weighted_score: int
    call_count: int
    reliability_score: int
