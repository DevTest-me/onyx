"""
Remove stale Onyx registrations that are not VAN application program IDs.

This repairs the temporary owner-address registrations created before the
runtime learned to read VAN's `id` field.
"""

import argparse
import logging

from config import load_config
from onyx_client import OnyxClient
from van_client import VanClient
from vara_client import VaraClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="submit removals")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)-22s  %(message)s",
    )

    cfg = load_config()
    vara = VaraClient(node_url=cfg.vara_node_url, wallet_json=cfg.wallet_json)
    vara.connect()
    try:
        onyx = OnyxClient(vara, cfg.onyx_program_id)
        van = VanClient()

        apps = van.discover_all()
        van_ids = {app.program_id.strip().lower() for app in apps if app.program_id}
        onyx_ids = {addr.strip().lower() for addr in onyx.get_all_agents()}
        stale = sorted(onyx_ids - van_ids)

        print(f"VAN apps: {len(van_ids)}")
        print(f"Onyx registrations: {len(onyx_ids)}")
        print(f"Stale registrations: {len(stale)}")

        if not stale:
            return 0

        if not args.apply:
            print("Dry run only. Re-run with --apply to remove stale registrations.")
            for address in stale:
                print(address)
            return 0

        removed = 0
        for address in stale:
            if onyx.remove_agent(address):
                removed += 1
                print(f"removed {address}")
            else:
                print(f"not found {address}")

        final_count = len(onyx.get_all_agents())
        print(f"Removed: {removed}")
        print(f"Final Onyx registrations: {final_count}")
        return 0
    finally:
        vara.close()


if __name__ == "__main__":
    raise SystemExit(main())
