import os
import re
import json
import shutil
import csv
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# -----------------------------
# CONSTANTS / GLOBAL SETTINGS
# -----------------------------
STATE_FILE = "abs_cleaner_state.json"
LOG_DIR_NAME = "_ABS_CLEANER_LOGS"

AUDIO_EXTS = {".m4b", ".m4a"}
MP3_EXT = ".mp3"


# -----------------------------
# STATE HELPERS
# -----------------------------
def load_state():
    if Path(STATE_FILE).exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


# -----------------------------
# UTILITY FUNCTIONS
# -----------------------------
def safe_name(name: str) -> str:
    if not isinstance(name, str):
        name = str(name)
    bad = '<>:"/\\|?*'
    for c in bad:
        name = name.replace(c, "")
    return name.strip()


def load_metadata(folder: Path):
    meta_path = folder / "metadata.json"
    if not meta_path.exists():
        return None, None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data, meta_path
    except Exception:
        return None, None


def extract_info(meta: dict):
    """Return (author, title, series_name or None, book_number or None)."""

    # Title
    title = meta.get("title") or "Unknown Title"

    # Author
    authors = meta.get("authors") or meta.get("author")
    author_name = "Unknown Author"
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, dict):
            author_name = first.get("name") or first.get("sort") or author_name
        else:
            author_name = str(first)
    elif isinstance(authors, dict):
        author_name = authors.get("name") or authors.get("sort") or author_name
    elif isinstance(authors, str):
        author_name = authors

    # Series and book number
    series_name = None
    book_num = None

    series = meta.get("series")
    if isinstance(series, list) and series:
        s0 = series[0]
        if isinstance(s0, dict):
            series_name = s0.get("name") or s0.get("title")
            seq = (
                s0.get("sequence")
                or s0.get("sequenceNumber")
                or s0.get("index")
            )
            if seq is not None:
                try:
                    book_num = int(float(seq))
                except Exception:
                    book_num = None
        else:
            series_name = str(s0)
    elif isinstance(series, dict):
        series_name = series.get("name") or series.get("title")
        seq = (
            series.get("sequence")
            or series.get("sequenceNumber")
            or series.get("index")
        )
        if seq is not None:
            try:
                book_num = int(float(seq))
            except Exception:
                book_num = None
    elif isinstance(series, str):
        series_name = series

    # Some ABS exports store sequence at root
    if book_num is None:
        seq = meta.get("seriesSequence")
        if seq is not None:
            try:
                book_num = int(float(seq))
            except Exception:
                book_num = None

    return (
        safe_name(author_name),
        safe_name(title),
        safe_name(series_name) if series_name else None,
        book_num,
    )


def normalize_series_and_book(series_name, book_num):
    """
    Fix cases like 'A Thousand Li #10' or 'Adventures on Brad #1':
    - strip the '#10' from the series name
    - infer book_num from that suffix if missing
    """

    if not series_name:
        return series_name, book_num

    # pattern: "Series Name #10"
    m = re.match(r"^(.*?)[\s_-]*#\s*(\d+)\s*$", series_name)
    if m:
        base = m.group(1).strip()
        num_str = m.group(2)
        suffix_num = int(num_str)
        # if book_num is missing, use suffix
        if book_num is None:
            book_num = suffix_num
        # if book_num exists but differs, keep book_num but still strip suffix from the name
        series_name = base

    return series_name, book_num


# -----------------------------
# SCAN LIBRARY
# -----------------------------
def scan_library(source_root: Path):
    """
    Walk source_root and find:
      - books: folders with metadata.json and at least one .m4b/.m4a
      - mp3_only: folders with mp3s but no m4b/m4a
    """
    books = []
    mp3_only = []

    for root, dirs, files in os.walk(source_root):
        root_path = Path(root)

        audio_files = [
            root_path / f for f in files if (root_path / f).suffix.lower() in AUDIO_EXTS
        ]
        mp3_files = [
            root_path / f for f in files if (root_path / f).suffix.lower() == MP3_EXT
        ]

        if audio_files:
            meta, meta_path = load_metadata(root_path)
            if meta:
                books.append(
                    {
                        "folder": root_path,
                        "audio": audio_files[0],
                        "meta": meta,
                        "meta_path": meta_path,
                    }
                )
        else:
            if mp3_files:
                mp3_only.append(root_path)

    return books, mp3_only


