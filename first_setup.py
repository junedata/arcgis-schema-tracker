"""Populate config.yaml with all FeatureServer service names from the org.

Fetches the org's service listing and rewrites config.yaml so that:
- ``exclude: all``  is set as the default (nothing syncs unless listed)
- ``include:``      lists every discovered FeatureServer service name

Delete the lines for any services you do NOT want to track, then run
sync_all_schemas.py to download schemas for the remaining entries.
"""

import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.yaml"

CONFIG_TEMPLATE = """\
# ArcGIS REST services root URL for the target organization.
# Find it at: https://services3.arcgis.com/<orgId>/ArcGIS/rest/services
# The orgId appears in the URL of any ArcGIS Online item or service for that org.
services_url: {services_url}

# exclude: all means nothing syncs unless explicitly listed under include.
exclude: all

# Delete any service names below that you do NOT want to track.
# All layers and tables within each remaining service will be downloaded.
include:
{include_lines}"""


def fetch_service_names(services_url: str) -> list[str]:
    """Return sorted FeatureServer service names for the org at *services_url*.

    Args:
        services_url: ArcGIS REST services root URL

    Returns:
        Sorted list of service name strings

    Raises:
        urllib.error.URLError: On network errors
    """
    with urllib.request.urlopen(f"{services_url}?f=json") as response:
        data = json.loads(response.read())
    return sorted(
        s["name"] for s in data.get("services", []) if s["type"] == "FeatureServer"
    )


def write_config(services_url: str, service_names: list[str]) -> None:
    """Write a fresh config.yaml populated with all discovered service names.

    Args:
        services_url: ArcGIS REST services root URL to embed in the config
        service_names: List of FeatureServer service names to include
    """
    include_lines = "\n".join(f"  - {name}" for name in service_names)
    CONFIG_PATH.write_text(
        CONFIG_TEMPLATE.format(services_url=services_url, include_lines=include_lines),
        encoding="utf-8",
    )
    logger.info("Wrote %s with %d services", CONFIG_PATH, len(service_names))


def main(services_url: str | None = None) -> None:
    """Fetch services and rewrite config.yaml.

    Args:
        services_url: Override URL; falls back to existing config or prompts
    """
    if services_url is None and CONFIG_PATH.exists():
        for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("services_url:"):
                _, _, val = line.partition(":")
                services_url = val.strip()
                break

    if not services_url:
        logger.error(
            "No services_url found. Pass it as an argument or set it in config.yaml."
        )
        sys.exit(1)

    logger.info("Fetching service list from %s", services_url)
    try:
        names = fetch_service_names(services_url)
    except urllib.error.URLError as exc:
        logger.error("Failed to fetch services: %s", exc)
        sys.exit(1)

    logger.info("Found %d FeatureServer services", len(names))
    write_config(services_url, names)
    print(f"\nconfig.yaml updated. Delete any services you don't want to track, then run:\n\n    python sync_all_schemas.py\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main(sys.argv[1] if len(sys.argv) == 2 else None)
