#!/usr/bin/env python3
"""SNAKE://ARENA hub generator.

Regenerates the contestant card grid and the model counts in index.html from
snakes.json. Each card's *title* is read from the game file's own <title> and
its *size* is read from disk, so neither can drift. Only curated facts (display
name, counter slug, added date, warning, title override) live in the manifest.

Cards are ordered newest-first by their "added" date, alphabetical within a
batch; the latest additions therefore sit at the top of the page.

Usage:
    python3 build.py            # regenerate index.html from snakes.json
    python3 build.py --dry-run  # show the diff without writing
    python3 build.py --check    # exit non-zero if index.html is out of sync
    python3 build.py --seed     # (re)create snakes.json from the current page
"""
from __future__ import annotations

import argparse
import difflib
import html as html_mod
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
MANIFEST = ROOT / "snakes.json"

CARDS_RE = re.compile(r"(<!-- cards:start -->\n)(.*?)(\n<!-- cards:end -->)", re.S)
WORDS_RE = re.compile(r"<!--n:words-->.*?<!--/n:words-->")
DIGITS_RE = re.compile(r"<!--n:digits-->.*?<!--/n:digits-->")
TITLE_RE = re.compile(r"<title>.*?</title>", re.S)
HUB_COMMENT_RE = re.compile(r"hub for [\w-]+ single-file snake clones")
KICKER_RE = re.compile(r"protocol v\d+")

CARD_BLOCK_RE = re.compile(r"<article\b[^>]*>.*?</article>", re.S)
CARD_WARN_RE = re.compile(r'<a class="card-warn"[^>]*>⚠\s*(.*?)</a>', re.S)

_FIRST = [
    "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
    "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
    "Sixteen", "Seventeen", "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety"]


