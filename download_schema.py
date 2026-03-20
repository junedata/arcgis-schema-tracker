"""Download an ArcGIS FeatureServer layer schema to a JSON file."""

import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMAS_DIR = Path("schemas")


def encode_url(url: str) -> str:
    """Percent-encode the path component of *url*, preserving the rest.

    Args:
        url: URL that may contain unencoded characters (e.g. spaces) in its path

    Returns:
        URL with the path component safely encoded
    """
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        parts._replace(path=urllib.parse.quote(parts.path, safe="/:@"))
    )


def url_to_filename(url: str) -> str:
    """Derive a schema filename from an ArcGIS service URL.

    Args:
        url: ArcGIS service URL, e.g.
             https://services3.arcgis.com/.../CAD_Fire/FeatureServer/0

    Returns:
        Filename like ``CAD_Fire.FeatureServer.0.schema.json``

    Raises:
        ValueError: If the URL path has fewer than 3 segments
    """
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    if len(parts) < 3:
        raise ValueError(f"Cannot derive filename from URL: {url!r}")
    service_name, server_type, layer_id = parts[-3], parts[-2], parts[-1]
    return f"{service_name}.{server_type}.{layer_id}.schema.json"


def apply_ignore_keys(data: dict, ignore_keys: list[str]) -> None:
    """Replace the values of specified nested keys with ``"untracked"`` in-place.

    Args:
        data: Parsed schema dict to modify
        ignore_keys: Dot-notation paths to suppress, e.g. ``["editingInfo.lastEditDate"]``
    """
    for path in ignore_keys:
        parts = path.split(".")
        obj = data
        for part in parts[:-1]:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                break
        else:
            if isinstance(obj, dict) and parts[-1] in obj:
                obj[parts[-1]] = "untracked"


def download_schema(url: str, output_path: Path, ignore_keys: list[str] | None = None) -> bool:
    """Fetch an ArcGIS service schema and write it to *output_path* if changed.

    Args:
        url: ArcGIS service URL (``?f=json`` is appended automatically)
        output_path: Destination file path
        ignore_keys: Optional dot-notation key paths whose values should be
            replaced with ``"untracked"`` before writing

    Returns:
        True if the file was written or updated, False if content was unchanged

    Raises:
        urllib.error.HTTPError: On non-2xx responses
        urllib.error.URLError: On network errors
    """
    full_url = f"{encode_url(url)}?f=json"
    with urllib.request.urlopen(full_url) as response:
        data = json.loads(response.read())
    if ignore_keys:
        apply_ignore_keys(data, ignore_keys)
    new_content = json.dumps(data, indent=2, ensure_ascii=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.read_text(encoding="utf-8") == new_content:
        return False
    output_path.write_text(new_content, encoding="utf-8")
    logger.debug("Saved schema to %s", output_path)
    return True


def main(url: str) -> None:
    """Entry point: download schema for *url* into the schemas directory."""
    filename = url_to_filename(url)
    output_path = SCHEMAS_DIR / filename
    download_schema(url, output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if len(sys.argv) != 2:
        logger.error("Usage: python download_schema.py <arcgis_service_url>")
        sys.exit(1)
    main(sys.argv[1])