# -----------------------------
# BUILD COPY PLAN
# -----------------------------
def build_plan(books, dest_root: Path):
    plan = []
    for b in books:
        meta = b["meta"]
        audio = Path(b["audio"])
        meta_src = Path(b["meta_path"])

        author, title, series_name, book_num = extract_info(meta)

        # normalize series string like "Series #10"
        series_name, book_num = normalize_series_and_book(series_name, book_num)

        ext = audio.suffix.lower()

        if series_name:
            # Series structure:
            #   Author / Series / Book 10 / "Series Book 10.m4b"
            base = Path(author) / series_name
            if book_num:
                book_folder = f"Book {book_num:02d}"
                dest_folder = dest_root / base / book_folder
                dest_name = f"{series_name} Book {book_num:02d}{ext}"
            else:
                dest_folder = dest_root / base
                dest_name = f"{title}{ext}"
        else:
            # Standalone:
            #   Author / Title / "Title.m4b"
            dest_folder = dest_root / author / title
            dest_name = f"{title}{ext}"

        dst = dest_folder / dest_name
        plan.append(
            {
                "src": audio,
                "dst": dst,
                "meta_src": meta_src,
                "meta_dst": dest_folder / "metadata.json",
                "status": "PENDING",
                "note": "",
            }
        )

    return plan


# -----------------------------
# EXECUTE PLAN (COPY PHASE)
# -----------------------------
def execute_plan(plan, dry_run, skip_existing, overwrite_existing, progress):
    total = len(plan)
    if total == 0:
        return

    progress["maximum"] = total
    progress["value"] = 0
    progress.update_idletasks()

    for idx, p in enumerate(plan, start=1):
        progress["value"] = idx
        progress.update_idletasks()

        src = Path(p["src"])
        dst = Path(p["dst"])
        meta_src = Path(p["meta_src"])
        meta_dst = Path(p["meta_dst"])

        if not src.exists():
            p["status"] = "ERROR"
            p["note"] = "SOURCE MISSING"
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)

        exists = dst.exists()
        if dry_run:
            p["status"] = "DRY-RUN"
            p["note"] = "EXISTS" if exists else ""
            continue

        if exists and skip_existing and not overwrite_existing:
            p["status"] = "SKIPPED"
            p["note"] = "DEST EXISTS, SKIP"
            continue

        try:
            shutil.copy2(src, dst)

            if meta_src.exists():
                meta_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(meta_src, meta_dst)

            if exists and overwrite_existing:
                p["status"] = "COPIED"
                p["note"] = "OVERWRITE"
            else:
                p["status"] = "COPIED"
        except Exception as e:
            p["status"] = "ERROR"
            p["note"] = str(e)


