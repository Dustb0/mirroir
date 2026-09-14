import json
import os
import shutil
import stat
import sys
import traceback
from pathlib import Path
from collections import defaultdict
import concurrent.futures

# Without a real terminal (pipe, log file, Task Scheduler) Windows falls back to
# cp1252 and the emojis below raise UnicodeEncodeError mid-run. Pin UTF-8 early.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

try:
    from tqdm import tqdm
except ImportError:
    print("ERROR: 'tqdm' is not installed. Run: pip install tqdm")
    exit(1)

try:
    import pathspec
except ImportError:
    print("ERROR: 'pathspec' is not installed. Run: pip install pathspec")
    exit(1)


SCRIPT_DIR = Path(__file__).resolve().parent

# Warn when a plan deletes at least this share of the destination.
# Override per config with "delete_warn_threshold".
DEFAULT_DELETE_WARN_THRESHOLD = 0.5

# shutil.rmtree reports failures via onexc (3.12+) or onerror (older).
RMTREE_ERROR_KWARG = "onexc" if sys.version_info >= (3, 12) else "onerror"


def force_writable(path):
    # Git stores everything under .git/objects/ read-only. Windows then refuses
    # to delete it with WinError 5; on Linux/macOS the folder permissions decide
    # instead, which is why this only ever bites on Windows.
    os.chmod(path, stat.S_IWRITE)


def on_rmtree_error(func, path, _exc):
    # Signature fits both onexc (3.12+) and onerror (older) since the third
    # argument is never inspected.
    force_writable(path)
    func(path)


def started_from_explorer() -> bool:
    # True when Windows opened the console just for this process, i.e. double
    # click or shortcut — the window would vanish before anyone reads it. From
    # an existing shell at least two processes share the console.
    if os.name != "nt":
        return False
    try:
        import ctypes

        buf = (ctypes.c_uint * 4)()
        return ctypes.windll.kernel32.GetConsoleProcessList(buf, 4) <= 1
    except Exception:
        return False


def pause_before_exit():
    if not started_from_explorer():
        return
    try:
        input("\nPress Enter to close ...")
    except (EOFError, KeyboardInterrupt):
        pass


def resolve_config_path(argv_path=None):
    # Order matters: the script's own folder beats the working directory. A
    # desktop shortcut can start the tool anywhere, and a stray config.json in
    # that folder would silently take over. Working directory stays as fallback
    # for non-editable installs, where this module lives in site-packages.
    if argv_path:
        return Path(argv_path)
    beside_script = SCRIPT_DIR / "config.json"
    if beside_script.is_file():
        return beside_script
    return Path.cwd() / "config.json"


def load_config(config_file=None):
    if config_file is None:
        config_file = resolve_config_path()
    config_file = Path(config_file)

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Config file '{config_file}' not found.")
        exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: '{config_file}' contains invalid JSON ({e}).")
        exit(1)


def is_file_different(src_file: Path, dst_file: Path) -> bool:
    try:
        if not dst_file.exists():
            return True
        src_stat = src_file.stat()
        dst_stat = dst_file.stat()
    except OSError:
        # Locked, vanished (temp/cache/cloud placeholder) or path too long:
        # copy it and let the copy worker report a real failure, rather than
        # taking the whole run down from inside the scan.
        return True
    if src_stat.st_size != dst_stat.st_size:
        return True
    if abs(src_stat.st_mtime - dst_stat.st_mtime) > 2.0:
        return True
    return False


def load_gitignore(source_dir: Path):
    lines = [".git/"]
    gitignore_path = source_dir / ".gitignore"
    try:
        if gitignore_path.is_file():
            with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                lines.extend(f.readlines())
    except OSError as e:
        print(f"[!] WARNING: could not read .gitignore ({e}) — ignoring it.")
    return pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, lines)


def is_ignored(rel_path_obj: Path, spec, is_dir=False) -> bool:
    if not spec:
        return False
    posix_path = rel_path_obj.as_posix()
    if posix_path == ".":
        return False
    if is_dir:
        posix_path += "/"
    return spec.match_file(posix_path)


def count_source_files(src, spec):
    total = 0
    for root, dirs, files in os.walk(src):
        rel_path = Path(root).relative_to(src)
        if spec:
            dirs[:] = [d for d in dirs if not is_ignored(rel_path / d, spec, True)]
            files = [f for f in files if not is_ignored(rel_path / f, spec, False)]
        total += len(files)
    return total


def count_raw_files(directory):
    return sum(len(files) for _, _, files in os.walk(directory))


