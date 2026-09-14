# mirroir

> A fast, interactive file mirror tool for local backups — `.gitignore`-aware, multithreaded, and confirmation-first.

**mirroir** (French for *mirror*) syncs one or more source directories to destination directories. Before touching a single file, it builds a full diff plan and asks you to confirm. Only new or changed files are copied; files deleted from the source are removed from the destination too — a true mirror, not just a copy.

---

## Features

- **Interactive dry-run** — always shows a per-folder summary before making any changes
- **`.gitignore`-aware** — automatically skips files and folders your `.gitignore` excludes
- **Multithreaded** — copies files in parallel (16 worker threads) for fast external drive performance
- **Smart diffing** — only acts on files that have actually changed (size + mtime comparison)
- **Multi-job config** — back up as many source/destination pairs as you like from one `config.json`
- **Fails soft** — a job that errors out never takes the remaining jobs down with it
- **Mass-deletion guard** — warns before wiping a destination whose source only *looks* available
- **Script-friendly** — honest error counts and a meaningful exit code
- **Clean progress bars** — powered by [tqdm](https://github.com/tqdm/tqdm)

---

## Requirements

- [mise](https://mise.jdx.dev) — manages Python and uv per-project
- Everything else (Python 3.12, uv) is installed automatically by mise

---

## Setup

1. Clone the repository:

```bash
git clone https://github.com/your-username/mirroir.git
cd mirroir
```

2. Install tools and dependencies:

```bash
mise install   # installs Python 3.12 + uv (scoped to this project)
uv sync        # creates .venv and installs tqdm + pathspec
```

3. Copy the example config and edit it:

```bash
cp config.json.example config.json
```

4. Open `config.json` and define your backup jobs (see [Configuration](#configuration) below).

---

## Configuration

`config.json` holds a list of named jobs. Each job needs a `source` and a `destination` path.

```json
{
  "jobs": [
    {
      "name": "My Project",
      "source": "/home/user/Documents/MyProject",
      "destination": "/mnt/backup/MyProject"
    },
    {
      "name": "My Photos",
      "source": "/home/user/Pictures",
      "destination": "/mnt/backup/Pictures"
    }
  ]
}
```

Paths can be absolute paths on any OS. On Windows, use forward slashes or escaped backslashes:

```json
"source": "C:/Users/YourName/Documents/MyProject"
```

> `config.json` is listed in `.gitignore` — your paths won't be committed to version control.

### Where the config is loaded from

mirroir looks for the config in this order, and **prints the path it settled on** as the first line of every run:

1. the path you pass as an argument
2. `config.json` next to `mirroir.py`
3. `config.json` in the current working directory

The script's own folder deliberately beats the working directory. Desktop shortcuts and scheduled tasks often start in some unrelated folder, and a stray `config.json` sitting there would otherwise take over silently.

### Optional settings

| Key | Default | What it does |
|-----|---------|--------------|
| `delete_warn_threshold` | `0.5` | Warn when a plan deletes at least this share of the destination. Set to a value above `1` to switch the warning off. |

```json
{
  "delete_warn_threshold": 0.8,
  "jobs": [ ... ]
}
```

---

## Usage

```bash
uv run mirroir                      # uses the config next to the script
uv run mirroir /path/to/other.json  # or point it at a specific config
```

mirroir processes each job in sequence:

1. **Analyze** — scans source and destination, builds a diff plan
2. **Summarize** — prints a breakdown of what will be copied/deleted, grouped by top-level folder
3. **Confirm** — asks `y/n` before touching anything
4. **Execute** — runs the sync with a live progress bar

Example output:

```
================================================================================
========================= INTERACTIVE BACKUP (MIRROR) ==========================
================================================================================
 Config: /path/to/mirroir/config.json

---> My Project <---

 SUMMARY (top-level folders):
--------------------------------------------------------------------------------

  /
      3 to update

  assets/
      5 to update
      2 to delete

  old_build/
      folder will be completely removed incl. contents
--------------------------------------------------------------------------------

Proceed with these changes? (y/n): y

🚀 Running backup    : 100%|██████████| 13/13 [00:00<00:00, 1083.18action/s]

 ✅ Job completed successfully!

================================================================================
============================= All jobs completed! ==============================
================================================================================
 Done: 1   Skipped: 0   With errors: 0
```

---

## How It Works

| Step | What happens |
|------|-------------|
| **Scan source** | Walks the source tree, skipping anything matched by `.gitignore` |
| **Scan destination** | Walks the destination tree to find stale files and folders |
| **Build plan** | Compares files by size and modification time; only flags genuinely changed files |
| **Confirm** | Shows the plan and waits for your `y/n` |
| **Copy (parallel)** | Up to 16 threads copy new/changed files simultaneously |
| **Delete (parallel)** | Stale files are removed in parallel |
| **Prune dirs (sequential)** | Empty or removed directories are cleaned up safely in order |

---

## Safety and failure handling

A backup tool is only useful if a bad file, a locked folder or a stray keystroke can't derail the whole run.

**One job never breaks the others.** Declining a job, an unreadable file, a missing source folder or an unexpected crash all end that single job. The run moves on to the next one and reports what happened at the end.

**Read-only files are handled.** Git marks everything under `.git/objects/` read-only, and Windows refuses to delete such files. mirroir clears the flag and retries instead of failing.

**Mass deletions are flagged.** If a plan would delete most of the destination, mirroir says so before the prompt. This almost always means the source only *looks* present — a reassigned drive letter, a disconnected network share, an unsynced cloud folder — and not that you really want the backup emptied.

**Ctrl+C cancels the current job**, then asks whether to continue with the rest. Queued copies are dropped immediately rather than draining first.

**Errors are counted, not glossed over.** A job with failures reports `⚠️ Job completed with N error(s)` instead of a green checkmark, and every run ends with a tally:

```
 Done: 3   Skipped: 1   With errors: 1
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Every job finished cleanly, or you declined them |
| `1` | At least one job reported errors, or the run was aborted |

Jobs you decline with `n`, and jobs whose source folder is missing, count as *skipped* and leave the exit code at `0`.

---

## Tips

- **External drives** — the 16-thread default is tuned for USB/SSD drives. For network shares you may see diminishing returns.
- **Large repos** — if your source has a `.gitignore`, mirroir respects it automatically. No extra setup needed.
- **Dry run only** — answer `n` at the prompt to see the plan without running it.
- **Debugging a failing job** — set `MIRROIR_DEBUG=1` to get a full traceback instead of a one-line error.
- **Which config am I actually running?** — check the `Config:` line at the top of the output before blaming the tool.

---

## License

MIT
