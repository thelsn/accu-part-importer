from __future__ import annotations

import re
from pathlib import Path

PART_FOLDER_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
PART_NUMBER_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def parse_part_number(value: str) -> tuple[str, int] | None:
    text = (value or "").strip().upper().replace(" ", "")
    match = PART_NUMBER_RE.match(text)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def format_part_number(prefix: str, number: int, digits: int) -> str:
    prefix = (prefix or "TA").strip().upper()
    width = max(int(digits), 1)
    return f"{prefix}{number:0{width}d}"


def scan_existing_numbers(parts_root: str | Path, prefix: str) -> list[int]:
    root = Path(parts_root)
    prefix = (prefix or "TA").strip().upper()
    if not root.is_dir():
        return []
    found: list[int] = []
    try:
        entries = root.iterdir()
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        match = PART_FOLDER_RE.match(entry.name)
        if not match:
            continue
        if match.group(1).upper() != prefix:
            continue
        found.append(int(match.group(2)))
    found.sort()
    return found


def suggest_next_part_number(parts_root: str | Path, prefix: str, digits: int) -> str:
    existing = scan_existing_numbers(parts_root, prefix)
    nxt = (existing[-1] + 1) if existing else 1
    return format_part_number(prefix, nxt, digits)


def part_folder(parts_root: str | Path, part_number: str) -> Path:
    return Path(parts_root) / part_number.strip().upper()


CAD_EXTS = {".ipt", ".stp", ".step", ".igs", ".iges"}


def part_number_exists(parts_root: str | Path, part_number: str) -> bool:
    folder = part_folder(parts_root, part_number)
    if not folder.exists():
        return False
    if not folder.is_dir():
        return True
    try:
        next(folder.iterdir())
        return True
    except StopIteration:
        return True
    except OSError:
        return True


def existing_cad_files(folder: Path, part_number: str) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    found: list[Path] = []
    prefix = part_number.strip().upper()
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_file():
            continue
        name = entry.name.upper()
        if name.startswith(prefix) and entry.suffix.lower() in CAD_EXTS:
            found.append(entry)
    return found


def replace_existing_cad_files(folder: Path, part_number: str) -> list[Path]:
    removed: list[Path] = []
    for path in existing_cad_files(folder, part_number):
        path.unlink()
        removed.append(path)
    return removed
