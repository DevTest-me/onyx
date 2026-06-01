"""
vara_client.py — Low-level Vara node client.

Wraps substrate-interface to:
  • send signed Gear messages  (mutations)
  • call gear_calculateReplyForHandle RPC  (free queries)

All methods are sync (substrate-interface is sync).
The runtime calls these from asyncio via run_in_executor.
"""

import logging
import time
import base64
import threading
import os
from typing import Any, Dict, Optional

from substrateinterface import SubstrateInterface, Keypair
from sr25519 import pair_from_ed25519_secret_key

# VOUCHER_ID = "0x38d5f40c0fa6fbd962eff9670e1fbdcb8f77e950c31249c01636c26a2185fb73"

log = logging.getLogger("onyx.vara")

GAS_MUTATION = 50_000_000_000
GAS_QUERY    = 0


class VaraClientError(Exception):
    pass


class VaraClient:

    def __init__(self, node_url: str, wallet_json: Dict[str, Any]):
        self.node_url    = node_url
        self._wallet_json = wallet_json
        self._substrate: Optional[SubstrateInterface] = None
        self._keypair:   Optional[Keypair]            = None
        self._next_nonce: Optional[int]                = None
        self._nonce_lock = threading.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        log.info("Connecting to Vara node: %s", self.node_url)
        try:
            self._substrate = SubstrateInterface(url=self.node_url)
            encoded = base64.b64decode(self._wallet_json['encoded'])
            raw_secret = encoded[16:80]
            public_key = encoded[85:117]
            converted_public_key, private_key = pair_from_ed25519_secret_key(raw_secret)
            if converted_public_key != public_key:
                raise VaraClientError("Wallet public key does not match converted private key")
            self._keypair = Keypair(
                private_key=private_key,
                public_key=public_key,
                crypto_type=1,
                ss58_format=137
            )
            expected = os.getenv("EXPECTED_WALLET_ADDRESS", "").strip()
            if expected and self._keypair.ss58_address != expected:
                raise VaraClientError(f"Address mismatch: {self._keypair.ss58_address}")

            log.info("Wallet address: %s", self._keypair.ss58_address)
            self._next_nonce = self._substrate.get_account_nonce(self._keypair.ss58_address) or 0
            log.info("Wallet nonce: %d", self._next_nonce)
        except Exception as exc:
            raise VaraClientError(f"Failed to connect: {exc}") from exc

    def refresh_nonce(self) -> int:
        if not self._substrate or not self._keypair:
            raise VaraClientError("Not connected or no keypair configured.")
        with self._nonce_lock:
            self._next_nonce = self._substrate.get_account_nonce(self._keypair.ss58_address) or 0
            log.info("Wallet nonce refreshed: %d", self._next_nonce)
            return self._next_nonce

    def close(self) -> None:
        if self._substrate:
            try:
                self._substrate.close()
            except Exception:
                pass
            self._substrate = None

    @property
    def address(self) -> Optional[str]:
        return self._keypair.ss58_address if self._keypair else None

    @property
    def address_hex(self) -> Optional[str]:
        if not self._keypair:
            return None
        return "0x" + self._keypair.public_key.hex()

    # ── Mutation: gear.sendMessage ─────────────────────────────────────────

    def send_message(
        self,
        destination: str,
        payload: bytes,
        gas_limit: int = GAS_MUTATION,
        value: int = 0,
        wait_for_inclusion: bool = False,
    ) -> dict:
        if not self._substrate or not self._keypair:
            raise VaraClientError("Not connected or no keypair configured.")

        dest        = "0x" + destination.lstrip("0x")
        payload_hex = "0x" + payload.hex()

        log.debug("send_message  dest=%s  payload_len=%d", dest[:14] + "…", len(payload))

        try:
            log.debug("send_message composing  dest=%s  payload_len=%d", dest[:14] + "...", len(payload))
            call = self._substrate.compose_call(
               call_module="Gear",
               call_function="send_message",
               call_params={
                   "destination": dest,
                   "payload":     payload_hex,
                   "gas_limit":   gas_limit,
                   "value":       value,
                   "keep_alive":  False,
                },
            )
            with self._nonce_lock:
                if self._next_nonce is None:
                    self._next_nonce = self._substrate.get_account_nonce(self._keypair.ss58_address) or 0
                nonce = self._next_nonce
                log.debug("send_message signing  nonce=%d", nonce)
                extrinsic = self._substrate.create_signed_extrinsic(
                    call=call,
                    keypair=self._keypair,
                    era=None,
                    nonce=nonce,
                )
                log.debug("send_message submitting wait_for_inclusion=%s", wait_for_inclusion)
                receipt = self._substrate.submit_extrinsic(
                    extrinsic, wait_for_inclusion=wait_for_inclusion
                )
                self._next_nonce = nonce + 1
            log.info("send_message submitted  extrinsic=%s", receipt.extrinsic_hash)
        except Exception as exc:
            raise VaraClientError(f"send_message failed: {exc}") from exc

        if receipt.block_hash and not receipt.is_success:
            raise VaraClientError(f"Extrinsic reverted: {receipt.error_message}")

        log.debug("send_message submitted  extrinsic=%s", receipt.extrinsic_hash)
        return {
            "block_hash":      receipt.block_hash,
            "extrinsic_hash":  receipt.extrinsic_hash,
            "events":          receipt.triggered_events if receipt.block_hash else [],
        }

    # ── Query: gear_calculateReplyForHandle ────────────────────────────────

    def calculate_reply(
        self,
        destination: str,
        payload: bytes,
        origin: Optional[str] = None,
    ) -> bytes:
       if not self._substrate:
          raise VaraClientError("Not connected.")

       dest_hex    = "0x" + destination.lstrip("0x")
       payload_hex = "0x" + payload.hex()

       log.debug("calculate_reply  dest=%s  payload_len=%d", dest_hex[:14] + "...", len(payload))

       print("PAYLOAD HEX:", payload_hex)
       print("DEST:", dest_hex)
       origin_hex = origin or self.address_hex
       if not origin_hex:
           raise VaraClientError("calculate_reply requires an origin address")

       try:
           result = self._substrate.rpc_request(
               method="gear_calculateReplyForHandle",
               params=[origin_hex, dest_hex, payload_hex, GAS_MUTATION, 0, None],
            )
       except Exception as exc:
           raise VaraClientError(f"calculate_reply RPC failed: {exc}") from exc
       
       import json
       print("RAW RPC RESULT:", json.dumps(result, indent=2)[:500])

       if "error" in result:
          raise VaraClientError(f"calculate_reply error: {result['error']}")

       raw = result.get("result", {})
       payload_out = raw if isinstance(raw, str) else raw.get("payload", "") if isinstance(raw, dict) else ""

       if not payload_out or payload_out == "0x":
           raise VaraClientError("calculate_reply returned empty payload")

       return bytes.fromhex(payload_out.lstrip("0x"))
    

    def subscribe_user_messages(self, program_id: str, callback):
        """
        Subscribe to outgoing messages from a program.
        Calls callback(payload_hex) for each message sent to our address.
        Runs forever — call in a separate thread.
        """
        if not self._substrate:
            raise VaraClientError("Not connected.")

        dest_hex = "0x" + program_id.lstrip("0x")

        def _handler(obj, update_nr, subscription_id):
            try:
               msg = obj.get("params", {}).get("result", {})
               destination = msg.get("destination", "")
               if destination == self.address_hex:
                  payload = msg.get("payload", "")
                  if payload and payload != "0x":
                      callback(payload)
            except Exception as exc:
                log.warning("subscribe_user_messages handler error: %s", exc)

        self._substrate.rpc_request(
            method="gear_subscribeUserMessageSent",
            params=[{"program_id": dest_hex}],
            result_handler=_handler,
        )

    # ── Retry wrapper ──────────────────────────────────────────────────────

    def send_message_with_retry(
        self,
        destination: str,
        payload: bytes,
        retries: int = 3,
        delay: float = 2.0,
        **kwargs,
    ) -> dict:
        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                return self.send_message(destination, payload, **kwargs)
            except VaraClientError as exc:
                last_exc = exc
                log.warning("send_message attempt %d/%d failed: %s", attempt, retries, exc)
                if attempt < retries:
                    if _looks_like_nonce_error(exc):
                        try:
                            self.refresh_nonce()
                        except VaraClientError as refresh_exc:
                            log.warning("nonce refresh failed: %s", refresh_exc)
                    time.sleep(delay)
        raise VaraClientError(f"All {retries} retries failed: {last_exc}") from last_exc


def _looks_like_nonce_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "stale",
        "future",
        "priority is too low",
        "invalid transaction",
        "invalid transaction: stale",
        "invalid transaction: future",
        "bad proof",
        "bad signature",
        "nonce",
    )
    return any(marker in text for marker in markers)
