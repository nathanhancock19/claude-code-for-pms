#!/usr/bin/env python3
r"""
find_on_sdrive.py - worker behind the /find-on-sdrive skill.

Search the Dunsteel S: drive for project artefacts (drawings, variations, bolt
certs, schedules, SWMS, markups) using a natural-language query, knowing the
standard project folder conventions:

    S:\Operations\01 Current Project\[number] - [head contractor] - [project name]\
        03 Workshop Drawings\For Approval\ ...
        04 Variations\V[NN] [title]\ ...
        08 QA\[scope]\ ...           (Level 2..7, Stair 1, HV Room, etc.)
        13 Schedules\ ...
        15 WHS and Environmental\ ...

Pipeline:
  1. Parse the query: optional project number, a document type, and scope/level
     keywords (e.g. "level 4", "stair 1", "V08", "cooling tower").
  2. Map the document type to the folders worth searching (the convention map).
  3. Glob those folders (recursively where it makes sense), plus a project-wide
     fallback walk for free-text queries with no clear type.
  4. Rank matches by filename relevance and modification time.
  5. Print the top 5 to 10 matches with full paths.
  6. Log the query and matched paths to reference/sdrive-index.json via
     scripts/sdrive_index.py so the skill learns over time.

The S: drive is READ-ONLY for this script. It never creates, moves, or deletes
anything on S:. The only file it writes is the workspace learning index.

Outlook-based queries are handled by a separate skill. This script does not
touch email.

Usage:
    python scripts/find_on_sdrive.py 501 latest delivery schedule
    python scripts/find_on_sdrive.py 501 V08 supporting docs
    python scripts/find_on_sdrive.py 504 bolt cert level 4
    python scripts/find_on_sdrive.py cooling tower handrail markup
    python scripts/find_on_sdrive.py 501 latest variation --json
    python scripts/find_on_sdrive.py 501 programme --limit 8

Flags:
    --limit N   Max results to print (default 10, clamped to 1..25).
    --json      Print results as JSON (for programmatic callers).
    --no-log    Do not update the learning index (dry run).

HARD RULE: no long dashes anywhere in this file or its output. Use a hyphen, a
colon, or restructure.
"""

import datetime
import json
import re
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
S_CURRENT_PROJECTS = Path(r"S:\Operations\01 Current Project")

# How many files a recursive walk will look at before giving up, so a stray
# query never hangs on a huge tree.
MAX_WALK_FILES = 6000

# Files we never want to surface.
NOISE_NAMES = {"thumbs.db", "desktop.ini"}
NOISE_SUFFIXES = {".lnk", ".tmp"}


# ---------------------------------------------------------------------------
# Project lookup
# ---------------------------------------------------------------------------

def find_project_folder(number: str):
    """Return the S: drive folder Path matching '[number] -*', or None."""
    if not S_CURRENT_PROJECTS.exists():
        return None
    try:
        matches = [p for p in S_CURRENT_PROJECTS.iterdir()
                   if p.is_dir() and re.match(rf"^{re.escape(number)}\s*-", p.name)]
    except OSError:
        return None
    return matches[0] if matches else None


def resolve_subfolder(project_folder, sub):
    """Resolve a convention subfolder name tolerantly within a project.

    Folder names vary between projects (e.g. "13 Schedules" on 501 but
    "13 Program" on 509). The convention map uses one canonical name; this
    resolves it to whatever the project actually uses by matching on the numeric
    prefix ("13 ", "08 ", etc). Returns the matched Path, or project_folder/sub
    unchanged if nothing better is found (iter_files handles a missing path).
    """
    exact = project_folder / sub
    if exact.exists():
        return exact
    m = re.match(r"^(\d{2})\s", sub)
    if not m:
        return exact
    prefix = m.group(1)
    try:
        for p in project_folder.iterdir():
            if p.is_dir() and re.match(rf"^{prefix}\s*[-\s]", p.name):
                return p
    except OSError:
        pass
    return exact


