#!/usr/bin/env python3
"""
Notion Database -> Markdown sync.

Reads every page in the configured Notion database and writes one
Markdown file per page into logs/ (filename = page's date property,
fallback = page created_time). Existing files are overwritten only
when content actually changes.

Environment variables:
    NOTION_TOKEN          Internal Integration token (secret_...)
    NOTION_DATABASE_ID    32-char database id from the database URL

Usage:
    python scripts/notion_to_md.py            # write/update files
    python scripts/notion_to_md.py --dry-run  # show planned changes only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


# ---------- HTTP layer ----------

class NotionError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
    for attempt in range(5):
        resp = requests.request(method, url, headers=_headers(token), timeout=30, **kwargs)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "2"))
            print(f"  [rate-limit] waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            raise NotionError(
                f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()
    raise NotionError(f"{method} {url} -> gave up after 5 attempts")


def query_database(token: str, database_id: str) -> list[dict[str, Any]]:
    url = f"{NOTION_API}/databases/{database_id}/query"
    results: list[dict[str, Any]] = []
    payload: dict[str, Any] = {"page_size": 100}
    while True:
        data = _request("POST", url, token, json=payload)
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]
    return results


def get_block_children(token: str, block_id: str) -> list[dict[str, Any]]:
    url = f"{NOTION_API}/blocks/{block_id}/children"
    results: list[dict[str, Any]] = []
    params: dict[str, Any] = {"page_size": 100}
    while True:
        data = _request("GET", url, token, params=params)
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        params["start_cursor"] = data["next_cursor"]
    return results


# ---------- Property extraction ----------

def extract_title(props: dict[str, Any]) -> str:
    for prop in props.values():
        if prop.get("type") == "title":
            return _rich_text_plain(prop.get("title", []))
    return ""


def extract_date(props: dict[str, Any]) -> str | None:
    for prop in props.values():
        if prop.get("type") == "date":
            date_obj = prop.get("date")
            if date_obj and date_obj.get("start"):
                return date_obj["start"][:10]
    return None


def extract_frontmatter(props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, prop in props.items():
        ptype = prop.get("type")
        if ptype == "title":
            out[name] = _rich_text_plain(prop.get("title", []))
        elif ptype == "rich_text":
            out[name] = _rich_text_plain(prop.get("rich_text", []))
        elif ptype == "select":
            sel = prop.get("select")
            out[name] = sel["name"] if sel else None
        elif ptype == "multi_select":
            out[name] = [s["name"] for s in prop.get("multi_select", [])]
        elif ptype == "date":
            date_obj = prop.get("date")
            out[name] = date_obj["start"] if date_obj else None
        elif ptype == "checkbox":
            out[name] = prop.get("checkbox", False)
        elif ptype == "number":
            out[name] = prop.get("number")
        elif ptype == "url":
            out[name] = prop.get("url")
        elif ptype == "status":
            st = prop.get("status")
            out[name] = st["name"] if st else None
    return out


# ---------- Rich text -> markdown ----------

def _rich_text_plain(rich: list[dict[str, Any]]) -> str:
    return "".join(r.get("plain_text", "") for r in rich)


def _rich_text_md(rich: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for r in rich:
        text = r.get("plain_text", "")
        if not text:
            continue
        ann = r.get("annotations", {})
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("bold"):
            text = f"**{text}**"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        href = r.get("href")
        if href:
            text = f"[{text}]({href})"
        parts.append(text)
    return "".join(parts)


# ---------- Block -> markdown ----------

def blocks_to_md(token: str, blocks: list[dict[str, Any]], depth: int = 0) -> str:
    lines: list[str] = []
    indent = "  " * depth
    for block in blocks:
        btype = block.get("type")
        data = block.get(btype, {})
        rt = data.get("rich_text", [])
        text = _rich_text_md(rt)

        if btype == "paragraph":
            lines.append(f"{indent}{text}" if text else "")
        elif btype == "heading_1":
            lines.append(f"{indent}# {text}")
        elif btype == "heading_2":
            lines.append(f"{indent}## {text}")
        elif btype == "heading_3":
            lines.append(f"{indent}### {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"{indent}- {text}")
        elif btype == "numbered_list_item":
            lines.append(f"{indent}1. {text}")
        elif btype == "to_do":
            checked = data.get("checked", False)
            mark = "x" if checked else " "
            lines.append(f"{indent}- [{mark}] {text}")
        elif btype == "quote":
            lines.append(f"{indent}> {text}")
        elif btype == "callout":
            icon = data.get("icon", {}).get("emoji", "")
            lines.append(f"{indent}> {icon} {text}".rstrip())
        elif btype == "code":
            lang = data.get("language", "")
            lines.append(f"{indent}```{lang}")
            lines.append(_rich_text_plain(rt))
            lines.append(f"{indent}```")
        elif btype == "divider":
            lines.append(f"{indent}---")
        elif btype == "image":
            img = data
            url = ""
            if img.get("type") == "external":
                url = img["external"]["url"]
            elif img.get("type") == "file":
                url = img["file"]["url"]
            caption = _rich_text_plain(img.get("caption", []))
            lines.append(f"{indent}![{caption}]({url})")
        elif btype == "bookmark":
            lines.append(f"{indent}[{data.get('url', '')}]({data.get('url', '')})")
        elif btype == "toggle":
            lines.append(f"{indent}<details><summary>{text}</summary>")
            if block.get("has_children"):
                children = get_block_children(token, block["id"])
                lines.append(blocks_to_md(token, children, depth))
            lines.append(f"{indent}</details>")
        else:
            if text:
                lines.append(f"{indent}{text}")

        if (
            block.get("has_children")
            and btype not in {"toggle"}
            and btype is not None
        ):
            children = get_block_children(token, block["id"])
            child_md = blocks_to_md(token, children, depth + 1)
            if child_md:
                lines.append(child_md)

    return "\n".join(lines)


# ---------- Page rendering ----------

def _yaml_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(_yaml_value(x) for x in v) + "]"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace('"', '\\"')
    return f'"{s}"'


def build_frontmatter(page: dict[str, Any], fm: dict[str, Any]) -> str:
    base = {
        "notion_id": page["id"],
        "last_edited_time": page.get("last_edited_time"),
        "url": page.get("url"),
    }
    merged = {**base, **{k: v for k, v in fm.items() if v not in (None, "", [])}}
    lines = ["---"]
    for k, v in merged.items():
        lines.append(f"{k}: {_yaml_value(v)}")
    lines.append("---")
    return "\n".join(lines)


def render_page(token: str, page: dict[str, Any]) -> tuple[str, str]:
    props = page.get("properties", {})
    title = extract_title(props) or "Untitled"
    date = extract_date(props) or page.get("created_time", "")[:10]
    fm = extract_frontmatter(props)

    blocks = get_block_children(token, page["id"])
    body_md = blocks_to_md(token, blocks)

    frontmatter = build_frontmatter(page, fm)
    content = f"{frontmatter}\n\n# {title}\n\n{body_md}\n"

    filename = f"{date}.md" if date else f"{page['id']}.md"
    return filename, content


# ---------- File writing ----------

def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_if_changed(path: Path, content: str, dry_run: bool) -> str:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if _digest(existing) == _digest(content):
            return "unchanged"
        action = "update"
    else:
        action = "create"
    if dry_run:
        return f"would-{action}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return action


# ---------- Main ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Notion database to Markdown files.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned changes without writing files")
    args = parser.parse_args()

    token = os.environ.get("NOTION_TOKEN")
    database_id = os.environ.get("NOTION_DATABASE_ID")

    if not token:
        print("ERROR: NOTION_TOKEN is not set", file=sys.stderr)
        return 2
    if not database_id:
        print("ERROR: NOTION_DATABASE_ID is not set", file=sys.stderr)
        return 2

    print(f"[1/3] Querying database {database_id[:8]}...")
    try:
        pages = query_database(token, database_id)
    except NotionError as e:
        print(f"ERROR querying database: {e}", file=sys.stderr)
        return 3
    print(f"      -> {len(pages)} pages")

    print("[2/3] Rendering pages")
    summary: dict[str, int] = {"create": 0, "update": 0, "unchanged": 0, "would-create": 0, "would-update": 0}
    for i, page in enumerate(pages, 1):
        try:
            filename, content = render_page(token, page)
        except NotionError as e:
            print(f"  [{i}/{len(pages)}] FAILED: {e}", file=sys.stderr)
            return 4
        path = LOGS_DIR / filename
        result = write_if_changed(path, content, args.dry_run)
        summary[result] = summary.get(result, 0) + 1
        print(f"  [{i}/{len(pages)}] {result:14s} {filename}")

    print("[3/3] Summary")
    for k, v in summary.items():
        if v:
            print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
