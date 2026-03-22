# arcgis-schema-tracker

ArcGIS dataset schemas change silently. This tool snapshots every layer and table schema in an ArcGIS organization as versioned JSON files in a public GitHub repository. Every sync is a git commit, so anyone can subscribe to the commit feed and get notified the moment something changes.

It works with any publicly accessible ArcGIS organization.

## Problems solved

- **Breaking changes go unnoticed.** A dataset owner renames a field or changes a data type. A dependent pipeline, application, or script silently fails at its next run. No one was notified.
- **Additive changes go unnoticed.** A new field is added. Downstream developers who could benefit never find out, and continue working around a gap that no longer exists.
- **No change history exists.** ArcGIS does not version or publish schema history. There is no way to look up what a schema looked like last week, or when a field was added.
- **Change notifications require polling.** A developer who wants to know if a schema changed must periodically fetch it manually and compare it themselves.
- **No scalable channel for communicating changes.** Manually notifying downstream users is time-consuming and does not scale. Many data owners have no communications channel for it at all. A public feed lets downstream users subscribe on their own terms, with no coordination required.
- **Schema edits by other teams go unreviewed.** In organizations where multiple teams have schema-edit privileges, a central data manager may have no visibility into changes made by others. Running these scripts creates an audit trail, and any detected change becomes a natural check-in opportunity to confirm the change aligns with data policies and organizational standards.

## How it works

1. `sync_all_schemas.py` fetches the schema for every included layer and table from the ArcGIS REST API and writes each one as a pretty-printed JSON file under `schemas/`.
2. Those files are committed to a public GitHub repository.
3. Downstream users subscribe to the repo's Atom commit feed and get notified of any change: added fields, removed fields, type changes, new services, and retired services.

When a schema change is pushed, it has already taken effect. Any downstream service that depends on the changed dataset may already be broken. A subscribed developer can receive the notification, identify what changed, and act immediately. For processes that do not run continuously, a fix can be deployed before the next scheduled run. Even for continuously running services, prompt awareness shortens the window of downtime or malfunction.

A single sync by one data owner can notify any number of downstream developers at once, multiplying the value of the change across every team that depends on it.

---

## For data owners

### Setup

No dependencies. Requires Python 3.9+.

1. Copy the sample config:

   ```bash
   cp config.sample.yaml config.yaml
   ```

2. Set `services_url` in `config.yaml` to your org's ArcGIS REST services root, then run:

   ```bash
   python first_setup.py
   ```

   This fetches all FeatureServer names from your org and writes them into the `include:` list. Delete any services you do **not** want to track, then save.

### Creating the GitHub repository

The public repository only needs to contain the `schemas/` directory. The Python scripts, config files, and credentials stay on the machine running the syncs and do not need to be committed.

1. Create a new public repository on GitHub.

2. Clone it locally and run an initial sync into it:

   ```bash
   python sync_all_schemas.py
   ```

3. Commit and push only the schema files:

   ```bash
   git add schemas/
   git commit -m "Initial schema snapshot"
   git push
   ```

