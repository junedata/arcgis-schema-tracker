"""Fetch schemas for every layer and table in an ArcGIS org's FeatureServers."""

import json
import logging
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from download_schema import apply_ignore_keys, download_schema, encode_url, url_to_filename, SCHEMAS_DIR

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.yaml"


class Progress:
    """Thread-safe terminal progress bar rendered in-place using carriage return.

    Args:
        total: Total number of items to process
        label: Label displayed to the left of the bar
        width: Character width of the bar itself
    """

    def __init__(self, total: int, label: str, width: int = 35) -> None:
        self._total = total
        self._current = 0
        self._label = label
        self._width = width
        self._lock = threading.Lock()
        self._tty = sys.stdout.isatty()
        if self._tty:
            self._render()

    def increment(self) -> None:
        """Increment the counter by one and redraw the bar."""
        with self._lock:
            self._current += 1
            if self._tty:
                self._render()

    def _render(self) -> None:
        filled = int(self._width * self._current / self._total) if self._total else self._width
        tip = ">" if filled < self._width else "="
        bar = "=" * filled + tip + " " * max(0, self._width - filled - 1)
        sys.stdout.write(f"\r{self._label} [{bar}] {self._current}/{self._total}")
        sys.stdout.flush()

    def done(self) -> None:
        """Move to the next line after the bar is complete."""
        if self._tty:
            sys.stdout.write("\n")
            sys.stdout.flush()


def load_config(path: Path) -> dict[str, str | list[str]]:
    """Parse a simple YAML config supporting scalar and list values.

    Handles ``key: value`` scalar lines and ``- item`` list entries grouped
    under the most recently seen key.

    Args:
        path: Path to the YAML file

    Returns:
        Dict mapping keys to either a string value or a list of strings

    Raises:
        FileNotFoundError: If *path* does not exist
    """
    config: dict[str, str | list[str]] = {}
    current_key: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            if current_key is not None:
                entry = config.setdefault(current_key, [])
                if isinstance(entry, list):
                    entry.append(line[2:].strip())
        else:
            key, _, value = line.partition(":")
            current_key = key.strip()
            if value.strip():
                config[current_key] = value.strip()
    return config


def _is_valid_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return bool(parsed.scheme in ("http", "https") and parsed.netloc)


def validate_config(config: dict[str, str | list[str]]) -> list[str]:
    """Validate config values and return a list of error messages.

    Args:
        config: Parsed config dict from :func:`load_config`

    Returns:
        List of human-readable error strings; empty if config is valid
    """
    errors: list[str] = []
    on_change = config.get("on_change", "none")

    services_url = config.get("services_url", "")
    if not services_url:
        errors.append("services_url is required")
    elif not isinstance(services_url, str) or not _is_valid_url(services_url):
        errors.append(f"services_url is not a valid URL: {services_url!r}")

    if on_change not in ("none", "commit", "ntfy", "both"):
        errors.append(f"on_change must be one of: none, commit, ntfy, both — got {on_change!r}")

    if on_change in ("ntfy", "both"):
        ntfy_topic = config.get("ntfy_topic", "")
        if not ntfy_topic:
            errors.append("ntfy_topic is required when on_change is 'ntfy' or 'both'")

    if on_change in ("commit", "both"):
        repo_url = config.get("repo_url", "")
        if not repo_url:
            errors.append("repo_url is required when on_change is 'commit' or 'both'")
        elif not isinstance(repo_url, str) or not _is_valid_url(repo_url):
            errors.append(f"repo_url is not a valid URL: {repo_url!r}")

    ntfy_server = config.get("ntfy_server", "")
    if ntfy_server and (not isinstance(ntfy_server, str) or not _is_valid_url(ntfy_server)):
        errors.append(f"ntfy_server is not a valid URL: {ntfy_server!r}")

    include = config.get("include", "all")
    if include != "all" and not isinstance(include, list):
        errors.append("include must be 'all' or a list of service names")

    exclude = config.get("exclude", [])
    if exclude != "all" and not isinstance(exclude, list):
        errors.append("exclude must be 'all' or a list of service names")

    ignore_keys = config.get("ignore_keys")
    if ignore_keys is not None and not isinstance(ignore_keys, list):
        errors.append("ignore_keys must be a list of dot-notation key paths")

    tz_name = config.get("timezone", "")
    if tz_name:
        try:
            ZoneInfo(str(tz_name))
        except KeyError:
            errors.append(f"timezone is not a valid IANA timezone name: {tz_name!r}")

    return errors