def create_sync_plan(source_dir: str, dest_dir: str):
    src = Path(source_dir)
    dst = Path(dest_dir)
    plan = {"copy": [], "delete_files": [], "delete_dirs": [], "dst_file_count": 0}

    if not src.exists() or not src.is_dir():
        print(f"\n[!] WARNING: Source folder '{src}' not found! Skipping job.")
        return None

    spec = load_gitignore(src)

    src_total = count_source_files(src, spec)
    with tqdm(
        total=src_total,
        desc="🔍 Scanning source  ",
        unit="file",
        leave=False,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}]",
    ) as pbar:
        for src_dir, dirs, files in os.walk(src):
            src_dir_path = Path(src_dir)
            rel_path = src_dir_path.relative_to(src)

            if spec:
                dirs[:] = [d for d in dirs if not is_ignored(rel_path / d, spec, True)]
                files = [f for f in files if not is_ignored(rel_path / f, spec, False)]

            dst_dir_path = dst / rel_path

            for file in files:
                src_file = src_dir_path / file
                dst_file = dst_dir_path / file

                if is_file_different(src_file, dst_file):
                    plan["copy"].append((src_file, dst_file))
                pbar.update(1)

    if dst.exists():
        dst_total = count_raw_files(dst)
        plan["dst_file_count"] = dst_total
        with tqdm(
            total=dst_total,
            desc="🔍 Scanning dest.   ",
            unit="file",
            leave=False,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}]",
        ) as pbar:
            for dst_dir, dirs, files in os.walk(dst, topdown=False):
                dst_dir_path = Path(dst_dir)
                rel_path = dst_dir_path.relative_to(dst)
                src_dir_path = src / rel_path

                for file in files:
                    dst_file = dst_dir_path / file
                    src_file = src_dir_path / file

                    if not src_file.exists() or is_ignored(
                        rel_path / file, spec, False
                    ):
                        plan["delete_files"].append(dst_file)
                    pbar.update(1)

                if dst_dir_path != dst:
                    if not src_dir_path.exists() or is_ignored(rel_path, spec, True):
                        plan["delete_dirs"].append(dst_dir_path)

    return plan


# --- Multithreaded execution ---
def execute_plan(plan):
    total_tasks = (
        len(plan["copy"]) + len(plan["delete_files"]) + len(plan["delete_dirs"])
    )

    # 16 worker threads is a good sweet spot for external SSDs/HDDs
    MAX_THREADS = 16

    def copy_worker(task):
        src_file, dst_file = task
        try:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            return None
        except Exception as e:
            return f"Error copying {src_file.name}: {e}"

    def delete_worker(dst_file):
        try:
            if dst_file.exists():
                try:
                    dst_file.unlink()
                except PermissionError:
                    # Read-only file, e.g. a git object. Drop the flag and retry.
                    force_writable(dst_file)
                    dst_file.unlink()
            return None
        except Exception as e:
            return f"Error deleting {dst_file.name}: {e}"

    errors = []

    with tqdm(total=total_tasks, desc="🚀 Running backup    ", unit="action") as pbar:

        # ThreadPoolExecutor manages the parallel workers
        def run_phase(worker, items):
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=MAX_THREADS
            ) as executor:
                futures = [executor.submit(worker, item) for item in items]
                try:
                    for future in concurrent.futures.as_completed(futures):
                        err = future.result()
                        if err:
                            tqdm.write(err)
                            errors.append(err)
                        pbar.update(1)
                except KeyboardInterrupt:
                    # Drop queued work, otherwise leaving the with-block drains
                    # the whole queue before the interrupt takes effect.
                    for f in futures:
                        f.cancel()
                    raise

        # Phase 1: Copy files (PARALLEL)
        run_phase(copy_worker, plan["copy"])

        # Phase 2: Delete files (PARALLEL)
        run_phase(delete_worker, plan["delete_files"])

        # Phase 3: Remove directories (SEQUENTIAL — safe ordering matters here)
        for dst_dir in plan["delete_dirs"]:
            try:
                if dst_dir.exists():
                    shutil.rmtree(dst_dir, **{RMTREE_ERROR_KWARG: on_rmtree_error})
            except Exception as e:
                err = f"Error removing folder {dst_dir.name}: {e}"
                tqdm.write(err)
                errors.append(err)
            pbar.update(1)

    return errors


def display_plan_summary(plan, source, destination):
    src_path = Path(source)
    dst_path = Path(destination)
    dir_stats = defaultdict(
        lambda: {"copy": 0, "delete": 0, "delete_dirs": 0, "completely_deleted": False}
    )

    def get_top_level(item_path, base_path, is_file):
        rel = item_path.relative_to(base_path)
        if is_file:
            return "/" if len(rel.parts) <= 1 else f"{rel.parts[0]}/"
        else:
            return "/" if len(rel.parts) == 0 else f"{rel.parts[0]}/"

    for src_file, _ in plan["copy"]:
        top_folder = get_top_level(src_file, src_path, is_file=True)
        dir_stats[top_folder]["copy"] += 1

    for dst_file in plan["delete_files"]:
        top_folder = get_top_level(dst_file, dst_path, is_file=True)
        dir_stats[top_folder]["delete"] += 1

    for dst_dir in plan["delete_dirs"]:
        top_folder = get_top_level(dst_dir, dst_path, is_file=False)
        dir_stats[top_folder]["delete_dirs"] += 1
        rel = dst_dir.relative_to(dst_path)
        if len(rel.parts) == 1:
            dir_stats[top_folder]["completely_deleted"] = True

    print("\n SUMMARY (top-level folders):")
    print("-" * 80)
    for folder in sorted(dir_stats.keys()):
        stats = dir_stats[folder]

        if stats["completely_deleted"]:
            lines = ["folder will be completely removed incl. contents"]
        else:
            lines = []
            if stats["copy"] > 0:
                lines.append(f"{stats['copy']} to update")
            if stats["delete"] > 0:
                lines.append(f"{stats['delete']} to delete")
            if stats["delete_dirs"] > 0:
                lines.append(f"{stats['delete_dirs']} folders to remove")

        if lines:
            print(f"\n  {folder}")
            for line in lines:
                print(f"      {line}")
    print("-" * 80)