4. Share the Atom feed URL with downstream users (see [For downstream users](#for-downstream-users) below).

**What happens when a new service is added to `include`.** On the next sync, a new schema file appears in the diff. Subscribers will see it as a new file in the feed.

**What happens when a service is removed from the org.** The schema file remains in the repository as a historical record of the schema at the time it was last tracked. To explicitly retire it, delete the file manually and commit the deletion.

### Automating syncs with cron

With `on_change: commit` set in `config.yaml`, the script handles the git commit and push automatically. The cron entry is simply:

```bash
crontab -e
```

```
0 * * * * cd /path/to/repo && /path/to/python sync_all_schemas.py
```

A commit is only created when schemas have actually changed, so the history stays meaningful.

> **Tradeoff:** `on_change: commit` uses a generic datestamped commit message. If your team wants to document the reason for each schema change (recommended), use `on_change: ntfy` instead. The ntfy alert prompts a human to review the diff and commit manually with a meaningful message.

**macOS alternative: launchd**

macOS uses `launchd` as its native scheduler. First, find your Python path:

```bash
which python3
```

Create a plist file at `~/Library/LaunchAgents/com.yourname.arcgis-schema-tracker.plist`, replacing `/opt/homebrew/bin/python3` with your actual path:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.yourname.arcgis-schema-tracker</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/python3</string>
    <string>/path/to/repo/sync_all_schemas.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/path/to/repo</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/path/to/repo/sync.log</string>
  <key>StandardErrorPath</key>
  <string>/path/to/repo/sync.log</string>
</dict>
</plist>
```

Load it with:

```bash
launchctl load ~/Library/LaunchAgents/com.yourname.arcgis-schema-tracker.plist
```

To apply changes to the plist (e.g. after editing the interval or path):

```bash
launchctl unload ~/Library/LaunchAgents/com.yourname.arcgis-schema-tracker.plist
launchctl load ~/Library/LaunchAgents/com.yourname.arcgis-schema-tracker.plist
```

### GitHub credentials

Pushing to GitHub from a script requires authentication. The recommended approach is a personal access token (PAT) with only `Contents: write` scope on the target repository.

Store the token in Git's credential helper or in the remote URL, not in any file that could be committed:

```bash
git remote set-url origin https://<username>:<token>@github.com/<owner>/<repo>.git
```

Alternatively, use SSH key authentication. Generate a deploy key for the repository with write access and add it to your SSH agent. This avoids storing any token in the remote URL.

Never commit a token or private key to the repository.

### Optional recommendations

**Add a README to the public repository.** The public repo is the interface for downstream users. Consider adding a README describing your organization's datasets, schema change policy, and who to contact with questions. This is separate from this file, which documents the tool itself.

**Document schema changes in commit messages.** When a sync produces a diff, it shows exactly which fields were added, removed, or modified. Using `on_change: ntfy` instead of `commit` keeps a human in the loop — the ntfy alert prompts the data team to review the diff and commit manually with a message explaining why the change was made. Downstream users who receive the feed notification will see that explanation alongside the diff, giving them the context they need to update their own processes.

### Syncing schemas

```bash
python sync_all_schemas.py
```

Fetches the current schema for every included service and writes each layer and table to `schemas/<ServiceName>.<ServerType>.<LayerId>.schema.json`. Commit and push the result to publish the changes.

To target a different org without editing `config.yaml`:

```bash
python sync_all_schemas.py \
  https://services3.arcgis.com/<orgId>/ArcGIS/rest/services
```

### Configuration

#### on_change

Controls what happens automatically when schemas change after a sync:

| Value | Behavior |
|---|---|
| `none` | Write files only. Commit and push manually. (default) |
| `commit` | Auto-commit changed files and push with a datestamped message. |
| `ntfy` | Send an alert to an ntfy channel. Commit and push manually. |
| `both` | Auto-commit and push, then send an ntfy alert. |

```yaml
on_change: ntfy
ntfy_topic: your-topic-name
ntfy_server: https://ntfy.sh  # optional, defaults to ntfy.sh
```

`ntfy` is a free, open-source push notification service. Subscribers follow a topic URL in any ntfy-compatible app or browser. When a sync detects changed schemas, an alert is sent listing the affected files.

Choosing `ntfy` over `commit` means the git commit happens manually, giving the data owner an opportunity to write a meaningful commit message explaining the change before it is published to subscribers.

#### include / exclude

`config.yaml` controls which services are synced:

| `include` | `exclude` | Result |
|---|---|---|
| `all` | `all` | nothing |
| `all` | list | everything except listed |
| list | `all` | only listed services |
| list | list | listed services minus excluded |

#### ignore_keys

Some fields in ArcGIS schema responses are metadata rather than schema. Timestamps, cache ages, and similar values change frequently without reflecting a meaningful schema change. Left unfiltered, these would generate commits on every sync with no useful signal.

`ignore_keys` in `config.yaml` accepts a list of dot-notation key paths. Any matching key in a downloaded schema will have its value replaced with `"untracked"` before the file is written:

```yaml
ignore_keys:
  - editingInfo.lastEditDate
  - editingInfo.dataLastEditDate
  - timeInfo.timeExtent
```

This keeps commits focused on structural changes: fields added or removed, types changed, and other differences that actually affect downstream consumers.

---

## For downstream users

### Subscribing to schema changes

Every sync that produces changes results in a new commit. Subscribe to the Atom feed to get notified:

```
https://github.com/<owner>/arcgis-schema-tracker-v0.1/commits/main.atom
```

Paste that URL into any feed reader (e.g. Feedly, NewsBlur). Each entry will show exactly which schema files changed and how.

The data owner may also publish a ntfy channel for push notifications. If they have configured `on_change: ntfy` or `on_change: both`, subscribers can follow that channel in the [ntfy app](https://ntfy.sh) to receive an immediate alert when schemas change, without needing to poll a feed. Ask the data owner whether a ntfy channel is available and what the topic name is.

> The Atom feed is only accessible if this repository is public.

---

## Going further

Because the Atom feed notifies subscribers of any commit to the repository, the repo can serve as a broader communication channel around data governance, not just schema snapshots.

Data owners and downstream users can add folders alongside `schemas/` for other types of announcements. `sync_all_schemas.py` only ever touches the `schemas/` directory, so anything committed to other folders is preserved across syncs.

Some possibilities:

- **`alerts/`** — urgent notices about breaking changes that have already been deployed, or unexpected data quality issues.
- **`announcements/`** — advance notice of upcoming schema changes, giving downstream teams time to prepare before a change goes live.
- **`changes/`** — a human-authored log of past changes with context that goes beyond what a git diff shows (rationale, related systems affected, links to documentation).
- **`requests/`** — field or schema requests submitted by downstream users, creating a lightweight public record of interest for changes.

A file in any of these folders could be as simple as a markdown file with a date in the filename, e.g. `announcements/2024-06-01-rental-registrations-new-ward-fields.md`. Templates for each folder type could be included in the repository to encourage consistent formatting.

Any commit adding or updating one of these files will appear in the Atom feed, reaching all subscribers alongside the schema change history.

---

## Reference

### Downloading a single schema

```bash
python download_schema.py <arcgis_service_url>
```

The URL must end in `.../<ServiceName>/<ServerType>/<LayerId>`, e.g.:

```bash
python download_schema.py \
  https://services3.arcgis.com/<orgId>/ArcGIS/rest/services/CAD_Fire/FeatureServer/0
```

Output is saved to `schemas/CAD_Fire.FeatureServer.0.schema.json`.

### Finding service URLs

1. Open the ArcGIS REST Services Directory for the org:
   `https://services3.arcgis.com/<orgId>/ArcGIS/rest/services`

2. Click a service name to see its layers and their IDs.

3. Click a layer. That page's URL is what you pass to the script.

Alternatively, open an ArcGIS Online item page, scroll to the **Layers** section, and click **View** next to a layer.
