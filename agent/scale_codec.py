"""
scale_codec.py — Minimal SCALE encoder/decoder for Onyx runtime.

Covers every type used by the Onyx IDL and the VAN Registry IDL:
  primitives : u8, u32, u64, bool, str, actor_id (32-byte AccountId)
  composites : struct, vec<T>, option<T>
  enums      : IntentStatus, Track (index-tagged)
"""

import struct
from typing import Any, Callable, List, Optional, Tuple


# ── Compact integer ────────────────────────────────────────────────────────────

def enc_compact(n: int) -> bytes:
    if n < 0:
        raise ValueError(f"compact requires n>=0, got {n}")
    if n < 64:
        return bytes([n << 2])
    if n < 16384:
        return struct.pack("<H", (n << 2) | 1)
    if n < 1_073_741_824:
        return struct.pack("<I", (n << 2) | 2)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "little")
    return bytes([(len(raw) - 4) << 2 | 3]) + raw


def dec_compact(data: bytes, off: int) -> Tuple[int, int]:
    first = data[off]
    mode = first & 3
    if mode == 0:
        return first >> 2, off + 1
    if mode == 1:
        return struct.unpack_from("<H", data, off)[0] >> 2, off + 2
    if mode == 2:
        return struct.unpack_from("<I", data, off)[0] >> 2, off + 4
    # big-int mode
    extra = (first >> 2) + 4
    val = int.from_bytes(data[off + 1 : off + 1 + extra], "little")
    return val, off + 1 + extra


# ── Primitives ─────────────────────────────────────────────────────────────────

def enc_u8(v: int) -> bytes:     return struct.pack("<B", v)
def enc_u32(v: int) -> bytes:    return struct.pack("<I", v)
def enc_u64(v: int) -> bytes:    return struct.pack("<Q", v)
def enc_bool(v: bool) -> bytes:  return bytes([1 if v else 0])

def enc_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return enc_compact(len(b)) + b

def enc_actor_id(hex_str: str) -> bytes:
    h = hex_str.lstrip("0x")
    return bytes.fromhex(h.zfill(64))


def dec_u8(data: bytes, off: int) -> Tuple[int, int]:
    return data[off], off + 1

def dec_u32(data: bytes, off: int) -> Tuple[int, int]:
    return struct.unpack_from("<I", data, off)[0], off + 4

def dec_u64(data: bytes, off: int) -> Tuple[int, int]:
    return struct.unpack_from("<Q", data, off)[0], off + 8

def dec_bool(data: bytes, off: int) -> Tuple[bool, int]:
    return bool(data[off]), off + 1

def dec_str(data: bytes, off: int) -> Tuple[str, int]:
    length, off = dec_compact(data, off)
    return data[off : off + length].decode("utf-8"), off + length

def dec_actor_id(data: bytes, off: int) -> Tuple[str, int]:
    return "0x" + data[off : off + 32].hex(), off + 32


# ── Composite ──────────────────────────────────────────────────────────────────

def enc_vec(items: List[Any], enc_item: Callable[[Any], bytes]) -> bytes:
    out = enc_compact(len(items))
    for item in items:
        out += enc_item(item)
    return out


def enc_option(val: Optional[Any], enc_inner: Callable[[Any], bytes]) -> bytes:
    if val is None:
        return bytes([0])
    return bytes([1]) + enc_inner(val)


def dec_vec(
    data: bytes, off: int, dec_item: Callable[[bytes, int], Tuple[Any, int]]
) -> Tuple[List[Any], int]:
    count, off = dec_compact(data, off)
    items = []
    for _ in range(count):
        item, off = dec_item(data, off)
        items.append(item)
    return items, off


def dec_option(
    data: bytes, off: int, dec_inner: Callable[[bytes, int], Tuple[Any, int]]
) -> Tuple[Optional[Any], int]:
    present = data[off]
    off += 1
    if present == 0:
        return None, off
    return dec_inner(data, off)


# ── Enums ──────────────────────────────────────────────────────────────────────

INTENT_STATUS = {0: "Pending", 1: "Routed", 2: "Completed", 3: "Failed"}
TRACK_IDX     = {"Services": 0, "Social": 1, "Economy": 2, "Open": 3}
TRACK_NAME    = {v: k for k, v in TRACK_IDX.items()}


def dec_intent_status(data: bytes, off: int) -> Tuple[str, int]:
    idx, off = dec_u8(data, off)
    return INTENT_STATUS.get(idx, f"Unknown({idx})"), off


def dec_track(data: bytes, off: int) -> Tuple[str, int]:
    idx, off = dec_u8(data, off)
    return TRACK_NAME.get(idx, f"Unknown({idx})"), off


# ── Sails route prefix ─────────────────────────────────────────────────────────

def sails_encode(service: str, method: str, *arg_bytes: bytes) -> bytes:
    """
    Builds a Sails call payload:
        SCALE(service_name) + SCALE(method_name) + concat(arg_bytes)
    """
    payload = enc_str(service) + enc_str(method)
    for ab in arg_bytes:
        payload += ab
    return payload


def sails_strip_route(data: bytes) -> bytes:
    """
    Sails reply payloads echo the route prefix back.
    Strip it so we can decode the bare return value.
    """
    off = 0
    _, off = dec_str(data, off)   # service name
    _, off = dec_str(data, off)   # method name
    return data[off:]
