#!/usr/bin/env python3
"""
dunsteel_monday.py - Shared Monday.com API helper for Dunsteel scripts.

One place for the Monday API contract so no script hardcodes a token or
re-implements the call. Loads the API key from the workspace-root .env under the
dotted key ``MONDAY.COM_API_KEY`` (python-dotenv is NOT installed, so we parse
.env ourselves). The token is sent raw in the Authorization header (Monday
convention, no "Bearer" prefix) with API-Version 2024-01.

Usage:
    from dunsteel_monday import MondayClient
    m = MondayClient()
    cols = m.get_columns(YOUR_TOOLBOX_BOARD_ID)
    item_id = m.create_item(YOUR_TOOLBOX_BOARD_ID, "topics", "Incoming form answer", {
        "date": {"date": "2026-06-08"},
        "long_text": {"text": "Agenda ..."},
    })
    m.change_column_values(YOUR_TOOLBOX_BOARD_ID, item_id, {"status_col": {"label": "PDF Created"}})

HARD RULE: no long dashes anywhere in this file. Use a hyphen, a colon, or
restructure.
"""

import json
import urllib.request
import urllib.error
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
ENV_PATH = WORKSPACE / ".env"
MONDAY_URL = "https://api.monday.com/v2"
API_VERSION = "2024-01"
ENV_KEY = "MONDAY.COM_API_KEY"


def load_monday_token(env_path: Path = ENV_PATH) -> str:
    """Read MONDAY.COM_API_KEY from .env. The key name contains dots, which is
    fine: we split on the first '=' only. Raises if the key is missing/empty."""
    if not env_path.exists():
        raise RuntimeError(f"No .env at {env_path}; cannot find {ENV_KEY}.")
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip() == ENV_KEY:
            val = val.strip().strip('"').strip("'")
            if not val:
                break
            return val
    raise RuntimeError(
        f"{ENV_KEY} not found (or empty) in {env_path}. "
        "Add it to .env (raw Monday API token, no quotes needed)."
    )


class MondayError(RuntimeError):
    """Raised when the Monday API returns an errors array."""


class MondayClient:
    def __init__(self, token: str | None = None, timeout: int = 30):
        self.token = token or load_monday_token()
        self.timeout = timeout

    # --- core ------------------------------------------------------------
    def query(self, gql: str, variables: dict | None = None) -> dict:
        """Run a GraphQL query/mutation and return the ``data`` object.
        Raises MondayError on an errors array, urllib errors on transport."""
        body = json.dumps({"query": gql, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(
            MONDAY_URL,
            data=body,
            headers={
                "Authorization": self.token,  # raw, no Bearer
                "Content-Type": "application/json",
                "API-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise MondayError(f"HTTP {e.code} from Monday: {detail}") from e
        if "errors" in payload:
            raise MondayError(json.dumps(payload["errors"], indent=2))
        return payload.get("data", {})

    # --- reads -----------------------------------------------------------
    def get_columns(self, board_id: int) -> list[dict]:
        data = self.query(
            "query($b:[ID!]){boards(ids:$b){columns{id title type settings_str}}}",
            {"b": [str(board_id)]},
        )
        boards = data.get("boards") or []
        return boards[0]["columns"] if boards else []

    def get_board_name(self, board_id: int) -> str:
        data = self.query(
            "query($b:[ID!]){boards(ids:$b){name}}", {"b": [str(board_id)]}
        )
        boards = data.get("boards") or []
        return boards[0]["name"] if boards else ""

    def board_items(self, board_id: int, page_limit: int = 100, max_items: int = 5000) -> list[dict]:
        """Return all items on a board as [{id, name, columns:{col_id: text}}].

        Pages through items_page with a cursor (Monday caps a page at 500). Only
        the column id and its display text are pulled, which is enough to filter
        by a status label or a date string."""
        first = self.query(
            "query($b:[ID!],$l:Int!){boards(ids:$b){items_page(limit:$l){cursor "
            "items{id name column_values{id text}}}}}",
            {"b": [str(board_id)], "l": page_limit},
        )
        boards = first.get("boards") or []
        if not boards:
            return []
        page = boards[0]["items_page"]
        out, cursor = [], page.get("cursor")
        out.extend(page["items"])
        while cursor and len(out) < max_items:
            nxt = self.query(
                "query($c:String!,$l:Int!){next_items_page(cursor:$c,limit:$l){cursor "
                "items{id name column_values{id text}}}}",
                {"c": cursor, "l": page_limit},
            )
            np = nxt.get("next_items_page") or {}
            out.extend(np.get("items", []))
            cursor = np.get("cursor")
        return [
            {"id": it["id"], "name": it["name"],
             "columns": {cv["id"]: cv.get("text") for cv in it["column_values"]}}
            for it in out
        ]

    # --- writes ----------------------------------------------------------
    def create_item(
        self,
        board_id: int,
        group_id: str | None,
        item_name: str,
        column_values: dict | None = None,
    ) -> str:
        """Create an item. column_values is a dict keyed by column id; the value
        is the Monday JSON shape for that column type. Returns the new item id."""
        gql = (
            "mutation($b:ID!,$g:String,$n:String!,$cv:JSON){"
            " create_item(board_id:$b,group_id:$g,item_name:$n,column_values:$cv){id}}"
        )
        variables = {
            "b": str(board_id),
            "g": group_id,
            "n": item_name,
            "cv": json.dumps(column_values or {}),
        }
        data = self.query(gql, variables)
        return data["create_item"]["id"]

    def change_column_values(self, board_id: int, item_id: str, values: dict) -> str:
        gql = (
            "mutation($b:ID!,$i:ID!,$cv:JSON!){"
            " change_multiple_column_values(board_id:$b,item_id:$i,column_values:$cv){id}}"
        )
        variables = {"b": str(board_id), "i": str(item_id), "cv": json.dumps(values)}
        data = self.query(gql, variables)
        return data["change_multiple_column_values"]["id"]

    def create_column(
        self, board_id: int, title: str, column_type: str, settings: dict | None = None
    ) -> dict:
        """Create a column. column_type e.g. 'status' (single select), 'link',
        'text'. For a status column, pass settings={'labels': {'0':'A','1':'B'}}.
        Returns {id, title, type}."""
        gql = (
            "mutation($b:ID!,$t:String!,$ct:ColumnType!,$d:JSON){"
            " create_column(board_id:$b,title:$t,column_type:$ct,defaults:$d){id title type}}"
        )
        variables = {
            "b": str(board_id),
            "t": title,
            "ct": column_type,
            "d": json.dumps(settings) if settings else None,
        }
        data = self.query(gql, variables)
        return data["create_column"]


if __name__ == "__main__":
    # Smoke test: prints both safety boards' names + column counts.
    m = MondayClient()
    for bid in (YOUR_TOOLBOX_BOARD_ID, YOUR_SAFETY_AUDIT_BOARD_ID):
        cols = m.get_columns(bid)
        print(f"{bid}  {m.get_board_name(bid)!r}  ({len(cols)} columns)")