def fetch_json(url: str) -> dict:
    """GET *url* with ``?f=json`` and return the parsed response.

    Args:
        url: ArcGIS REST endpoint

    Returns:
        Parsed JSON response

    Raises:
        urllib.error.HTTPError: On non-2xx responses
        urllib.error.URLError: On network errors
    """
    with urllib.request.urlopen(f"{encode_url(url)}?f=json") as response:
        return json.loads(response.read())


def layer_urls(service: dict) -> list[str]:
    """Return layer/table schema URLs for a single FeatureServer service.

    Args:
        service: Service dict with ``url`` key from the org services listing

    Returns:
        List of layer/table endpoint URLs
    """
    data = fetch_json(service["url"])
    items = data.get("layers", []) + data.get("tables", [])
    return [f"{service['url']}/{item['id']}" for item in items]


def sync_schema(url: str, ignore_keys: list[str] | None = None) -> str | None:
    """Download one schema; return the path if content changed, else None.

    Args:
        url: ArcGIS layer/table endpoint URL
        ignore_keys: Optional dot-notation key paths to suppress in the output

    Returns:
        Path string if the schema was updated, None if unchanged
    """
    output_path = SCHEMAS_DIR / url_to_filename(url)
    changed = download_schema(url, output_path, ignore_keys=ignore_keys)
    return str(output_path) if changed else None


def git_commit_and_push(changed_files: list[str], tz: tzinfo = timezone.utc) -> str:
    """Stage changed schema files, commit with a datestamped message, and push.

    Only the explicitly changed schema files are staged. Any other directories
    in the repository (e.g. alerts/, changes/, requests/) are left untouched.

    Args:
        changed_files: List of file paths to stage

    Returns:
        The full commit hash of the new commit

    Raises:
        subprocess.CalledProcessError: If any git command fails
    """
    subprocess.run(["git", "add"] + changed_files, check=True)
    services = sorted({Path(f).name.split(".")[0] for f in changed_files})
    timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")
    message = f"{timestamp} - Changed {', '.join(services)}"
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)
    commit_hash = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    n = len(changed_files)
    logger.info("Committed and pushed %d changed %s", n, "schema" if n == 1 else "schemas")
    return commit_hash


def notify_ntfy(
    topic: str,
    changed_files: list[str],
    server: str = "https://ntfy.sh",
    commit_url: str = "",
) -> None:
    """POST a change notification to an ntfy channel.

    Args:
        topic: ntfy topic name
        changed_files: List of changed schema file paths to include in the message
        server: ntfy server base URL
        commit_url: Optional URL to the specific commit; included in the message
            body and set as the notification click target

    Raises:
        urllib.error.URLError: On network errors
    """
    names = "\n".join(Path(f).name for f in sorted(changed_files))
    count = len(changed_files)
    message = f"{count} {'schema' if count == 1 else 'schemas'} changed:\n{names}"
    if commit_url:
        message += f"\n{commit_url}"
    url = f"{server.rstrip('/')}/{topic}"
    headers: dict[str, str] = {"Content-Type": "text/plain"}
    if commit_url:
        headers["Click"] = commit_url
    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req) as response:
        logger.info("ntfy alert sent to %s (status %d)", url, response.status)


def filter_services(
    services: list[dict],
    include: list[str] | str,
    exclude: list[str] | str,
) -> list[dict]:
    """Apply include/exclude rules to a list of services.

    Rules:
    - ``exclude: all`` + ``include: [list]`` → whitelist: only listed services
    - ``include: all`` + ``exclude: [list]`` → everything except listed services
    - ``include: [list]`` + ``exclude: [list]`` → listed services minus excluded
    - ``include: all`` + ``exclude: all``  → nothing

    Args:
        services: Full list of service dicts from the org
        include: ``"all"`` or a list of service names to include
        exclude: ``"all"`` or a list of service names to exclude

    Returns:
        Filtered list of service dicts
    """
    included = (
        services if include == "all"
        else [s for s in services if s["name"] in include]
    )
    if exclude == "all":
        return [] if include == "all" else included
    return [s for s in included if s["name"] not in exclude]


