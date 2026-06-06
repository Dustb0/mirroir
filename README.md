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

---

## Usage

```bash
uv run mirroir
```

mirroir will process each job in sequence:

1. **Analyze** — scans source and destination, builds a diff plan
2. **Summarize** — prints a breakdown of what will be copied/deleted, grouped by top-level folder
3. **Confirm** — asks `y/n` before touching anything
4. **Execute** — runs the sync with a live progress bar

Example output:

```
================================================================================
======================== INTERACTIVE BACKUP (MIRROR) ===========================
================================================================================

---> My Project <---

 SUMMARY (top-level folders):
--------------------------------------------------------------------------------
  /                              | 3 file(s) to copy/update
  assets/                        | 12 file(s) to copy/update, 2 file(s) to delete
  old_build/                     | Folder will be completely deleted
--------------------------------------------------------------------------------

Proceed with these changes? (y/n): y

🚀 Running backup  100%|████████████████████| 17/17 [00:02<00:00]

 ✅ Job completed successfully!
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

## Tips

- **External drives** — the 16-thread default is tuned for USB/SSD drives. For network shares you may see diminishing returns.
- **Large repos** — if your source has a `.gitignore`, mirroir respects it automatically. No extra setup needed.
- **Dry run only** — answer `n` at the prompt to see the plan without running it.

---

## License

MIT
