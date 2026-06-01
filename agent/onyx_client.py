"""
onyx_client.py — Typed client for the Onyx on-chain program.

Every method maps 1-to-1 with a service method in the IDL:

  service Onyx   (mutations)
  service Query  (free reads)
  service Admin  (owner-only)

Uses scale_codec for all encoding/decoding.
Uses vara_client for the actual WebSocket calls.
"""

import logging
from typing import List, Optional

from scale_codec import (
    sails_encode, sails_strip_route,
    enc_u32, enc_u64, enc_bool, enc_str, enc_vec, enc_option, enc_actor_id,
    dec_u8, dec_u32, dec_u64, dec_bool, dec_str, dec_actor_id,
    dec_vec, dec_option, dec_intent_status,
)
from vara_client import VaraClient, VaraClientError
from models import Intent, AgentDna, RoutingResult, RankEntry

log = logging.getLogger("onyx.client.onyx")


class OnyxClient:
    """
    All calls are synchronous.  Run them inside asyncio via run_in_executor.
    """

    def __init__(self, client: VaraClient, program_id: str):
        self._c = client
        self._prog = program_id

    # ─────────────────────────────────────────────────────────────────────
    # service Onyx  (mutations — cost gas + VARA)
    # ─────────────────────────────────────────────────────────────────────

    def register_agent(self, name: str, specializations: List[str]) -> bool:
        """RegisterAgent(name, specializations) -> bool"""
        payload = sails_encode(
            "Onyx", "RegisterAgent",
            enc_str(name),
            enc_vec(specializations, enc_str),
        )
        reply = self._c.send_message_with_retry(self._prog, payload)
        # Reply payload: route_prefix + bool
        raw = self._extract_reply_payload(reply)
        if raw is None:
            log.info("RegisterAgent(%s) submitted: %s", name, reply.get("extrinsic_hash"))
            return bool(reply.get("extrinsic_hash"))
        stripped = sails_strip_route(raw)
        ok, _ = dec_bool(stripped, 0)
        log.info("RegisterAgent(%s) -> %s", name, ok)
        return ok

    def register_agent_for(
        self,
        address: str,
        name: str,
        specializations: List[str],
    ) -> bool:
        """RegisterAgentFor(address, name, specializations) -> bool"""
        payload = sails_encode(
            "Onyx", "RegisterAgentFor",
            enc_actor_id(address),
            enc_str(name),
            enc_vec(specializations, enc_str),
        )
        reply = self._c.send_message_with_retry(
            self._prog,
            payload,
            wait_for_inclusion=True,
        )
        raw = self._extract_reply_payload(reply)
        if raw is None:
            log.info("RegisterAgentFor(%s, %s) submitted: %s", address[:18] + "…", name, reply.get("extrinsic_hash"))
            return bool(reply.get("extrinsic_hash"))
        stripped = sails_strip_route(raw)
        ok, _ = dec_bool(stripped, 0)
        log.info("RegisterAgentFor(%s, %s) -> %s", address[:18] + "…", name, ok)
        return ok

    def remove_agent(self, address: str) -> bool:
        """Admin/RemoveAgent(address) -> bool"""
        payload = sails_encode(
            "Admin", "RemoveAgent",
            enc_actor_id(address),
        )
        reply = self._c.send_message_with_retry(
            self._prog,
            payload,
            wait_for_inclusion=True,
        )
        raw = self._extract_reply_payload(reply)
        if raw is None:
            log.info("RemoveAgent(%s) submitted: %s", address[:18] + "...", reply.get("extrinsic_hash"))
            return bool(reply.get("extrinsic_hash"))
        stripped = sails_strip_route(raw)
        ok, _ = dec_bool(stripped, 0)
        log.info("RemoveAgent(%s) -> %s", address[:18] + "...", ok)
        return ok

    def record_outcome(
        self, intent_id: int, success: bool, quality_score: int
    ) -> bool:
        """RecordOutcome(intent_id, success, quality_score) -> bool"""
        payload = sails_encode(
            "Onyx", "RecordOutcome",
            enc_u64(intent_id),
            enc_bool(success),
            enc_u32(quality_score),
        )
        reply = self._c.send_message_with_retry(self._prog, payload)
        raw = self._extract_reply_payload(reply)
        if raw is None:
            log.info("RecordOutcome(intent=%d) submitted: %s", intent_id, reply.get("extrinsic_hash"))
            return bool(reply.get("extrinsic_hash"))
        stripped = sails_strip_route(raw)
        ok, _ = dec_bool(stripped, 0)
        log.info("RecordOutcome(intent=%d, success=%s, quality=%d) -> %s",
                 intent_id, success, quality_score, ok)
        return ok

    def route_intent(self, intent_id: int) -> Optional[RoutingResult]:
        """RouteIntent(intent_id) -> RoutingResult"""
        payload = sails_encode("Onyx", "RouteIntent", enc_u64(intent_id))
        reply = self._c.send_message_with_retry(self._prog, payload)
        raw = self._extract_reply_payload(reply)
        if raw is None:
            return None
        stripped = sails_strip_route(raw)
        return self._decode_routing_result(stripped, 0)[0]

    def submit_and_route(
        self,
        description: str,
        tags: List[str],
        category: str,
        risk_level: int,
    ) -> Optional[RoutingResult]:
        """SubmitAndRoute(...) -> RoutingResult"""
        payload = sails_encode(
            "Onyx", "SubmitAndRoute",
            enc_str(description),
            enc_vec(tags, enc_str),
            enc_str(category),
            enc_u8_val(risk_level),
        )
        reply = self._c.send_message_with_retry(self._prog, payload)
        raw = self._extract_reply_payload(reply)
        if raw is None:
            return None
        stripped = sails_strip_route(raw)
        return self._decode_routing_result(stripped, 0)[0]

    # ─────────────────────────────────────────────────────────────────────
    # service Query  (free reads)
    # ─────────────────────────────────────────────────────────────────────

    def get_recent_intents(self, limit: int = 20) -> List[Intent]:
        """Query/GetRecentIntents(limit) -> vec Intent"""
        payload = sails_encode("Query", "GetRecentIntents", enc_u32(limit))
        raw = self._c.calculate_reply(self._prog, payload)
        stripped = sails_strip_route(raw)
        intents, _ = dec_vec(stripped, 0, self._decode_intent)
        return intents

    def get_agent_dna(self, address: str) -> Optional[AgentDna]:
        """Query/GetAgentDna(address) -> opt AgentDna"""
        payload = sails_encode("Query", "GetAgentDna", enc_actor_id(address))
        raw = self._c.calculate_reply(self._prog, payload)
        stripped = sails_strip_route(raw)
        dna, _ = dec_option(stripped, 0, self._decode_agent_dna)
        return dna

    def get_all_agents(self) -> List[str]:
        """Query/GetAllAgents() -> vec actor_id"""
        payload = sails_encode("Query", "GetAllAgents")
        raw = self._c.calculate_reply(self._prog, payload)
        stripped = sails_strip_route(raw)
        agents, _ = dec_vec(stripped, 0, dec_actor_id)
        return agents

    def get_intent_count(self) -> int:
        """Query/GetIntentCount() -> u64"""
        payload = sails_encode("Query", "GetIntentCount")
        raw = self._c.calculate_reply(self._prog, payload)
        stripped = sails_strip_route(raw)
        count, _ = dec_u64(stripped, 0)
        return count

    def get_total_routings(self) -> int:
        """Query/GetTotalRoutings() -> u64"""
        payload = sails_encode("Query", "GetTotalRoutings")
        raw = self._c.calculate_reply(self._prog, payload)
        stripped = sails_strip_route(raw)
        total, _ = dec_u64(stripped, 0)
        return total

    def get_categories(self) -> List[str]:
        """Query/GetCategories() -> vec str"""
        payload = sails_encode("Query", "GetCategories")
        raw = self._c.calculate_reply(self._prog, payload)
        stripped = sails_strip_route(raw)
        cats, _ = dec_vec(stripped, 0, dec_str)
        return cats

    def get_top_agents(self, limit: int = 10) -> List[RankEntry]:
        """Query/GetTopAgents(limit) -> vec RankEntry"""
        payload = sails_encode("Query", "GetTopAgents", enc_u32(limit))
        raw = self._c.calculate_reply(self._prog, payload)
        stripped = sails_strip_route(raw)
        entries, _ = dec_vec(stripped, 0, self._decode_rank_entry)
        return entries

    def get_rankings(self, category: str) -> List[RankEntry]:
        """Query/GetRankings(category) -> vec RankEntry"""
        payload = sails_encode("Query", "GetRankings", enc_str(category))
        raw = self._c.calculate_reply(self._prog, payload)
        stripped = sails_strip_route(raw)
        entries, _ = dec_vec(stripped, 0, self._decode_rank_entry)
        return entries

    # ─────────────────────────────────────────────────────────────────────
    # Decoders (matching IDL struct field order exactly)
    # ─────────────────────────────────────────────────────────────────────

    def _decode_intent(self, data: bytes, off: int):
        # struct Intent { id: u64, description: str, tags: vec str,
        #   category: str, risk_level: u8, submitter: actor_id,
        #   status: IntentStatus, assigned_agent: opt actor_id,
        #   submitted_at: u32, resolved_at: opt u32 }
        id_, off         = dec_u64(data, off)
        desc, off        = dec_str(data, off)
        tags, off        = dec_vec(data, off, dec_str)
        cat, off         = dec_str(data, off)
        risk, off        = dec_u8(data, off)
        subm, off        = dec_actor_id(data, off)
        status, off      = dec_intent_status(data, off)
        assigned, off    = dec_option(data, off, dec_actor_id)
        submitted_at, off = dec_u32(data, off)
        resolved_at, off  = dec_option(data, off, dec_u32)
        return Intent(
            id=id_, description=desc, tags=tags, category=cat,
            risk_level=risk, submitter=subm, status=status,
            assigned_agent=assigned,
            submitted_at=submitted_at, resolved_at=resolved_at,
        ), off

    def _decode_agent_dna(self, data: bytes, off: int):
        # struct AgentDna { name: str, reliability_score: u32, call_count: u32,
        #   success_count: u32, specializations: vec str, weighted_score: u32,
        #   mutation_count: u32, last_updated: u32 }
        name, off         = dec_str(data, off)
        reliability, off  = dec_u32(data, off)
        calls, off        = dec_u32(data, off)
        successes, off    = dec_u32(data, off)
        specs, off        = dec_vec(data, off, dec_str)
        weighted, off     = dec_u32(data, off)
        mutations, off    = dec_u32(data, off)
        updated, off      = dec_u32(data, off)
        return AgentDna(
            name=name, reliability_score=reliability,
            call_count=calls, success_count=successes,
            specializations=specs, weighted_score=weighted,
            mutation_count=mutations, last_updated=updated,
        ), off

    def _decode_routing_result(self, data: bytes, off: int):
        # struct RoutingResult { intent_id: u64, assigned_agent: actor_id,
        #   agent_name: str, agent_score: u32 }
        intent_id, off    = dec_u64(data, off)
        assigned, off     = dec_actor_id(data, off)
        agent_name, off   = dec_str(data, off)
        agent_score, off  = dec_u32(data, off)
        return RoutingResult(
            intent_id=intent_id, assigned_agent=assigned,
            agent_name=agent_name, agent_score=agent_score,
        ), off

    def _decode_rank_entry(self, data: bytes, off: int):
        # struct RankEntry { agent: actor_id, name: str, weighted_score: u32,
        #   call_count: u32, reliability_score: u32 }
        agent, off        = dec_actor_id(data, off)
        name, off         = dec_str(data, off)
        weighted, off     = dec_u32(data, off)
        calls, off        = dec_u32(data, off)
        reliability, off  = dec_u32(data, off)
        return RankEntry(
            agent=agent, name=name, weighted_score=weighted,
            call_count=calls, reliability_score=reliability,
        ), off

    # ─────────────────────────────────────────────────────────────────────
    # Reply extraction helper
    # ─────────────────────────────────────────────────────────────────────

    def _extract_reply_payload(self, receipt: dict) -> Optional[bytes]:
        """
        Walk triggered events to find the GearUserReply/GearMessage payload.
        Falls back to None (caller decides how to handle).
        """
        events = receipt.get("events") or []
        for event in events:
            try:
                attrs = event.value.get("event", {})
                module = attrs.get("module_id", "").lower()
                if module in ("gear", "gearprogram", "gearcore"):
                    params = attrs.get("params", {})
                    # Look for outgoing reply message
                    for key in ("payload", "message"):
                        if key in params:
                            msg = params[key]
                            if isinstance(msg, dict):
                                raw_payload = msg.get("payload", "")
                            else:
                                raw_payload = str(msg)
                            if raw_payload:
                                return bytes.fromhex(
                                    str(raw_payload).lstrip("0x")
                                )
            except Exception:
                continue
        log.debug("No reply payload found in events")
        return None


# Helper not imported from scale_codec to avoid circular import
def enc_u8_val(v: int) -> bytes:
    import struct
    return struct.pack("<B", v)
