#!/usr/bin/env python3
"""
sdrive_index.py - learning index updater for the /find-on-sdrive skill.

The index lives at reference/sdrive-index.json and grows on every query so the
skill "compiles over time": it records which queries were run, which paths they
matched, and rolling tallies of how often a given folder or document type was a
hit. Future versions of the search ranker can read these tallies to boost paths
that have proven useful for similar queries.

This module is imported and called by scripts/find_on_sdrive.py after each query.
It can also be run directly to inspect or reset the index:

    python scripts/sdrive_index.py --show
    python scripts/sdrive_index.py --reset

HARD RULE: no long dashes anywhere in this file. Use a hyphen, a colon, or
restructure.
"""

import argparse
import datetime
import json
import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
INDEX_PATH = WORKSPACE / "reference" / "sdrive-index.json"

EMPTY_SCHEMA = {
    "schema_version": 1,
    "description": (
        "Learning index for the /find-on-sdrive skill. The skill appends one "
        "entry per query so it can learn which folder conventions surface which "
        "document types over time. Do not hand-edit while the skill is running."
    ),
    "updated": None,
    "queries": [],
    "path_hits": {},
    "type_hits": {},
}

# Keep the query log from growing without bound.
MAX_QUERIES = 500


def load_index() -> dict:
    """Load the index, seeding the empty schema if the file is missing or bad."""
    if not INDEX_PATH.exists():
        return dict(EMPTY_SCHEMA)
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(EMPTY_SCHEMA)
    # Make sure all top-level keys exist even if an older file is loaded.
    for key, default in EMPTY_SCHEMA.items():
        if key not in data:
            data[key] = default if not isinstance(default, (list, dict)) else type(default)()
    return data


def save_index(data: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _parent_folder_label(full_path: str) -> str:
    """Reduce a full path to the project sub-folder it lives under.

    e.g. '...\\501 - Northbridge Constructions...\\08 QA\\Level 2\\cert.pdf' -> '08 QA/Level 2'.
    Falls back to the immediate parent folder name if no numbered folder is found.
    """
    parts = Path(full_path).parts
    # Locate the project root folder (matches "[number] - ...") then take the
    # next two segments: the numbered sub-folder and (if present) the scope folder.
    proj_idx = None
    for i, part in enumerate(parts):
        if re.match(r"^\d{2,3}\s*-\s", part):
            proj_idx = i
            break
    if proj_idx is not None and proj_idx + 1 < len(parts) - 1:
        sub = parts[proj_idx + 1]
        scope = parts[proj_idx + 2] if proj_idx + 2 < len(parts) - 1 else ""
        return f"{sub}/{scope}".rstrip("/")
    return Path(full_path).parent.name


def record_query(query: str, project: str, doc_type: str, matches: list) -> dict:
    """Append a query record and update the rolling hit tallies.

    matches is a list of dicts each with at least a "path" key (the full match
    path). project, doc_type and query are the parsed inputs. Returns the saved
    index dict.
    """
    data = load_index()

    matched_paths = [m.get("path", "") for m in matches if m.get("path")]

    entry = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "query": query,
        "project": project or None,
        "doc_type": doc_type or None,
        "match_count": len(matched_paths),
        "top_matches": matched_paths[:10],
    }

    data["queries"].append(entry)
    if len(data["queries"]) > MAX_QUERIES:
        data["queries"] = data["queries"][-MAX_QUERIES:]

    # Roll up which project sub-folders matched, so the ranker can learn.
    for path in matched_paths:
        label = _parent_folder_label(path)
        if label:
            data["path_hits"][label] = data["path_hits"].get(label, 0) + 1

    # Roll up by detected document type.
    if doc_type and matched_paths:
        data["type_hits"][doc_type] = data["type_hits"].get(doc_type, 0) + len(matched_paths)

    data["updated"] = entry["timestamp"]
    save_index(data)
    return data


def main():
    parser = argparse.ArgumentParser(description="Inspect or reset the S: drive learning index.")
    parser.add_argument("--show", action="store_true", help="Print a summary of the index")
    parser.add_argument("--reset", action="store_true", help="Reset the index to the empty schema")
    args = parser.parse_args()

    if args.reset:
        save_index(dict(EMPTY_SCHEMA))
        print(f"Index reset: {INDEX_PATH}")
        return

    data = load_index()
    print(f"Index: {INDEX_PATH}")
    print(f"  Updated: {data.get('updated')}")
    print(f"  Queries logged: {len(data.get('queries', []))}")
    print("  Top folder hits:")
    for label, count in sorted(data.get("path_hits", {}).items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {count:>4}  {label}")
    print("  Type hits:")
    for label, count in sorted(data.get("type_hits", {}).items(), key=lambda kv: -kv[1]):
        print(f"    {count:>4}  {label}")


if __name__ == "__main__":
    main()
