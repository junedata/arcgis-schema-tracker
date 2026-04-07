#!/usr/bin/env python3
"""Generate a markdown draft post when schema changes are detected in a commit.

Designed to be called from a GitHub Actions workflow or manually.

Usage:
    python draft_post.py [commit_sha]

    If commit_sha is omitted, uses HEAD.

Output:
    Writes a draft markdown file to posts/_drafts/ and prints the path.
    Exit code 0 if a draft was written, 1 if no meaningful changes were found.
"""

import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sync_all_schemas import load_config

logger = logging.getLogger(__name__)

SCHEMAS_DIR = "schemas"
DRAFTS_DIR = Path("_drafts")
CONFIG_PATH = Path(__file__).parent / "config.yaml"

FIELD_IDENTITY_KEY = "name"
FIELD_DESCRIPTION_KEY = "description"
FIELD_TYPE_KEY = "type"
FIELD_ALIAS_KEY = "alias"

BREAKING_BADGE = "[Breaking]"
INFO_BADGE = "[Info]"


def _run(cmd: list[str]) -> str:
    """Run a subprocess and return stripped stdout.

    Args:
        cmd: Command and arguments to execute

    Returns:
        Stripped stdout string

    Raises:
        subprocess.CalledProcessError: If the command exits non-zero
    """
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def get_changed_schema_files(commit_sha: str) -> list[str]:
    """Return schema file paths changed in the given commit.

    Args:
        commit_sha: Git commit SHA to inspect

    Returns:
        List of paths under schemas/ ending in .schema.json
    """
    output = _run([
        "git", "diff-tree", "--no-commit-id", "-r",
        "--name-only", commit_sha,
    ])
    return [
        line for line in output.splitlines()
        if line.startswith(f"{SCHEMAS_DIR}/")
        and line.endswith(".schema.json")
    ]


def get_file_at_commit(path: str, commit_sha: str) -> str | None:
    """Return file content at a given commit, or None if absent.

    Args:
        path: Repository-relative file path
        commit_sha: Git commit SHA

    Returns:
        File content as string, or None if the file did not exist
    """
    try:
        return _run(["git", "show", f"{commit_sha}:{path}"])
    except subprocess.CalledProcessError:
        return None


def get_parent_sha(commit_sha: str) -> str | None:
    """Return the parent commit SHA, or None for an initial commit.

    Args:
        commit_sha: Git commit SHA

    Returns:
        Parent SHA string, or None
    """
    try:
        return _run(["git", "rev-parse", f"{commit_sha}^"])
    except subprocess.CalledProcessError:
        return None


def get_commit_date(commit_sha: str) -> str:
    """Return ISO date string for the commit.

    Args:
        commit_sha: Git commit SHA

    Returns:
        ISO 8601 date string
    """
    return _run(["git", "log", "-1", "--format=%cI", commit_sha])


def format_local_timestamp(
    iso_date: str,
    tz_name: str,
) -> str:
    """Convert an ISO 8601 date string to a human-readable local timestamp.

    Args:
        iso_date: ISO 8601 date string from git (e.g. 2026-04-05T10:18:19-04:00)
        tz_name: IANA timezone name (e.g. America/New_York)

    Returns:
        Formatted string like ``2026-04-05 10:18 AM EDT``
    """
    dt = datetime.fromisoformat(iso_date)
    local_dt = dt.astimezone(ZoneInfo(tz_name))
    return local_dt.strftime("%Y-%m-%d %I:%M %p %Z")


def _empty_field_diff() -> dict:
    """Return a field diff dict with all keys initialized to empty."""
    return {
        "added": [],
        "removed": [],
        "confirmed_renames": [],
        "modified": [],
        "description_removed": [],
        "removed_had_descriptions": {},
        "type_changed": [],
    }