def warn_on_mass_deletion(plan, threshold):
    # Wiping most of the destination usually means the source only looks
    # present: reassigned drive letter, disconnected share, unsynced cloud
    # folder. Worth a second look before confirming.
    dst_count = plan["dst_file_count"]
    num_delete = len(plan["delete_files"])
    if dst_count == 0 or num_delete == 0:
        return
    if num_delete / dst_count < threshold:
        return

    percent = 100 * num_delete / dst_count
    print()
    print(" " + "!" * 78)
    print(
        f" ⚠️  WARNING: this plan deletes {percent:.0f}% of all files in the "
        f"destination ({num_delete} of {dst_count})."
    )
    print("    Is the source fully available?")
    print("    (external drive, network share, unsynced cloud folder)")
    print(" " + "!" * 78)


def ask_yes_no(question: str) -> bool:
    # EOFError (no stdin) is left to the caller — it means nobody can answer.
    while True:
        response = input(question).strip().lower()
        if response in ["y", "yes"]:
            return True
        elif response in ["n", "no"]:
            return False
        else:
            print("Please answer with 'y' for yes or 'n' for no.")


def run_job(job, threshold):
    # Returns "done", "skipped" or "failed".
    job_name = job.get("name", "Unnamed Job")
    source = job.get("source")
    destination = job.get("destination")

    if not source or not destination:
        print(f"\n[!] Skipping job '{job_name}': source or destination missing.")
        return "skipped"

    print(f"\n---> {job_name} <---")
    plan = create_sync_plan(source, destination)

    if plan is None:
        return "skipped"

    num_copy = len(plan["copy"])
    num_del_files = len(plan["delete_files"])
    num_del_dirs = len(plan["delete_dirs"])

    if num_copy == 0 and num_del_files == 0 and num_del_dirs == 0:
        print(" ✅ Everything is up to date. Nothing to do.")
        return "done"

    display_plan_summary(plan, source, destination)
    warn_on_mass_deletion(plan, threshold)

    if not ask_yes_no("\nProceed with these changes? (y/n): "):
        print(" ❌ Aborted by user.")
        return "skipped"

    print()
    errors = execute_plan(plan)
    if errors:
        print(f"\n ⚠️  Job completed with {len(errors)} error(s) — see log above.")
        return "failed"

    print("\n ✅ Job completed successfully!")
    return "done"


def main():
    print("=" * 80)
    print(" INTERACTIVE BACKUP (MIRROR) ".center(80, "="))
    print("=" * 80)

    config_path = resolve_config_path(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f" Config: {config_path}")

    config = load_config(config_path)
    threshold = config.get("delete_warn_threshold", DEFAULT_DELETE_WARN_THRESHOLD)
    jobs = config.get("jobs", [])

    results = {"done": 0, "skipped": 0, "failed": 0}
    aborted = False

    for index, job in enumerate(jobs):
        job_name = job.get("name", "Unnamed Job")
        try:
            results[run_job(job, threshold)] += 1

        except KeyboardInterrupt:
            # Ctrl+C ends the running job only, not the whole run.
            print("\n\n ❌ Job aborted with Ctrl+C.")
            results["skipped"] += 1
            if index == len(jobs) - 1:
                break
            try:
                if not ask_yes_no(" Continue with the next job? (y/n): "):
                    aborted = True
                    break
            except (KeyboardInterrupt, EOFError):
                print()
                aborted = True
                break

        except EOFError:
            print("\n[!] No input available (stdin closed) — aborting.")
            results["failed"] += 1
            aborted = True
            break

        except Exception as e:
            # One broken job must not take the remaining ones down with it.
            print(f"\n [!] ERROR in job '{job_name}': {type(e).__name__}: {e}")
            print("     Job skipped, the remaining ones continue.")
            if os.environ.get("MIRROIR_DEBUG"):
                traceback.print_exc()
            results["failed"] += 1

    print("\n" + "=" * 80)
    if aborted:
        print(" Run aborted — remaining jobs skipped ".center(80, "="))
    else:
        print(" All jobs completed! ".center(80, "="))
    print("=" * 80)
    print(
        f" Done: {results['done']}   "
        f"Skipped: {results['skipped']}   "
        f"With errors: {results['failed']}"
    )

    return 1 if (results["failed"] or aborted) else 0


if __name__ == "__main__":
    # The pause lives here, not in main(): the console script entry point runs
    # from a terminal that stays open anyway.
    try:
        code = main()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    except KeyboardInterrupt:
        print("\nAborted.")
        code = 1
    pause_before_exit()
    sys.exit(code)