# -----------------------------
# LOGGING
# -----------------------------
def write_logs(dest_root: Path, plan, mp3_only):
    log_dir = dest_root / LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)

    with open(log_dir / "results.txt", "w", encoding="utf-8") as f:
        for p in plan:
            f.write(f"{p['src']} -> {p['dst']} [{p['status']} {p['note']}]\n")

    with open(log_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Source", "Destination", "Status", "Note"])
        for p in plan:
            writer.writerow([p["src"], p["dst"], p["status"], p["note"]])

    with open(log_dir / "mp3_only.txt", "w", encoding="utf-8") as f:
        for folder in mp3_only:
            f.write(str(folder) + "\n")

    return log_dir


def move_mp3_folders(mp3_folders, target_root: Path, progress):
    moved = []
    skipped = []

    total = len(mp3_folders)
    progress["maximum"] = total
    progress["value"] = 0
    progress.update_idletasks()

    for idx, src in enumerate(mp3_folders, start=1):
        progress["value"] = idx
        progress.update_idletasks()

        src = Path(src)
        if not src.exists():
            skipped.append((str(src), "SOURCE NOT FOUND"))
            continue

        dst = target_root / src.name
        try:
            shutil.move(str(src), str(dst))
            moved.append((str(src), str(dst)))
        except Exception as e:
            skipped.append((str(src), f"ERROR: {e}"))

    return moved, skipped


def write_mp3_logs(log_dir: Path, moved, skipped):
    log_dir.mkdir(parents=True, exist_ok=True)

    with open(log_dir / "mp3_only_moved.txt", "w", encoding="utf-8") as f:
        f.write("MOVED FOLDERS\n")
        for src, dst in moved:
            f.write(f"{src} -> {dst}\n")
        f.write("\nSKIPPED FOLDERS\n")
        for src, reason in skipped:
            f.write(f"{src} [{reason}]\n")

    with open(log_dir / "mp3_only_moved.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Source", "Destination", "Status"])
        for src, dst in moved:
            writer.writerow([src, dst, "MOVED"])
        for src, reason in skipped:
            writer.writerow([src, "", reason])