def _classify_case(name: str) -> str:
    """Classify a field name into a case convention.

    Args:
        name: Field name string

    Returns:
        One of: UPPER_SNAKE, lower_snake, PascalCase, camelCase, mixed
    """
    if re.fullmatch(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*", name):
        return "UPPER_SNAKE"
    if re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", name):
        return "lower_snake"
    if re.fullmatch(r"[A-Z][a-zA-Z0-9]*", name) and "_" not in name:
        return "PascalCase"
    if re.fullmatch(r"[a-z][a-zA-Z0-9]*", name) and "_" not in name:
        return "camelCase"
    return "mixed"


def compute_case_stats(field_names: list[str]) -> dict[str, int]:
    """Count how many field names fall into each case convention.

    Args:
        field_names: List of field name strings

    Returns:
        Dict mapping case convention name to count, sorted descending
    """
    counts: dict[str, int] = {}
    for name in field_names:
        case = _classify_case(name)
        counts[case] = counts.get(case, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def compute_description_pct(fields: dict[str, dict]) -> float:
    """Compute the percentage of fields that have a non-empty description.

    Args:
        fields: Dict mapping field name to field dict

    Returns:
        Percentage as a float (0.0 to 100.0), or 0.0 if no fields
    """
    if not fields:
        return 0.0
    filled = sum(
        1 for f in fields.values()
        if (f.get(FIELD_DESCRIPTION_KEY) or "").strip()
    )
    return 100.0 * filled / len(fields)


def table_has_description(schema_json: str) -> bool:
    """Check whether the top-level schema has a non-empty description.

    Args:
        schema_json: Raw JSON string of the schema

    Returns:
        True if the top-level ``description`` field is non-empty
    """
    try:
        data = json.loads(schema_json)
    except json.JSONDecodeError:
        return False
    return bool((data.get("description") or "").strip())


def parse_fields(schema_json: str) -> dict[str, dict]:
    """Extract fields from a schema JSON blob, keyed by field name.

    Args:
        schema_json: Raw JSON string of the schema

    Returns:
        Dict mapping field name to its full field dict
    """
    try:
        data = json.loads(schema_json)
    except json.JSONDecodeError:
        return {}
    return {
        f[FIELD_IDENTITY_KEY]: f
        for f in data.get("fields", [])
        if f.get(FIELD_IDENTITY_KEY)
    }


def diff_fields(
    old_fields: dict[str, dict],
    new_fields: dict[str, dict],
) -> dict:
    """Compare two field dicts and return a structured diff.

    Args:
        old_fields: Fields from the previous schema version
        new_fields: Fields from the current schema version

    Returns:
        Dict with keys: added, removed, confirmed_renames, modified,
        description_removed, removed_had_descriptions, type_changed
    """
    old_names = set(old_fields)
    new_names = set(new_fields)

    raw_added = sorted(new_names - old_names)
    raw_removed = sorted(old_names - new_names)

    removed_by_alias: dict[str, str] = {
        (old_fields[n].get(FIELD_ALIAS_KEY) or "").lower(): n
        for n in raw_removed
    }
    added_by_alias: dict[str, str] = {
        (new_fields[n].get(FIELD_ALIAS_KEY) or "").lower(): n
        for n in raw_added
    }

    confirmed_renames: list[dict[str, str]] = []
    alias_matched_removed: set[str] = set()
    alias_matched_added: set[str] = set()

    for alias_lower, old_name in removed_by_alias.items():
        if alias_lower and alias_lower in added_by_alias:
            new_name = added_by_alias[alias_lower]
            old_alias = old_fields[old_name].get(FIELD_ALIAS_KEY, "")
            confirmed_renames.append({
                "old": old_name,
                "new": new_name,
                "confidence": "alias-match",
                "note": f"alias `{old_alias}` unchanged",
            })
            alias_matched_removed.add(old_name)
            alias_matched_added.add(new_name)

    unmatched_removed = sorted(set(raw_removed) - alias_matched_removed)
    unmatched_added = sorted(set(raw_added) - alias_matched_added)

    removed_had_descriptions: dict[str, str] = {
        name: old_fields[name].get(FIELD_DESCRIPTION_KEY, "")
        for name in raw_removed
        if (old_fields[name].get(FIELD_DESCRIPTION_KEY) or "").strip()
    }

    modified: list[dict] = []
    description_removed: list[str] = []
    type_changed: list[str] = []

    for name in sorted(old_names & new_names):
        old = old_fields[name]
        new = new_fields[name]
        field_changes: list[str] = []

        old_desc = (old.get(FIELD_DESCRIPTION_KEY) or "").strip()
        new_desc = (new.get(FIELD_DESCRIPTION_KEY) or "").strip()
        old_type = old.get(FIELD_TYPE_KEY, "")
        new_type = new.get(FIELD_TYPE_KEY, "")
        old_alias = old.get(FIELD_ALIAS_KEY, "")
        new_alias = new.get(FIELD_ALIAS_KEY, "")

        if old_desc and not new_desc:
            description_removed.append(name)
            truncated = old_desc[:80]
            ellipsis = "..." if len(old_desc) > 80 else ""
            field_changes.append(
                f"description removed (was: `{truncated}{ellipsis}`)"
            )
        elif not old_desc and new_desc:
            truncated = new_desc[:80]
            ellipsis = "..." if len(new_desc) > 80 else ""
            field_changes.append(
                f"description added: `{truncated}{ellipsis}`"
            )
        elif old_desc != new_desc:
            field_changes.append("description changed")

        if old_type != new_type:
            type_changed.append(name)
            field_changes.append(
                f"type changed: `{old_type}` -> `{new_type}`"
            )

        if old_alias != new_alias:
            field_changes.append(
                f"alias changed: `{old_alias}` -> `{new_alias}`"
            )

        if field_changes:
            modified.append({"name": name, "changes": field_changes})

    return {
        "added": unmatched_added,
        "removed": unmatched_removed,
        "confirmed_renames": confirmed_renames,
        "modified": modified,
        "description_removed": description_removed,
        "removed_had_descriptions": removed_had_descriptions,
        "type_changed": type_changed,
    }


def diff_top_level(old_schema: str, new_schema: str) -> dict:
    """Check for top-level metadata changes.

    Args:
        old_schema: Previous schema JSON string
        new_schema: Current schema JSON string

    Returns:
        Dict with a ``top_level`` key listing human-readable change strings
    """
    changes: list[str] = []
    try:
        old = json.loads(old_schema)
        new = json.loads(new_schema)
    except json.JSONDecodeError:
        return {"top_level": changes}

    watched_keys = [
        "fullTextSearchableFields", "geometryType",
        "displayField", "objectIdField",
    ]
    for key in watched_keys:
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            changes.append(
                f"`{key}`: `{json.dumps(old_val)}`"
                f" -> `{json.dumps(new_val)}`"
            )

    return {"top_level": changes}


def classify_changes(
    field_diff: dict,
    is_new_table: bool,
) -> tuple[list[str], bool]:
    """Classify a field diff into change-type tags.

    Args:
        field_diff: Output of :func:`diff_fields` or :func:`_empty_field_diff`
        is_new_table: Whether the schema file is brand new

    Returns:
        Tuple of (list of change-type tag strings, is_breaking bool)
    """
    tags: list[str] = []
    breaking = False

    if is_new_table:
        tags.append("new-table")

    if field_diff["added"]:
        tags.append("column-added")

    if field_diff["confirmed_renames"]:
        tags.append("column-renamed")
        breaking = True

    if field_diff["removed"]:
        tags.append("column-removed-or-renamed")
        breaking = True

    if (field_diff["description_removed"]
            or field_diff["removed_had_descriptions"]):
        tags.append("description-removed")

    if field_diff["type_changed"]:
        tags.append("type-changed")
        breaking = True

    breaking_tags = {
        "column-renamed", "column-removed-or-renamed",
        "description-removed", "type-changed",
    }
    if (field_diff["modified"]
            and not any(t in breaking_tags for t in tags)):
        tags.append("field-modified")

    if not tags:
        tags.append("metadata-change")

    return tags, breaking


def service_name_from_path(schema_path: str) -> str:
    """Extract the service name from a schema file path.

    Args:
        schema_path: e.g. ``schemas/Rental_Registrations.FeatureServer.0.schema.json``

    Returns:
        Service name, e.g. ``Rental_Registrations``
    """
    filename = Path(schema_path).stem.replace(".schema", "")
    parts = filename.split(".")
    return parts[0] if parts else filename


def rest_url(services_url: str, service_name: str) -> str:
    """Build a REST API query URL for the given service.

    Args:
        services_url: Base ArcGIS REST services URL from config
        service_name: Name of the feature service

    Returns:
        Full query URL with HTML output format
    """
    return (
        f"{services_url}/{service_name}/FeatureServer/0/"
        f"query?where=1%3D1&outFields=*"
        f"&orderByFields=OBJECTID+DESC&f=html"
    )


def diff_url(
    repo_url: str,
    commit_sha: str,
    schema_path: str,
) -> str:
    """Build a GitHub commit diff URL.

    Args:
        repo_url: GitHub repository URL from config
        commit_sha: Git commit SHA
        schema_path: Repository-relative schema file path

    Returns:
        URL linking to the commit diff anchored to the file
    """
    anchor = schema_path.replace("/", "-")
    return f"{repo_url}/commit/{commit_sha}#{anchor}"


def _render_field_list(names: list[str], prefix: str = "-") -> str:
    """Render a list of field names as markdown bullets."""
    if not names:
        return "_None_"
    return "\n".join(f"{prefix} `{n}`" for n in names)


def _render_modified_fields(modified: list[dict]) -> str:
    """Render modified field details as nested markdown bullets."""
    if not modified:
        return "_None_"
    lines: list[str] = []
    for f in modified:
        lines.append(f"- `{f['name']}`")
        for c in f["changes"]:
            lines.append(f"  - {c}")
    return "\n".join(lines)


def _render_case_stats(case_counts: dict[str, int]) -> str:
    """Render case convention counts as an inline summary.

    Args:
        case_counts: Dict from :func:`compute_case_stats`

    Returns:
        e.g. ``UPPER_SNAKE: 12, PascalCase: 3, mixed: 2``
    """
    return ", ".join(f"{k}: {v}" for k, v in case_counts.items())


def render_what_changed(
    service_name: str,
    field_diff: dict,
    top_level: dict,
    is_new_table: bool,
    commit_sha: str,
    schema_path: str,
    services_url: str,
    repo_url: str,
    case_stats: dict[str, int],
    description_pct: float,
    has_table_description: bool,
) -> str:
    """Render the per-service 'what changed' markdown section.

    Args:
        service_name: Name of the ArcGIS feature service
        field_diff: Structured diff from :func:`diff_fields`
        top_level: Top-level metadata diff from :func:`diff_top_level`
        is_new_table: Whether this is a newly tracked table
        commit_sha: Git commit SHA
        schema_path: Repository-relative schema file path
        services_url: Base ArcGIS REST services URL from config
        repo_url: GitHub repository URL from config
        case_stats: Case convention counts from :func:`compute_case_stats`
        description_pct: Percentage of fields with descriptions
        has_table_description: Whether the table has a top-level description

    Returns:
        Markdown string describing changes
    """
    parts: list[str] = []

    if is_new_table:
        parts.append("**New table added to the tracked org.**\n")

    if field_diff["confirmed_renames"]:
        lines = [
            f"- `{r['old']}` -> `{r['new']}` _{r['note']}_"
            for r in field_diff["confirmed_renames"]
        ]
        count = len(field_diff["confirmed_renames"])
        parts.append(
            f"**Fields renamed ({count}) -- breaking change:**\n"
            + "\n".join(lines) + "\n"
        )

    if field_diff["removed"]:
        lines = []
        for name in field_diff["removed"]:
            has_desc = name in field_diff["removed_had_descriptions"]
            note = " _(had description -- now lost)_" if has_desc else ""
            lines.append(f"- `{name}`{note}")
        count = len(field_diff["removed"])
        parts.append(
            f"**Fields removed or unconfirmed rename ({count})"
            f" -- breaking change, verify manually:**\n"
            + "\n".join(lines) + "\n"
        )

    if field_diff["added"]:
        count = len(field_diff["added"])
        parts.append(
            f"**Fields added ({count}):**\n"
            f"{_render_field_list(field_diff['added'])}\n"
        )

    if field_diff["modified"]:
        count = len(field_diff["modified"])
        parts.append(
            f"**Fields modified ({count}):**\n"
            f"{_render_modified_fields(field_diff['modified'])}\n"
        )

    if field_diff["description_removed"]:
        count = len(field_diff["description_removed"])
        parts.append(
            f"**Existing fields with descriptions removed ({count}):**\n"
            f"{_render_field_list(field_diff['description_removed'])}\n"
        )

    if top_level["top_level"]:
        items = "\n".join(f"- {c}" for c in top_level["top_level"])
        parts.append(f"**Top-level metadata changes:**\n{items}\n")

    stats_lines: list[str] = []
    if not has_table_description:
        stats_lines.append(
            "- **Table description:** missing"
        )
    stats_lines.append(
        f"- **Field descriptions:** {description_pct:.0f}% complete"
    )
    stats_lines.append(
        f"- **Column naming:** {_render_case_stats(case_stats)}"
    )
    parts.append("\n".join(stats_lines) + "\n")

    parts.append(
        f"[View full diff ->]"
        f"({diff_url(repo_url, commit_sha, schema_path)})"
    )
    parts.append(
        f"[REST API ->]({rest_url(services_url, service_name)})"
    )

    return "\n".join(parts)


def render_draft(
    date_str: str,
    commit_sha: str,
    service_names: list[str],
    change_types: list[str],
    is_breaking: bool,
    per_service_sections: list[str],
    repo_url: str,
    local_timestamp: str,
) -> str:
    """Render the full draft post markdown.

    Args:
        date_str: ISO date string from the commit
        commit_sha: Git commit SHA
        service_names: List of affected service names
        change_types: Aggregated change-type tags
        is_breaking: Whether any change is breaking
        per_service_sections: Pre-rendered per-service markdown sections
        repo_url: GitHub repository URL from config
        local_timestamp: Human-readable timestamp in the configured timezone

    Returns:
        Complete markdown draft string with YAML frontmatter
    """
    badge = BREAKING_BADGE if is_breaking else INFO_BADGE
    tables_joined = ", ".join(service_names)
    date_short = date_str[:10]

    change_type_yaml = "\n".join(
        f"  - {t}" for t in sorted(set(change_types))
    )
    tables_yaml = "\n".join(f"  - {s}" for s in service_names)

    verb = "schema change" if is_breaking else "Schema update"
    commit_link = f"[`{commit_sha[:8]}`]({repo_url}/commit/{commit_sha})"

    title = f"{badge} {verb.capitalize()}: {tables_joined}"

    breaking_banner = ""
    if is_breaking:
        breaking_banner = (
            "> **This commit contains a potential breaking change.**"
            " A breaking change occurs when a field is removed,"
            " renamed, or has its type changed -- any downstream"
            " query, pipeline, or application that references the"
            " old field name or type will fail silently or return"
            " no data."
        )

    breaking_dev_note = (
        "**Action required:** Any pipeline or script referencing the"
        " removed/renamed fields by name will fail on its next run."
        " Review the field list above and update references before"
        " the next execution."
        if is_breaking
        else "_TODO_"
    )

    sections = "\n".join(per_service_sections)

    return f"""---
date: {date_short}
title: "{title}"
commit: {commit_sha[:8]}
tables:
{tables_yaml}
change_types:
{change_type_yaml}
breaking: {"true" if is_breaking else "false"}
draft: true
---

**{local_timestamp}** | {commit_link}

{breaking_banner}

{sections}

---

## Why it matters -- civic context

_TODO_

## Why it matters -- developers

{breaking_dev_note}

## Open questions

_TODO_
"""


def make_slug(date_str: str, service_names: list[str]) -> str:
    """Build a URL-safe slug for the draft filename.

    Args:
        date_str: ISO date string (first 10 chars used)
        service_names: List of service names (first 2 used)

    Returns:
        Slug string like ``2026-04-05-rental-registrations``
    """
    date_short = date_str[:10]
    tables_part = "-".join(
        re.sub(r"[^a-z0-9]+", "-", s.lower())
        for s in service_names[:2]
    ).strip("-")
    return f"{date_short}-{tables_part}"


def main() -> None:
    """Entry point: generate a draft post for schema changes in a commit."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    config = load_config(CONFIG_PATH)
    services_url = str(config.get("services_url", ""))
    repo_url = str(config.get("repo_url", ""))
    tz_name = str(config.get("timezone", "UTC"))

    if not services_url:
        logger.error("services_url is not set in config.yaml")
        sys.exit(1)
    if not repo_url:
        logger.error("repo_url is not set in config.yaml")
        sys.exit(1)

    commit_sha = (
        sys.argv[1]
        if len(sys.argv) > 1
        else _run(["git", "rev-parse", "HEAD"])
    )
    parent_sha = get_parent_sha(commit_sha)
    commit_date = get_commit_date(commit_sha)
    local_timestamp = format_local_timestamp(commit_date, tz_name)

    changed_files = get_changed_schema_files(commit_sha)
    if not changed_files:
        logger.info(
            "No schema files changed in this commit. No draft generated."
        )
        sys.exit(1)

    all_service_names: list[str] = []
    all_change_types: list[str] = []
    any_breaking = False
    per_service_sections: list[str] = []

    for schema_path in changed_files:
        service_name = service_name_from_path(schema_path)
        all_service_names.append(service_name)

        new_content = get_file_at_commit(schema_path, commit_sha)
        old_content = (
            get_file_at_commit(schema_path, parent_sha)
            if parent_sha is not None
            else None
        )

        is_new_table = old_content is None

        if is_new_table:
            new_fields = parse_fields(new_content) if new_content else {}
            field_diff = _empty_field_diff()
            field_diff["added"] = sorted(new_fields.keys())
            top_level: dict = {"top_level": []}
        else:
            old_fields = parse_fields(old_content)
            new_fields = parse_fields(new_content) if new_content else {}
            field_diff = diff_fields(old_fields, new_fields)
            top_level = (
                diff_top_level(old_content, new_content)
                if new_content
                else {"top_level": []}
            )

        change_types, is_breaking = classify_changes(
            field_diff, is_new_table
        )
        all_change_types.extend(change_types)
        if is_breaking:
            any_breaking = True

        case_stats = compute_case_stats(list(new_fields.keys()))
        desc_pct = compute_description_pct(new_fields)
        has_table_desc = (
            table_has_description(new_content) if new_content else False
        )

        section = (
            f"## `{service_name}`\n\n"
            + render_what_changed(
                service_name, field_diff, top_level,
                is_new_table, commit_sha, schema_path,
                services_url, repo_url,
                case_stats, desc_pct, has_table_desc,
            )
        )
        per_service_sections.append(section)

    slug = make_slug(commit_date, all_service_names)
    draft_path = DRAFTS_DIR / f"{slug}.md"
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

    content = render_draft(
        date_str=commit_date,
        commit_sha=commit_sha,
        service_names=all_service_names,
        change_types=all_change_types,
        is_breaking=any_breaking,
        per_service_sections=per_service_sections,
        repo_url=repo_url,
        local_timestamp=local_timestamp,
    )

    draft_path.write_text(content, encoding="utf-8")
    print(draft_path)
    sys.exit(0)


if __name__ == "__main__":
    main()
