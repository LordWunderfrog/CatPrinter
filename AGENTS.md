# Agent briefing — Cat Printer

Read this first, then follow links. Site-specific facts belong here; behaviour detail lives under `docs/`.

## What you are working on

YHK Classic Bluetooth thermal printer HTTP API for Home Assistant OS.

**Flow:** NFC → HA package → `POST /print/reddit` → disk spool → RFCOMM.

| Fact | Value |
|------|--------|
| Repo path | `C:\Users\AranFroggatt\PythonProjects\CatPrinter` |
| Branch | `main` |
| Add-on version | **1.1.23** (`ha-addon/config.yaml`) |
| Project status | **Finished / signed off** (2026-08-08) |
| Printer MAC | `25:00:27:00:1B:D5` |
| API | HA host `:8080` |
| Default sub | `wunkus` |
| Supervisor slug | `local_cat_printer` |

## Docs (use these, not tribal memory)

| Need | Doc |
|------|-----|
| Modules + print/settle pipeline | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Caller contract (build integrations here) | [docs/CALLERS.md](docs/CALLERS.md) |
| HTTP routes (maintainer reference) | [docs/API.md](docs/API.md) |
| Pack / Samba / Rebuild | [docs/DEPLOY.md](docs/DEPLOY.md) |
| Logs, env, smash checklist | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Reddit listing + cache | [docs/REDDIT.md](docs/REDDIT.md) |
| Markdown renderer | [docs/MARKDOWN.md](docs/MARKDOWN.md) |
| **Parked keep-awake** | [docs/PARKED.md](docs/PARKED.md) |

Index: [README.md](README.md).

## Hard rules

1. **App code at repo root.** `ha-addon/` is metadata only. Never duplicate modules there.
2. **Deploy ≠ live.** After `scripts/pack-addon.ps1 -Deploy`, the user must **Rebuild** in Settings → Apps. Do not pretend rebuild is automatic.
3. **Docs-only / Postman / local pytest** → no deploy, no version bump.
4. **Commit + push** at significant checkpoints (see `.cursor/rules/git-checkpoints.mdc`). Do not commit `dist/`, secrets, or junk review dumps.
5. **Read share logs before burning labels.** Job log: `\\home.lan\share\cat_printer\addon.log`.
6. **Do not “fix” smash with extra feed newlines / `PRINT_FEED_LINES`.** Lowers plastic label stock. Prefer longer settle (`SPOOL_PX_PER_SEC` / gap).
7. **Glanceable success** is `event=queued` → `event=printed`, not HTTP accept / `print_start`.
8. User preference: direct, blunt, no paper waste. Challenge bad approaches.

## Out of scope (do not pick up)

Shopping-list / Grocy integrations, inventing CI pipelines, typed-config refactors, inventing caller payloads, keep-awake Phase B unless the user re-opens [docs/PARKED.md](docs/PARKED.md).

## Workspace tip

Multi-root workspaces with `N:` / `S:` break Agents (cwd becomes `workspace.json`). Prefer **single root** = this repo. Optional gitignored junctions:

```powershell
New-Item -ItemType Directory -Force -Path .ha | Out-Null
cmd /c mklink /D ".ha\local_apps" "\\home.lan\local_apps\cat_printer"
cmd /c mklink /D ".ha\config" "\\home.lan\config"
cmd /c mklink /D ".ha\share" "\\home.lan\share"
```

Cloud agents cannot use those drive letters — use UNC or dump logs to `/config` on HA.

## Tests

```powershell
python -m pytest tests/ -q --tb=short
```

## Deploy one-liner

```powershell
powershell -NoProfile -File scripts/pack-addon.ps1 -Deploy
```

Then tell the user to Rebuild. Details: [docs/DEPLOY.md](docs/DEPLOY.md).