# ---------------------------------------------------------------------------
# Query parsing
# ---------------------------------------------------------------------------

# Document type map. Each entry: keyword triggers -> search plan.
# A plan is a list of (subfolder, recursive, name_globs) tuples. subfolder is
# relative to the project root. name_globs are case-insensitive fragments any of
# which a filename should contain to be a strong match (used for ranking, not a
# hard filter, so nothing is missed).
TYPE_MAP = [
    {
        "type": "delivery-schedule",
        "triggers": ["delivery schedule", "delivery"],
        "plan": [("13 Schedules", True, ["delivery"])],
    },
    {
        "type": "programme",
        "triggers": ["programme", "program", "schedule", "fabrication schedule", "forecast"],
        "plan": [("13 Schedules", True, ["programme", "program", "schedule", "forecast", "dates"])],
    },
    {
        "type": "variation",
        "triggers": ["variation", "variations"],
        "plan": [("04 Variations", True, [])],
    },
    {
        "type": "bolt-cert",
        "triggers": ["bolt cert", "bolt tightening", "bolt certificate", "tightening certificate"],
        "plan": [("08 QA", True, ["bolt", "tight"])],
    },
    {
        "type": "itp",
        "triggers": ["itp", "inspection test plan"],
        "plan": [("08 QA", True, ["itp"])],
    },
    {
        "type": "itr",
        "triggers": ["itr"],
        "plan": [("08 QA", True, ["itr"])],
    },
    {
        "type": "verticality",
        "triggers": ["verticality", "vertical report"],
        "plan": [("08 QA", True, ["verticality", "vertical"])],
    },
    {
        "type": "traceability",
        "triggers": ["traceability"],
        "plan": [("08 QA", True, ["traceability"])],
    },
    {
        "type": "weld",
        "triggers": ["weld", "weld map", "ut report", "visual weld"],
        "plan": [("08 QA", True, ["weld"])],
    },
    {
        "type": "gal-cert",
        "triggers": ["gal cert", "galvanising", "galvanizing", "gal certificate"],
        "plan": [("08 QA", True, ["gal"])],
    },
    {
        "type": "drawing-for-approval",
        "triggers": ["for approval", "approval drawing", "shop drawing for approval"],
        "plan": [("03 Workshop Drawings\\For Approval", True, [])],
    },
    {
        "type": "drawing-for-fabrication",
        "triggers": ["for fabrication", "fabrication drawing"],
        "plan": [("03 Workshop Drawings\\For Fabrication", True, [])],
    },
    {
        "type": "rfi",
        "triggers": ["rfi"],
        "plan": [("03 Workshop Drawings\\RFI", True, ["rfi"])],
    },
    {
        "type": "drawing",
        "triggers": ["drawing", "drawings", "marking plan", "shop drawing", "mp1", "ga "],
        "plan": [("03 Workshop Drawings", True, [])],
    },
    {
        "type": "swms",
        "triggers": ["swms", "safe work method"],
        "plan": [("15 WHS and Environmental", True, ["swms"])],
    },
    {
        "type": "whs",
        "triggers": ["whs", "prestart", "pre-start", "rescue plan", "safety audit", "environmental"],
        "plan": [("15 WHS and Environmental", True, [])],
    },
    {
        "type": "markup",
        "triggers": ["markup", "mark up", "mark-up"],
        "plan": [("12 Markups", True, []), ("04 Variations", True, ["markup", "mark up"])],
    },
    {
        "type": "scope",
        "triggers": ["scope"],
        "plan": [("05 Scopes", True, [])],
    },
    {
        "type": "costing",
        "triggers": ["costing", "quote", "ctc", "budget"],
        "plan": [("09 Costing-Quotes", True, [])],
    },
    {
        "type": "delivery-docket",
        "triggers": ["docket", "delivery docket"],
        "plan": [("06 Delivery Dockets", True, [])],
    },
    {
        "type": "contract",
        "triggers": ["contract", "contractural", "contractual"],
        "plan": [("02 Contractural", True, [])],
    },
    {
        "type": "completion",
        "triggers": ["o&m", "o and m", "operations and maintenance", "completion", "handover"],
        "plan": [("10 Completion Documents", True, []), ("08 QA", True, ["o&m", "o & m"])],
    },
]

