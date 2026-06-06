import json
import os
import shutil
from pathlib import Path
from collections import defaultdict
import concurrent.futures

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


def load_config(config_file="config.json"):
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Config file '{config_file}' not found.")
        exit(1)
    except json.JSONDecodeError:
        print(f"ERROR: '{config_file}' contains invalid JSON.")
        exit(1)


def is_file_different(src_file: Path, dst_file: Path) -> bool:
    if not dst_file.exists():
        return True
    src_stat = src_file.stat()
    dst_stat = dst_file.stat()
    if src_stat.st_size != dst_stat.st_size:
        return True
    if abs(src_stat.st_mtime - dst_stat.st_mtime) > 2.0:
        return True
    return False


def load_gitignore(source_dir: Path):
    lines = [".git/"]
    gitignore_path = source_dir / ".gitignore"
    if gitignore_path.is_file():
        with open(gitignore_path, "r", encoding="utf-8") as f:
            lines.extend(f.readlines())
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
    plan = {"copy": [], "delete_files": [], "delete_dirs": []}

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
                dst_file.unlink()
            return None
        except Exception as e:
            return f"Error deleting {dst_file.name}: {e}"

    with tqdm(total=total_tasks, desc="🚀 Running backup    ", unit="action") as pbar:

        # ThreadPoolExecutor manages the parallel workers
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:

            # Phase 1: Copy files (PARALLEL)
            futures_copy = [executor.submit(copy_worker, task) for task in plan["copy"]]
            for future in concurrent.futures.as_completed(futures_copy):
                err = future.result()
                if err:
                    tqdm.write(err)
                pbar.update(1)

            # Phase 2: Delete files (PARALLEL)
            futures_delete = [
                executor.submit(delete_worker, f) for f in plan["delete_files"]
            ]
            for future in concurrent.futures.as_completed(futures_delete):
                err = future.result()
                if err:
                    tqdm.write(err)
                pbar.update(1)

        # Phase 3: Remove directories (SEQUENTIAL — safe ordering matters here)
        for dst_dir in plan["delete_dirs"]:
            try:
                if dst_dir.exists():
                    shutil.rmtree(dst_dir)
            except Exception as e:
                tqdm.write(f"Error removing folder {dst_dir.name}: {e}")
            pbar.update(1)


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
        actions = []

        if stats["completely_deleted"]:
            actions.append("Folder will be completely removed incl. contents")
        else:
            if stats["copy"] > 0:
                actions.append(f"{stats['copy']} file(s) to copy/update")
            if stats["delete"] > 0:
                actions.append(f"{stats['delete']} file(s) to delete")
            if stats["delete_dirs"] > 0:
                actions.append(f"{stats['delete_dirs']} subfolder(s) to remove")

        if actions:
            print(f"  {folder:<30} | {', '.join(actions)}")
    print("-" * 80)


def main():
    print("=" * 80)
    print(" INTERACTIVE BACKUP (MIRROR) ".center(80, "="))
    print("=" * 80)

    config = load_config()

    for job in config.get("jobs", []):
        job_name = job.get("name", "Unnamed Job")
        source = job.get("source")
        destination = job.get("destination")

        if not source or not destination:
            print(f"\n[!] Skipping job '{job_name}': source or destination missing.")
            continue

        print(f"\n---> {job_name} <---")
        plan = create_sync_plan(source, destination)

        if plan is None:
            continue

        num_copy = len(plan["copy"])
        num_del_files = len(plan["delete_files"])
        num_del_dirs = len(plan["delete_dirs"])

        if num_copy == 0 and num_del_files == 0 and num_del_dirs == 0:
            print(" ✅ Everything is up to date. Nothing to do.")
            continue

        display_plan_summary(plan, source, destination)

        while True:
            response = (
                input("\nProceed with these changes? (y/n): ")
                .strip()
                .lower()
            )
            if response in ["y", "yes"]:
                print()
                execute_plan(plan)
                print("\n ✅ Job completed successfully!")
                break
            elif response in ["n", "no"]:
                print(" ❌ Aborted by user.")
                break
            else:
                print("Please answer with 'y' for yes or 'n' for no.")

    print("\n" + "=" * 80)
    print(" All jobs completed! ".center(80, "="))
    print("=" * 80)


if __name__ == "__main__":
    main()
