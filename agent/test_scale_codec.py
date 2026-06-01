"""
Tests for scale_codec.py — roundtrip encode/decode for every type used by Onyx.
Run: pytest tests/ -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import struct
from scale_codec import (
    enc_compact, dec_compact,
    enc_u8, dec_u8,
    enc_u32, dec_u32,
    enc_u64, dec_u64,
    enc_bool, dec_bool,
    enc_str, dec_str,
    enc_actor_id, dec_actor_id,
    enc_vec, dec_vec,
    enc_option, dec_option,
    sails_encode, sails_strip_route,
    INTENT_STATUS, TRACK_NAME,
    dec_intent_status, dec_track,
)


# ── Compact ───────────────────────────────────────────────────────────────────

def test_compact_small():
    for n in [0, 1, 5, 20, 63]:
        enc = enc_compact(n)
        val, off = dec_compact(enc, 0)
        assert val == n, f"compact({n}) failed"
        assert off == len(enc)

def test_compact_medium():
    for n in [64, 100, 1000, 16383]:
        enc = enc_compact(n)
        val, _ = dec_compact(enc, 0)
        assert val == n

def test_compact_large():
    n = 1_073_741_823
    enc = enc_compact(n)
    val, _ = dec_compact(enc, 0)
    assert val == n


# ── Primitives ────────────────────────────────────────────────────────────────

def test_u8_roundtrip():
    for v in [0, 1, 127, 255]:
        b = enc_u8(v)
        out, _ = dec_u8(b, 0)
        assert out == v

def test_u32_roundtrip():
    for v in [0, 42, 2**32 - 1]:
        b = enc_u32(v)
        out, _ = dec_u32(b, 0)
        assert out == v

def test_u64_roundtrip():
    for v in [0, 42, 2**64 - 1]:
        b = enc_u64(v)
        out, _ = dec_u64(b, 0)
        assert out == v

def test_bool_roundtrip():
    assert dec_bool(enc_bool(True),  0) == (True, 1)
    assert dec_bool(enc_bool(False), 0) == (False, 1)

def test_str_roundtrip():
    for s in ["", "hello", "finance", "Onyx-Python-Agent", "a" * 200]:
        b = enc_str(s)
        out, _ = dec_str(b, 0)
        assert out == s

def test_actor_id_roundtrip():
    addr = "0x9c2cfce724dcea96453eb7ef9c40ef67b572744374e6bc4195e030619ab02c33"
    b = enc_actor_id(addr)
    assert len(b) == 32
    out, _ = dec_actor_id(b, 0)
    assert out == addr


# ── Composites ────────────────────────────────────────────────────────────────

def test_vec_str_roundtrip():
    items = ["finance", "risk", "general"]
    b = enc_vec(items, enc_str)
    out, _ = dec_vec(b, 0, dec_str)
    assert out == items

def test_vec_empty():
    b = enc_vec([], enc_str)
    out, _ = dec_vec(b, 0, dec_str)
    assert out == []

def test_option_some():
    b = enc_option("hello", enc_str)
    out, _ = dec_option(b, 0, dec_str)
    assert out == "hello"

def test_option_none():
    b = enc_option(None, enc_str)
    out, _ = dec_option(b, 0, dec_str)
    assert out is None


# ── Enums ─────────────────────────────────────────────────────────────────────

def test_intent_status():
    for idx, name in INTENT_STATUS.items():
        data = bytes([idx])
        out, _ = dec_intent_status(data, 0)
        assert out == name

def test_track():
    for name, idx in {"Services": 0, "Social": 1, "Economy": 2, "Open": 3}.items():
        data = bytes([idx])
        out, _ = dec_track(data, 0)
        assert out == name


# ── Sails route ───────────────────────────────────────────────────────────────

def test_sails_strip_route():
    payload = sails_encode("Query", "GetRecentIntents", enc_u32(20))
    # Simulate a reply: route prefix + bool result
    reply_with_prefix = payload + enc_bool(True)
    stripped = sails_strip_route(reply_with_prefix)
    # Stripped should just be the bool byte
    result, _ = dec_bool(stripped, 0)
    assert result is True

def test_sails_encode_register_agent():
    payload = sails_encode(
        "Onyx", "RegisterAgent",
        enc_str("my-agent"),
        enc_vec(["finance", "risk"], enc_str),
    )
    # Should decode service="Onyx", method="RegisterAgent"
    off = 0
    svc, off = dec_str(payload, off)
    method, off = dec_str(payload, off)
    name, off = dec_str(payload, off)
    specs, off = dec_vec(payload, off, dec_str)
    assert svc == "Onyx"
    assert method == "RegisterAgent"
    assert name == "my-agent"
    assert specs == ["finance", "risk"]

def test_sails_record_outcome():
    payload = sails_encode(
        "Onyx", "RecordOutcome",
        enc_u64(99),
        enc_bool(True),
        enc_u32(85),
    )
    off = 0
    svc, off = dec_str(payload, off)
    meth, off = dec_str(payload, off)
    intent_id, off = dec_u64(payload, off)
    success, off = dec_bool(payload, off)
    quality, off = dec_u32(payload, off)
    assert svc == "Onyx"
    assert meth == "RecordOutcome"
    assert intent_id == 99
    assert success is True
    assert quality == 85