def main(
    services_url: str,
    include: list[str] | str = "all",
    exclude: list[str] | str = [],
    ignore_keys: list[str] | None = None,
    on_change: str = "none",
    ntfy_topic: str = "",
    ntfy_server: str = "https://ntfy.sh",
    repo_url: str = "",
    tz: tzinfo = timezone.utc,
) -> None:
    """Download FeatureServer schemas for the org at *services_url*.

    Args:
        services_url: ArcGIS REST services root URL
        include: ``"all"`` or list of service names to sync
        exclude: ``"all"`` or list of service names to skip
        ignore_keys: Optional dot-notation key paths to suppress in all schemas
        on_change: What to do when schemas change: ``"none"``, ``"commit"``,
            ``"ntfy"``, or ``"both"``
        ntfy_topic: ntfy topic name (required when on_change is ``"ntfy"`` or ``"both"``)
        ntfy_server: ntfy server base URL
        repo_url: Public GitHub repository URL used to build commit links in ntfy alerts
        tz: Timezone for commit message timestamps; defaults to UTC
    """
    data = fetch_json(services_url)
    all_feature_servers = [
        s for s in data.get("services", []) if s["type"] == "FeatureServer"
    ]
    feature_servers = filter_services(all_feature_servers, include, exclude)
    logger.info("Syncing %d / %d FeatureServers", len(feature_servers), len(all_feature_servers))

    urls: list[str] = []
    progress = Progress(len(feature_servers), "Fetching layer lists ")
    with ThreadPoolExecutor() as executor:
        layer_futures = {executor.submit(layer_urls, s): s for s in feature_servers}
        for future in as_completed(layer_futures):
            service = layer_futures[future]
            try:
                urls.extend(future.result())
            except urllib.error.URLError as exc:
                sys.stdout.write("\n")
                logger.error("Skipping %s: %s", service["name"], exc)
            finally:
                progress.increment()
    progress.done()

    changed_files: list[str] = []
    progress = Progress(len(urls), "Downloading schemas  ")
    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(sync_schema, url, ignore_keys): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                result = future.result()
                if result is not None:
                    changed_files.append(result)
            except Exception as exc:
                sys.stdout.write("\n")
                logger.error("Failed %s: %s", url, exc)
            finally:
                progress.increment()
    progress.done()

    n = len(changed_files)
    logger.info("%d %s changed", n, "schema" if n == 1 else "schemas")

    if not changed_files:
        return

    commit_url = ""
    if on_change in ("commit", "both"):
        try:
            commit_hash = git_commit_and_push(changed_files, tz)
            if repo_url:
                commit_url = f"{repo_url.rstrip('/')}/commit/{commit_hash}"
        except subprocess.CalledProcessError as exc:
            logger.error("Git commit/push failed: %s", exc)

    if on_change in ("ntfy", "both"):
        if not ntfy_topic:
            logger.error("on_change includes ntfy but ntfy_topic is not set in config")
        else:
            try:
                notify_ntfy(ntfy_topic, changed_files, ntfy_server, commit_url=commit_url)
            except urllib.error.URLError as exc:
                logger.error("ntfy notification failed: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)
    config = load_config(CONFIG_PATH)
    errors = validate_config(config)
    if errors:
        for error in errors:
            logger.error("Config error: %s", error)
        sys.exit(1)
    tz_name = config.get("timezone", "")
    tz: tzinfo = ZoneInfo(str(tz_name)) if tz_name else timezone.utc
    _start = datetime.now(tz)
    print(f"Started {_start.strftime('%Y-%m-%d %H:%M:%S %Z')}", flush=True)
    url = sys.argv[1] if len(sys.argv) == 2 else config["services_url"]
    include = config.get("include", "all")
    exclude = config.get("exclude", [])
    ignore_keys = config.get("ignore_keys")
    on_change = config.get("on_change", "none")
    ntfy_topic = config.get("ntfy_topic", "")
    ntfy_server = config.get("ntfy_server", "https://ntfy.sh")
    repo_url = config.get("repo_url", "")
    main(
        url,
        include=include,
        exclude=exclude,
        ignore_keys=ignore_keys if isinstance(ignore_keys, list) else None,
        on_change=on_change,
        ntfy_topic=ntfy_topic if isinstance(ntfy_topic, str) else "",
        ntfy_server=ntfy_server if isinstance(ntfy_server, str) else "https://ntfy.sh",
        repo_url=repo_url if isinstance(repo_url, str) else "",
        tz=tz,
    )
    _end = datetime.now(tz)
    _elapsed = int((_end - _start).total_seconds())
    print(f"Finished {_end.strftime('%Y-%m-%d %H:%M:%S %Z')}  ({_elapsed}s elapsed)\n")