# -----------------------------
# GUI
# -----------------------------
def main():
    state = load_state()

    root = tk.Tk()
    root.title("ABS Cleaner")

    frame = ttk.Frame(root, padding=10)
    frame.pack(fill="both", expand=True)

    src_var = tk.StringVar(value=state.get("last_source", ""))
    dst_var = tk.StringVar(value=state.get("last_destination", ""))

    mp3_folders = []

    # ---- Source / Destination ----
    def choose_source():
        path = filedialog.askdirectory(title="Select SOURCE AudiobookShelf Library")
        if path:
            src_var.set(path)
            state["last_source"] = path
            save_state(state)

    def choose_destination():
        path = filedialog.askdirectory(title="Select DESTINATION Clean Library")
        if path:
            dst_var.set(path)
            state["last_destination"] = path
            save_state(state)

    src_btn = ttk.Button(frame, text="Source", command=choose_source)
    dst_btn = ttk.Button(frame, text="Destination", command=choose_destination)
    src_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
    dst_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))

    frame.columnconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, textvariable=src_var, anchor="w").grid(
        row=1, column=0, sticky="w", pady=(2, 10)
    )
    ttk.Label(frame, textvariable=dst_var, anchor="w").grid(
        row=1, column=1, sticky="w", pady=(2, 10)
    )

    # ---- Options ----
    dry_run_var = tk.BooleanVar(value=True)
    skip_existing_var = tk.BooleanVar(value=True)
    overwrite_var = tk.BooleanVar(value=False)

    tk.Checkbutton(
        frame, text="Dry-run only (no copying)", variable=dry_run_var
    ).grid(row=2, column=0, columnspan=2, sticky="w")
    tk.Checkbutton(
        frame, text="Skip existing files", variable=skip_existing_var
    ).grid(row=3, column=0, columnspan=2, sticky="w")
    tk.Checkbutton(
        frame, text="Overwrite existing files (always)", variable=overwrite_var
    ).grid(row=4, column=0, columnspan=2, sticky="w")

    # ---- Progress ----
    progress = ttk.Progressbar(frame, length=500)
    progress.grid(row=5, column=0, columnspan=2, pady=(10, 5), sticky="ew")

    plan_status = tk.StringVar(value="No plan yet")
    ttk.Label(frame, textvariable=plan_status).grid(
        row=6, column=0, columnspan=2, pady=(0, 10)
    )

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=7, column=0, columnspan=2, pady=(0, 10))

    last_plan = {"books": [], "plan": [], "mp3": []}

    def build_current_plan():
        nonlocal mp3_folders
        src = src_var.get().strip()
        dst = dst_var.get().strip()

        if not src or not dst:
            messagebox.showerror("Error", "Please select both Source and Destination.")
            return None

        src_path = Path(src)
        dst_path = Path(dst)

        if not src_path.exists():
            messagebox.showerror("Error", f"Source does not exist:\n{src_path}")
            return None

        books, mp3_only = scan_library(src_path)
        plan = build_plan(books, dst_path)

        last_plan["books"] = books
        last_plan["plan"] = plan
        last_plan["mp3"] = mp3_only
        mp3_folders = mp3_only

        plan_status.set(
            f"Plan: {len(plan)} books, {len(mp3_only)} mp3-only folders found"
        )
        return plan

    def show_plan():
        plan = build_current_plan()
        if plan is None or not plan:
            return

        top = tk.Toplevel(root)
        top.title("Copy Plan")
        text = tk.Text(top, width=120, height=30)
        text.pack(fill="both", expand=True)

        for p in plan:
            text.insert(
                "end", f"{p['src']}\n  -> {p['dst']}  [{p['status']} {p['note']}]\n\n"
            )

        text.config(state="disabled")

    def run_copy():
        nonlocal mp3_folders

        plan = build_current_plan()
        if plan is None:
            return

        dry_run = dry_run_var.get()
        skip_existing = skip_existing_var.get()
        overwrite = overwrite_var.get()

        if not dry_run and not skip_existing and not overwrite:
            if not messagebox.askyesno(
                "Confirm",
                "You are about to copy files and overwrite behaviour is default.\n\n"
                "Proceed?",
            ):
                return

        progress["value"] = 0
        root.update_idletasks()

        execute_plan(plan, dry_run, skip_existing, overwrite, progress)

        dst_path = Path(dst_var.get().strip())
        log_dir = write_logs(dst_path, plan, mp3_folders)

        plan_status.set(
            f"Completed: {len(plan)} books processed, logs in {log_dir.name}"
        )

        if mp3_folders:
            mp3_label_var.set(
                f"MP3-only folders detected: {len(mp3_folders)} (see mp3_only.txt)"
            )
            mp3_frame.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        else:
            mp3_frame.grid_remove()

    ttk.Button(btn_frame, text="Show Plan", width=18, command=show_plan).pack(
        side="left", padx=10
    )
    ttk.Button(btn_frame, text="Copy Files", width=18, command=run_copy).pack(
        side="left", padx=10
    )

    # ---- MP3 SECTION ----
    mp3_frame = ttk.LabelFrame(frame, text="MP3-only Folder Handling", padding=10)
    mp3_label_var = tk.StringVar(value="")

    mp3_label = ttk.Label(mp3_frame, textvariable=mp3_label_var)
    mp3_label.grid(row=0, column=0, columnspan=2, sticky="w")

    mp3_progress = ttk.Progressbar(mp3_frame, length=500)
    mp3_progress.grid(row=1, column=0, columnspan=2, pady=(8, 8), sticky="ew")

    def move_mp3():
        nonlocal mp3_folders
        if not mp3_folders:
            messagebox.showinfo("No MP3 folders", "No MP3-only folders from last run.")
            return

        target = filedialog.askdirectory(title="Select destination for MP3 folders")
        if not target:
            return

        target_root = Path(target)
        mp3_progress["value"] = 0
        root.update_idletasks()

        moved, skipped = move_mp3_folders(mp3_folders, target_root, mp3_progress)

        dst_path = Path(dst_var.get().strip())
        log_dir = dst_path / LOG_DIR_NAME
        write_mp3_logs(log_dir, moved, skipped)

        messagebox.showinfo(
            "MP3 Move Complete",
            f"Moved: {len(moved)} folders\nSkipped: {len(skipped)}\n\nLogs written to:\n{log_dir}",
        )

    ttk.Button(mp3_frame, text="Move MP3-only folders", command=move_mp3).grid(
        row=2, column=0, columnspan=2, pady=(5, 0)
    )

    mp3_frame.grid_remove()

    root.mainloop()


if __name__ == "__main__":
    main()