# Scope keywords found inside 08 QA (used to filter QA-type searches).
SCOPE_PATTERNS = [
    (r"\blevel\s*(\d+)\b", lambda m: [f"level {m.group(1)}", f"l{m.group(1)}"]),
    (r"\bl(\d+)\b", lambda m: [f"level {m.group(1)}", f"l{m.group(1)}"]),
    (r"\bstair\s*(\d+)\b", lambda m: [f"stair {m.group(1)}"]),
    (r"\bhv\s*room\b", lambda m: ["hv room", "hv"]),
]

# Recency words.
RECENCY_WORDS = {"latest", "newest", "recent", "most recent", "current", "last"}

STOPWORDS = {
    "the", "a", "an", "of", "for", "to", "on", "in", "and", "supporting",
    "docs", "doc", "documents", "file", "files", "find", "show", "me", "get",
    "please", "with",
}


def parse_query(tokens):
    """Parse the query tokens into project, doc_type, scope keywords, free terms.

    Returns a dict with: project, want_latest, doc_type, plan, scope_keywords,
    variation_tag (e.g. 'V08' or '__latest__'), free_terms.
    """
    raw = " ".join(tokens).strip()
    lowered = raw.lower()

    # Project number: a standalone 3-digit token, preferably the first token.
    project = None
    if tokens and re.fullmatch(r"\d{3}", tokens[0]):
        project = tokens[0]
    else:
        m = re.search(r"\b(\d{3})\b", raw)
        if m:
            project = m.group(1)

    want_latest = any(w in lowered for w in RECENCY_WORDS)

    # Variation tag: explicit V08, or "latest variation".
    variation_tag = None
    vm = re.search(r"\bv\s*0*(\d{1,2})\b", lowered)
    if vm:
        variation_tag = f"V{int(vm.group(1)):02d}"

    # Document type: first matching trigger wins (longest trigger first for
    # specificity, e.g. "delivery schedule" beats "schedule").
    doc_type = None
    plan = None
    best_len = -1
    for entry in TYPE_MAP:
        for trig in entry["triggers"]:
            if trig in lowered and len(trig) > best_len:
                doc_type = entry["type"]
                plan = entry["plan"]
                best_len = len(trig)
    # If a variation tag was found but no type, treat as variation.
    if variation_tag and doc_type is None:
        doc_type = "variation"
        plan = [("04 Variations", True, [])]

    # Scope keywords (Level / Stair / HV Room).
    scope_keywords = []
    for pat, fn in SCOPE_PATTERNS:
        m = re.search(pat, lowered)
        if m:
            scope_keywords.extend(fn(m))

    # Free terms: everything left after stripping project, recency, type
    # triggers, scope words, and stopwords. Used for relevance ranking and for
    # the project-wide fallback search.
    used = set()
    if project:
        used.add(project)
    consumed = lowered
    for entry in TYPE_MAP:
        for trig in entry["triggers"]:
            if trig in consumed:
                consumed = consumed.replace(trig, " ")
    for pat, _ in SCOPE_PATTERNS:
        consumed = re.sub(pat, " ", consumed)
    consumed = re.sub(r"\bv\s*0*\d{1,2}\b", " ", consumed)
    free = []
    for w in re.findall(r"[a-z0-9&]+", consumed):
        if w in STOPWORDS or w in RECENCY_WORDS or w == project:
            continue
        if len(w) < 2:
            continue
        free.append(w)

    return {
        "raw": raw,
        "project": project,
        "want_latest": want_latest,
        "doc_type": doc_type,
        "plan": plan,
        "scope_keywords": scope_keywords,
        "variation_tag": variation_tag,
        "free_terms": free,
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def is_noise(path: Path) -> bool:
    name = path.name.lower()
    if name in NOISE_NAMES or name.startswith("~$"):
        return True
    if path.suffix.lower() in NOISE_SUFFIXES:
        return True
    return False


def iter_files(root: Path, recursive: bool, cap: int):
    """Yield files under root, up to cap, skipping noise. Robust to OS errors."""
    count = 0
    if not root.exists():
        return
    try:
        walker = root.rglob("*") if recursive else root.glob("*")
        for p in walker:
            try:
                if p.is_dir():
                    continue
            except OSError:
                continue
            if is_noise(p):
                continue
            yield p
            count += 1
            if count >= cap:
                return
    except OSError:
        return


def collect_candidates(parsed, project_folder):
    """Build the candidate file set per the parsed query and the convention map.

    Returns a list of unique Path objects. If a project folder is known and a
    plan exists, search only those sub-folders. If no type matched, fall back to
    a capped project-wide walk. If no project is known, search across all
    current projects (capped, type-folders only where a plan exists).
    """
    seen = {}

    def add(paths):
        for p in paths:
            seen[str(p)] = p

    plan = parsed["plan"]

    if project_folder is not None:
        if plan:
            for sub, recursive, _globs in plan:
                add(iter_files(resolve_subfolder(project_folder, sub), recursive, MAX_WALK_FILES))
            # Variation tag: also pull the specific V[NN] folder contents.
            if parsed["variation_tag"] and parsed["variation_tag"] != "__latest__":
                var_root = project_folder / "04 Variations"
                if var_root.exists():
                    try:
                        for d in var_root.iterdir():
                            if d.is_dir() and d.name.upper().startswith(parsed["variation_tag"]):
                                add(iter_files(d, True, MAX_WALK_FILES))
                    except OSError:
                        pass
        else:
            # No clear document type. Named artefacts (e.g. "cooling tower",
            # "handrail markup") almost always live in the drawing, markup,
            # tender, variation or scope folders. Search those first: it is far
            # faster than walking the whole project tree and usually more
            # relevant. Only fall back to a capped full walk if they yield
            # nothing.
            preferred = (
                "03 Workshop Drawings", "12 Markups", "01 Tender Information",
                "04 Variations", "05 Scopes", "13 Schedules",
            )
            for sub in preferred:
                add(iter_files(resolve_subfolder(project_folder, sub), True, MAX_WALK_FILES))
            if not seen:
                add(iter_files(project_folder, True, MAX_WALK_FILES))
        return list(seen.values())

    # A project number was given but no matching folder was found. Do not crawl
    # every other project: the query was scoped to one project. Return empty so
    # the caller reports "no folder found" and suggests a broader search.
    if parsed["project"]:
        return []

    # No project number given. Search every current project, but only the planned
    # sub-folders when a type matched (to keep it fast); otherwise a shallow,
    # capped walk per project root.
    if not S_CURRENT_PROJECTS.exists():
        return []
    try:
        projects = [p for p in S_CURRENT_PROJECTS.iterdir()
                    if p.is_dir() and re.match(r"^\d{2,3}\s*-", p.name)]
    except OSError:
        return []
    per_project_cap = max(400, MAX_WALK_FILES // max(len(projects), 1))
    for proj in projects:
        if plan:
            for sub, recursive, _globs in plan:
                add(iter_files(proj / sub, recursive, per_project_cap))
        else:
            # Free-text, no project: walk the markup and drawing folders plus a
            # shallow root pass, capped, since a full cross-project rglob is huge.
            for sub in ("12 Markups", "03 Workshop Drawings", "04 Variations"):
                add(iter_files(proj / sub, True, per_project_cap // 3))
    return list(seen.values())


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def variation_number_in_path(path: Path):
    """Return the integer variation number if the path is under a V[NN] folder."""
    for part in path.parts:
        m = re.match(r"^V\s*0*(\d{1,2})\b", part, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def score_candidate(path: Path, parsed, now_ts):
    """Return a relevance score. Higher is better.

    Combines filename keyword hits, scope match, recency words, plan glob hints,
    and modification time. Modification time is a secondary signal so a strong
    name match always outranks a merely-recent unrelated file.
    """
    name = path.name.lower()
    full = str(path).lower()
    score = 0.0
    reasons = []

    # Plan name-glob hints (e.g. "delivery", "bolt"/"tight").
    if parsed["plan"]:
        for _sub, _rec, globs in parsed["plan"]:
            for g in globs:
                if g in name:
                    score += 6
                    reasons.append(f"name~{g}")

    # Scope keywords (Level 2, Stair 1, HV Room) must hit somewhere in the path.
    for kw in parsed["scope_keywords"]:
        if kw in full:
            score += 10
            reasons.append(f"scope~{kw}")

    # Free terms: each hit in the filename is worth more than in the path.
    for term in parsed["free_terms"]:
        if term in name:
            score += 5
            reasons.append(f"term~{term}")
        elif term in full:
            score += 2

    # Variation tag exact folder hit.
    if parsed["variation_tag"]:
        vnum = variation_number_in_path(path)
        target = re.match(r"V(\d+)", parsed["variation_tag"])
        if vnum is not None and target and vnum == int(target.group(1)):
            score += 12
            reasons.append("variation-folder")

    # "latest variation": prefer the highest-numbered variation folder.
    if parsed["want_latest"] and parsed["doc_type"] == "variation":
        vnum = variation_number_in_path(path)
        if vnum is not None:
            score += vnum * 2  # higher V number scores higher
            reasons.append(f"V{vnum:02d}")

    # Prefer the primary deliverable file types over loose images.
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".xlsx", ".docx", ".mpp", ".doc", ".xls"}:
        score += 1.5
    elif suffix in {".jpg", ".jpeg", ".png", ".heic"}:
        score -= 1.0

    # Modification time as a tie-breaker / recency signal.
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    age_days = max((now_ts - mtime) / 86400.0, 0)
    # Recency contributes up to ~5 points, more if the query asked for "latest".
    recency_weight = 3.0 if parsed["want_latest"] else 1.2
    recency_score = recency_weight * max(0.0, 5.0 - (age_days / 90.0))
    score += recency_score

    return score, mtime, reasons


def rank(candidates, parsed):
    now_ts = datetime.datetime.now().timestamp()
    scored = []
    for p in candidates:
        s, mtime, reasons = score_candidate(p, parsed, now_ts)
        scored.append((s, mtime, p, reasons))
    # Sort by score, then by modification time (newest first).
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def fmt_mtime(path: Path):
    try:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return "????-??-??"


def build_results(scored, limit):
    results = []
    for s, mtime, p, reasons in scored[:limit]:
        results.append({
            "path": str(p),
            "name": p.name,
            "modified": fmt_mtime(p),
            "score": round(s, 1),
            "why": reasons,
        })
    return results


def print_human(parsed, project_folder, results):
    print(f"Query: {parsed['raw']}")
    pieces = []
    if parsed["project"]:
        pieces.append(f"project {parsed['project']}")
    if parsed["doc_type"]:
        pieces.append(f"type: {parsed['doc_type']}")
    if parsed["scope_keywords"]:
        pieces.append("scope: " + ", ".join(dict.fromkeys(parsed["scope_keywords"])))
    if parsed["variation_tag"]:
        pieces.append(f"variation: {parsed['variation_tag']}")
    if parsed["want_latest"]:
        pieces.append("latest requested")
    if pieces:
        print("Parsed: " + " | ".join(pieces))
    if project_folder is not None:
        print(f"Project folder: {project_folder}")
    elif parsed["project"]:
        print(f"No S: drive folder found matching '{parsed['project']} -*'.")
    print("")

    if not results:
        print("No matches found.")
        suggest_broader(parsed)
        return

    print(f"Top {len(results)} matches:")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['name']}")
        print(f"     {r['path']}")
        print(f"     modified {r['modified']}  (score {r['score']})")
    print("")
    print("To open the top match (Windows):")
    print(f'  start "" "{results[0]["path"]}"')


def suggest_broader(parsed):
    print("")
    print("Try broadening the search:")
    if parsed["project"] and parsed["doc_type"]:
        print(f"  - Drop the document type: /find-on-sdrive {parsed['project']} "
              + " ".join(parsed["free_terms"] or ["<keywords>"]))
    if parsed["scope_keywords"]:
        print("  - Remove the scope filter (Level / Stair / HV Room).")
    if not parsed["project"]:
        print("  - Add the project number as the first word, e.g. '501 ...'.")
    print("  - Use fewer, more distinctive keywords from the file name.")
    print("  - For emails, use the Outlook search skill instead (this skill only searches the S: drive).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def extract_flags(argv):
    """Pull --limit, --json and --no-log out of argv wherever they appear.

    Everything that is not a recognised flag is returned as the query terms.
    Done manually because the query is free text (any number of words) and an
    argparse REMAINDER positional would swallow the flags as query words.
    Returns (terms, limit, as_json, no_log).
    """
    terms = []
    limit = 10
    as_json = False
    no_log = False
    i = 0
    while i < len(argv):
        tok = argv[i]
        low = tok.lower()
        if low == "--json":
            as_json = True
        elif low == "--no-log":
            no_log = True
        elif low == "--limit":
            if i + 1 < len(argv):
                try:
                    limit = int(argv[i + 1])
                except ValueError:
                    pass
                i += 1
        elif low.startswith("--limit="):
            try:
                limit = int(tok.split("=", 1)[1])
            except ValueError:
                pass
        elif tok:
            terms.append(tok)
        i += 1
    return terms, limit, as_json, no_log


def main():
    tokens, limit_raw, as_json, no_log = extract_flags(sys.argv[1:])

    if not tokens:
        print("Usage: python scripts/find_on_sdrive.py <project?> <what you are looking for>")
        print("Example: python scripts/find_on_sdrive.py 501 latest delivery schedule")
        print("Flags: --limit N (1..25), --json, --no-log")
        sys.exit(2)

    limit = max(1, min(25, limit_raw))

    if not S_CURRENT_PROJECTS.exists():
        msg = (f"S: drive not reachable at {S_CURRENT_PROJECTS}. "
               "Connect to the Dunsteel network and try again.")
        if as_json:
            print(json.dumps({"error": msg, "results": []}, indent=2))
        else:
            print(msg)
        sys.exit(1)

    parsed = parse_query(tokens)
    project_folder = find_project_folder(parsed["project"]) if parsed["project"] else None

    candidates = collect_candidates(parsed, project_folder)
    scored = rank(candidates, parsed)
    results = build_results(scored, limit)

    if as_json:
        print(json.dumps({
            "query": parsed["raw"],
            "project": parsed["project"],
            "doc_type": parsed["doc_type"],
            "project_folder": str(project_folder) if project_folder else None,
            "results": results,
        }, indent=2, ensure_ascii=False))
    else:
        print_human(parsed, project_folder, results)

    # Update the learning index (after the search, never on S:).
    if not no_log:
        try:
            import sdrive_index
        except ImportError:
            sys.path.insert(0, str(WORKSPACE / "scripts"))
            import sdrive_index
        try:
            sdrive_index.record_query(
                query=parsed["raw"],
                project=parsed["project"] or "",
                doc_type=parsed["doc_type"] or "",
                matches=results,
            )
        except Exception as e:  # never let logging break a successful search
            if not as_json:
                print(f"\n(note: could not update learning index: {e})")


if __name__ == "__main__":
    main()
