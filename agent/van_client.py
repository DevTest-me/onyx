"""
van_client.py — VAN Registry client via GraphQL HTTP API.
"""

import logging
import requests
from typing import List
from models import Application

log = logging.getLogger("onyx.client.van")

GRAPHQL_URL = "https://agents-api.vara.network/graphql"

TRACK_SPECS = {
    "Services": ["services", "api", "integration"],
    "Economy":  ["economy", "finance", "defi"],
    "Social":   ["social", "community", "coordination"],
    "Open":     ["general", "open", "creative"],
}


class VanClient:

    def discover_all(self) -> List[Application]:
        query = """
        {
          allApplications {
            nodes {
              id
              handle
              owner
              track
              status
              description
            }
          }
        }
        """
        try:
            resp = requests.post(
                GRAPHQL_URL,
                json={"query": query},
                timeout=15,
            )
            resp.raise_for_status()
            nodes = resp.json()["data"]["allApplications"]["nodes"]
            log.info("VAN GraphQL: fetched %d applications", len(nodes))
            return [
                Application(
                    program_id=n.get("id", ""),
                    owner=n.get("owner", ""),
                    handle=n.get("handle", ""),
                    description=n.get("description", ""),
                    track=n.get("track", "Open"),
                    github_url="",
                    skills_url="",
                    idl_url="",
                    registered_at=0,
                    season_id=0,
                    status=n.get("status", ""),
                )
                for n in nodes
            ]
        except Exception as exc:
            log.error("VAN GraphQL fetch failed: %s", exc)
            return []

    def get_specializations(self, track: str) -> List[str]:
        return TRACK_SPECS.get(track, ["general"])