def number_words(n: int) -> str:
    """38 -> 'Thirty-Eight' (title case, hyphenated)."""
    if n < 20:
        return _FIRST[n]
    if n < 100:
        tens, rest = divmod(n, 10)
        return _TENS[tens] + (f"-{_FIRST[rest]}" if rest else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _FIRST[hundreds] + " Hundred" + (f" {number_words(rest)}" if rest else "")
    return str(n)


def natural_key(text: str):
    """Case-insensitive natural sort key: 'Qwen 3.8-27B' < 'Qwen 3.8-35B'."""
    key = []
    for token in re.findall(r"\d+|\D+", text):
        if token.isdigit():
            key.append((0, int(token), ""))
        else:
            key.append((1, 0, token.casefold()))
    return key


def game_title(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    if not match:
        return None
    return html_mod.unescape(re.sub(r"\s+", " ", match.group(1))).strip()


def size_kb(path: Path) -> int:
    return path.stat().st_size // 1024


def parsed_cards(text: str):
    """Read the card blocks currently in index.html (used by --seed)."""
    entries = []
    for block in CARD_BLOCK_RE.findall(text):
        tag = block[: block.index(">") + 1]

        def attr(name):
            found = re.search(rf'{name}="([^"]*)"', tag)
            return html_mod.unescape(found.group(1)) if found else None

        warn = CARD_WARN_RE.search(block)
        entries.append({
            "file": attr("data-game"),
            "model": attr("data-model"),
            "counter": attr("data-counter"),
            "card_title": attr("data-title"),
            "warn": html_mod.unescape(re.sub(r"\s+", " ", warn.group(1)).strip()) if warn else None,
        })
    return entries


def resolve_title(entry: dict):
    """Curated override wins; otherwise ask the game file itself."""
    if entry.get("title"):
        return entry["title"]
    return game_title(ROOT / entry["file"])


def render_card(entry: dict, index: int, title: str) -> str:
    game = html_mod.escape(entry["file"], quote=True)
    attr_title = html_mod.escape(title, quote=True)
    attr_model = html_mod.escape(entry["model"], quote=True)
    counter = html_mod.escape(entry["counter"], quote=True)
    added = f' data-added="{html_mod.escape(entry["added"], quote=True)}"' if entry.get("added") else ""
    lines = [
        f'<article class="card" tabindex="0" role="button" data-game="{game}" '
        f'data-title="{attr_title}" data-model="{attr_model}" data-counter="{counter}"{added}>',
        f'    <div class="card-index">{index:02d}</div>',
        f'    <div class="card-model">{html_mod.escape(entry["model"])}</div>',
        f'    <div class="card-title">{html_mod.escape(title)}</div>',
    ]
    if entry.get("warn"):
        lines.append(
            f'    <a class="card-warn" href="{game}" target="_blank" rel="noopener">'
            f'⚠ {html_mod.escape(entry["warn"])}</a>'
        )
    lines.append(
        f'    <div class="card-meta"><span>size <b>{size_kb(ROOT / entry["file"])} KB</b></span>'
        f'<span>self-contained</span><span>plays <b class="plays-count">—</b></span></div>'
    )
    lines.append('    <button class="play-btn" tabindex="-1">▶ Play</button>')
    lines.append('  </article>')
    return "\n".join(lines)


def validate(entries: list[dict]) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    seen_files, seen_counters = {}, {}
    for entry in entries:
        for field in ("file", "model", "counter"):
            if not entry.get(field):
                errors.append(f"entry missing '{field}': {entry!r}")
        path = ROOT / entry["file"]
        if not path.exists():
            errors.append(f"missing file: {entry['file']}")
        if entry["file"] in seen_files:
            errors.append(f"duplicate file: {entry['file']}")
        seen_files[entry["file"]] = entry["model"]
        if entry["counter"] in seen_counters:
            errors.append(
                f"duplicate counter '{entry['counter']}' "
                f"({seen_counters[entry['counter']]} and {entry['model']}) — would double-count"
            )
        seen_counters[entry["counter"]] = entry["model"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.get("added") or ""):
            warnings.append(f"{entry['model']}: no valid \"added\" date — it will sort last")
        if resolve_title(entry) is None:
            errors.append(f"no <title> found in {entry['file']} (set a \"title\" override)")

    for path in sorted(ROOT.glob("*.html")):
        if path.name != "index.html" and path.name not in seen_files:
            warnings.append(f"orphan game file not on the hub: {path.name}")
    return errors, warnings


def build_document(text: str, entries: list[dict]):
    # newest first, alphabetical within a batch (the second sort is stable)
    ordered = sorted(entries, key=lambda e: natural_key(e["model"]))
    ordered = sorted(ordered, key=lambda e: e.get("added") or "", reverse=True)
    count = len(ordered)

    cards = "\n\n".join(
        render_card(entry, i + 1, resolve_title(entry))
        for i, entry in enumerate(ordered)
    )
    region = CARDS_RE.search(text)
    if not region:
        raise SystemExit("index.html is missing the <!-- cards:start --> / <!-- cards:end --> markers")
    text = text[: region.start(2)] + cards + text[region.end(2):]

    words = number_words(count)
    text = TITLE_RE.sub(f"<title>SNAKE://ARENA — {words} Neural Snakes</title>", text, count=1)
    text = WORDS_RE.sub(f"<!--n:words-->{words}<!--/n:words-->", text)
    text = DIGITS_RE.sub(f"<!--n:digits-->{count}<!--/n:digits-->", text)
    text = HUB_COMMENT_RE.sub(f"hub for {words.lower()} single-file snake clones", text)
    text = KICKER_RE.sub(f"protocol v{count}", text)
    return text, count


def seed() -> None:
    entries = parsed_cards(INDEX.read_text(encoding="utf-8"))
    manifest = []
    for entry in entries:
        item = {"file": entry["file"], "model": entry["model"], "counter": entry["counter"]}
        derived = game_title(ROOT / entry["file"])
        if derived != entry["card_title"]:
            item["title"] = entry["card_title"]
            print(f"  title override kept for {entry['file']}: {entry['card_title']!r}")
        if entry["warn"]:
            item["warn"] = entry["warn"]
        manifest.append(item)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST.name} with {len(manifest)} entries")


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the SNAKE://ARENA hub.")
    parser.add_argument("--seed", action="store_true", help="rebuild snakes.json from the current index.html")
    parser.add_argument("--dry-run", action="store_true", help="print the diff without writing")
    parser.add_argument("--check", action="store_true", help="exit non-zero if index.html is out of sync")
    args = parser.parse_args()

    if args.seed:
        seed()
        return 0

    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    errors, warnings = validate(entries)
    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    original = INDEX.read_text(encoding="utf-8")
    generated, count = build_document(original, entries)

    if args.check:
        if generated != original:
            print("index.html is out of sync — run: python3 build.py", file=sys.stderr)
            return 1
        print(f"index.html is in sync ({count} contestants)")
        return 0

    if args.dry_run:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile="index.html (current)", tofile="index.html (generated)",
        )
        sys.stdout.writelines(diff)
        return 0

    if generated == original:
        print(f"index.html already up to date ({count} contestants)")
        return 0
    INDEX.write_text(generated, encoding="utf-8")
    print(f"wrote index.html ({count} contestants)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
