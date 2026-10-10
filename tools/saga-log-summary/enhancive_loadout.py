#!/usr/bin/env python3
"""Parse Saga logs for enhancive loadouts (inv enh / usage / RECALL).

Writes per-character JSON under ~/.config/saga/log-summaries/enhancives/
and regenerates enhancives.html for the local viewer.

Usage:
  enhancive_loadout.py                 # yesterday (nightly default)
  enhancive_loadout.py --date 2026-09-18
  enhancive_loadout.py --all           # scan every dated log
  enhancive_loadout.py --rebuild-index # HTML only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

LOG_ROOT = Path.home() / ".config/saga/logs"
SUMMARY_ROOT = Path.home() / ".config/saga/log-summaries"
ENH_ROOT = SUMMARY_ROOT / "enhancives"

FILE_RE = re.compile(
    r"^(?P<stem>.+) (?P<mm>\d{2})\.(?P<dd>\d{2})\.(?P<yyyy>\d{4}) - (?P<n>\d+)\.txt$"
)
TS_RE = re.compile(r"^\[(\d{1,2}:\d{2}:\d{2} [AP]M)\] ")
SESSION_RE = re.compile(r"^=== (?P<who>.+) — session started")

USAGE_HDR_RE = re.compile(r"^Your worn location usage is as follows:\s*$")
USAGE_ROW_RE = re.compile(
    r"^\s*(\d+/\d+)\s+(\d+/\d+)\s+(.+?)\s+(\d+)\s*$"
)
ENH_TOTAL_CMD_RE = re.compile(
    r"^>?inv(?:entory)?\s+enh(?:ancive)?s?\s+tot(?:al)?(?:\s+det(?:ails?)?)?\s*$",
    re.I,
)
ENH_TOTAL_START_RE = re.compile(r"^Stats:\s*$")
STAT_HEADER_RE = re.compile(r"^  (.+?): (\d+)/(\d+)\s*$")
SOURCE_RE = re.compile(r"^    \+(\d+): (.+)\s*$")
SECTION_RE = re.compile(r"^(Stats|Skills|Resources|Statistics):\s*$")
STATS_FOOTER_RE = re.compile(
    r"^  Enhancive Items: (\d+)\s*$|"
    r"^  Enhancive Properties: (\d+)\s*$|"
    r"^  Total Enhancive Amount: (\d+)\s*$"
)
UNKNOWN_RE = re.compile(r"an unknown source \(needs loresong\)", re.I)
RECALL_START_RE = re.compile(
    r"^As you recall .+?, you feel a faint resonating vibration from the (.+) in your hand",
    re.I,
)
RECALL_BOOST_RE = re.compile(
    r"^\s*It provides a boost of (\d+) to (.+?)\.\s*$"
)
RECALL_LEVEL_RE = re.compile(
    r"^\s*This enhancement may not be used by adventurers who have not trained (\d+) times\.\s*$"
)
RECALL_CHARGES_RE = re.compile(
    r"^\s*It seems to have (.+?) enhancive charges remaining\.\s*$"
)
RECALL_CRUMBLY_RE = re.compile(
    r"It will crumble into dust after its last enhancive charge", re.I
)
RECALL_ENH_HDR_RE = re.compile(r"^It is an enhancive item:\s*$")


def strip_line(raw: str) -> str:
    m = TS_RE.match(raw)
    return raw[m.end() :] if m else raw.rstrip("\n")


def parse_filename_date(name: str) -> date | None:
    m = FILE_RE.match(name)
    if not m:
        return None
    return date(int(m.group("yyyy")), int(m.group("mm")), int(m.group("dd")))


def char_key(folder: str) -> str:
    return folder.strip()


def slug_item(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def empty_state(character: str) -> dict:
    return {
        "character": character,
        "updated_at": None,
        "last_inventory_at": None,
        "last_usage_at": None,
        "statistics": {
            "items": 0,
            "properties": 0,
            "total_amount": 0,
        },
        "usage_slots": [],
        "categories": {},
        "items": {},
        "unknowns": [],
        "sources": [],
    }


def load_state(character: str) -> dict:
    ENH_ROOT.mkdir(parents=True, exist_ok=True)
    path = ENH_ROOT / f"{character.replace('/', '_')}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return empty_state(character)


def save_state(state: dict) -> Path:
    ENH_ROOT.mkdir(parents=True, exist_ok=True)
    safe = state["character"].replace("/", "_")
    path = ENH_ROOT / f"{safe}.json"
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return path


def files_for_date(day: date) -> list[tuple[str, Path]]:
    found = []
    if not LOG_ROOT.is_dir():
        return found
    needle = f"{day.month:02d}.{day.day:02d}.{day.year:04d}"
    for folder in sorted(LOG_ROOT.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        for path in sorted(folder.glob("*.txt")):
            if needle in path.name and parse_filename_date(path.name) == day:
                found.append((char_key(folder.name), path))
    return found


def all_dates() -> list[date]:
    days = set()
    if not LOG_ROOT.is_dir():
        return []
    for path in LOG_ROOT.rglob("*.txt"):
        d = parse_filename_date(path.name)
        if d:
            days.add(d)
    return sorted(days)


def parse_usage_block(lines: list[str], start: int) -> tuple[list[dict], int]:
    slots = []
    i = start + 1
    while i < len(lines):
        s = strip_line(lines[i]).rstrip()
        if not s or s.lstrip().startswith("FUNC"):
            i += 1
            continue
        m = USAGE_ROW_RE.match(s)
        if not m:
            break
        slots.append(
            {
                "func": m.group(1),
                "total": m.group(2),
                "location": m.group(3).strip(),
                "code": int(m.group(4)),
            }
        )
        i += 1
    return slots, i


def parse_enh_details_block(lines: list[str], start: int) -> tuple[dict, int]:
    """Parse from a 'Stats:' line that begins inv enh tot det output."""
    categories: dict[str, dict] = {}
    section = "Stats"
    categories[section] = {}
    current_stat = None
    stats = {"items": 0, "properties": 0, "total_amount": 0}
    i = start + 1
    while i < len(lines):
        s = strip_line(lines[i]).rstrip()
        if not s:
            i += 1
            continue
        if s.startswith("For fewer details") or s.startswith("For more details"):
            i += 1
            break
        if s.startswith(">") or s.startswith("--- Lich:") or s.startswith("[Thoughts]"):
            break
        sm = SECTION_RE.match(s)
        if sm:
            section = sm.group(1)
            if section == "Statistics":
                i += 1
                continue
            categories.setdefault(section, {})
            current_stat = None
            i += 1
            continue
        fm = STATS_FOOTER_RE.match(s)
        if fm:
            if fm.group(1):
                stats["items"] = int(fm.group(1))
            if fm.group(2):
                stats["properties"] = int(fm.group(2))
            if fm.group(3):
                stats["total_amount"] = int(fm.group(3))
            i += 1
            continue
        hm = STAT_HEADER_RE.match(s)
        if hm:
            current_stat = hm.group(1)
            categories.setdefault(section, {})[current_stat] = {
                "current": int(hm.group(2)),
                "cap": int(hm.group(3)),
                "sources": [],
            }
            i += 1
            continue
        xm = SOURCE_RE.match(s)
        if xm and current_stat:
            item = xm.group(2).strip()
            known = not bool(UNKNOWN_RE.search(item))
            categories[section][current_stat]["sources"].append(
                {
                    "bonus": int(xm.group(1)),
                    "item": item if known else None,
                    "known": known,
                    "note": None if known else "needs loresong",
                    "raw": item,
                }
            )
            i += 1
            continue
        # Non-detail totals-only dump ends quickly
        if s.startswith("Statistics:") or ENH_TOTAL_CMD_RE.match(s):
            break
        i += 1
    return {"categories": categories, "statistics": stats}, i


def parse_recall_block(lines: list[str], start: int, item_hint: str) -> tuple[dict | None, int]:
    """Parse RECALL output starting at the 'As you recall...' line."""
    item_name = item_hint.strip()
    bonuses = []
    crumbly = False
    charges_note = None
    in_enh = False
    pending_bonus = None
    i = start + 1
    while i < len(lines):
        s = strip_line(lines[i]).rstrip()
        if not s:
            i += 1
            continue
        if s.startswith(">") or s.startswith("--- Lich:") or s.startswith("[Thoughts]"):
            break
        if s.startswith("As you recall ") or s.startswith("Usage:"):
            break
        if RECALL_ENH_HDR_RE.match(s):
            in_enh = True
            i += 1
            continue
        if in_enh:
            bm = RECALL_BOOST_RE.match(s)
            if bm:
                pending_bonus = {
                    "amount": int(bm.group(1)),
                    "stat": bm.group(2).strip(),
                    "min_trains": None,
                }
                bonuses.append(pending_bonus)
                i += 1
                continue
            lm = RECALL_LEVEL_RE.match(s)
            if lm and pending_bonus is not None:
                pending_bonus["min_trains"] = int(lm.group(1))
                i += 1
                continue
            cm = RECALL_CHARGES_RE.match(s)
            if cm:
                charges_note = cm.group(1).strip()
                i += 1
                continue
            if RECALL_CRUMBLY_RE.search(s):
                crumbly = True
                i += 1
                continue
            if s.startswith("It ") and not s.startswith("It provides") and not s.startswith("It seems") and not s.startswith("It will crumble"):
                # left enhancive section
                if bonuses:
                    break
        i += 1
    if not bonuses:
        return None, i
    return {
        "name": item_name,
        "bonuses": bonuses,
        "crumbly": crumbly,
        "charges_note": charges_note,
    }, i


def apply_usage(state: dict, slots: list[dict], when: str, src: str) -> None:
    if not slots:
        return
    state["usage_slots"] = slots
    state["last_usage_at"] = when
    state["sources"].append({"type": "usage", "at": when, "file": src})


def apply_inventory(state: dict, payload: dict, when: str, src: str) -> None:
    cats = payload.get("categories") or {}
    # Prefer detailed dumps (have sources) over summary-only
    has_detail = any(
        (stat.get("sources") for sec in cats.values() for stat in sec.values())
    )
    if has_detail or not state.get("categories"):
        state["categories"] = cats
        state["statistics"] = payload.get("statistics") or state["statistics"]
        state["last_inventory_at"] = when
        # Rebuild attributed items from known sources
        items = state.setdefault("items", {})
        for section, stats in cats.items():
            for stat_name, info in stats.items():
                for src_row in info.get("sources", []):
                    if not src_row.get("known") or not src_row.get("item"):
                        continue
                    key = slug_item(src_row["item"])
                    entry = items.setdefault(
                        key,
                        {
                            "name": src_row["item"],
                            "bonuses": [],
                            "from_inventory": True,
                            "from_recall": False,
                        },
                    )
                    entry["name"] = src_row["item"]
                    entry["from_inventory"] = True
                    bonus = {
                        "stat": stat_name,
                        "amount": src_row["bonus"],
                        "section": section,
                    }
                    if bonus not in entry["bonuses"]:
                        # replace same-stat entry
                        entry["bonuses"] = [
                            b for b in entry["bonuses"] if b.get("stat") != stat_name
                        ]
                        entry["bonuses"].append(bonus)
        rebuild_unknowns(state)
        state["sources"].append({"type": "inv_enh_details", "at": when, "file": src})


def apply_recall(state: dict, recall: dict, when: str, src: str) -> None:
    key = slug_item(recall["name"])
    items = state.setdefault("items", {})
    entry = items.setdefault(
        key,
        {
            "name": recall["name"],
            "bonuses": [],
            "from_inventory": False,
            "from_recall": True,
        },
    )
    # Prefer longer/more complete name from recall vibration text
    if len(recall["name"]) >= len(entry.get("name") or ""):
        entry["name"] = recall["name"]
    entry["from_recall"] = True
    entry["crumbly"] = recall.get("crumbly", False)
    entry["charges_note"] = recall.get("charges_note")
    entry["recalled_at"] = when
    # Merge bonuses from recall (authoritative for what the piece does)
    by_stat = {b["stat"]: b for b in entry.get("bonuses", [])}
    for b in recall["bonuses"]:
        by_stat[b["stat"]] = {
            "stat": b["stat"],
            "amount": b["amount"],
            "min_trains": b.get("min_trains"),
            "section": by_stat.get(b["stat"], {}).get("section"),
        }
    entry["bonuses"] = list(by_stat.values())
    # Resolve unknowns in categories when this item's bonus matches an unknown
    resolve_unknowns_with_item(state, entry)
    rebuild_unknowns(state)
    state["sources"].append(
        {"type": "recall", "at": when, "file": src, "item": recall["name"]}
    )


def resolve_unknowns_with_item(state: dict, entry: dict) -> None:
    """If an unknown bonus matches a recalled item's amount+stat family, annotate it."""
    name = entry.get("name")
    if not name:
        return
    recalled = {(b["stat"].lower(), b["amount"]): b for b in entry.get("bonuses", [])}
    # Map inv enh names like "Agility (AGI)" / "Two-Handed Weapons Bonus" to recall "Agility Base" etc.
    for section, stats in (state.get("categories") or {}).items():
        for stat_name, info in stats.items():
            for src_row in info.get("sources", []):
                if src_row.get("known"):
                    continue
                amt = src_row["bonus"]
                # fuzzy: any recalled bonus with same amount whose stat tokens overlap
                for (rstat, ramt), rb in recalled.items():
                    if ramt != amt:
                        continue
                    if stat_tokens_match(stat_name, rstat):
                        src_row["known"] = True
                        src_row["item"] = name
                        src_row["note"] = "resolved via RECALL"
                        src_row["resolved_stat"] = rb["stat"]
                        break


def stat_tokens_match(inv_stat: str, recall_stat: str) -> bool:
    a = re.sub(r"[^a-z0-9]+", " ", inv_stat.lower()).split()
    b = re.sub(r"[^a-z0-9]+", " ", recall_stat.lower()).split()
    stop = {"bonus", "base", "ranks", "max", "a", "an", "the", "to"}
    a = [t for t in a if t not in stop and len(t) > 1]
    b = [t for t in b if t not in stop and len(t) > 1]
    if not a or not b:
        return False
    return bool(set(a) & set(b))


def rebuild_unknowns(state: dict) -> None:
    unknowns = []
    for section, stats in (state.get("categories") or {}).items():
        for stat_name, info in stats.items():
            for src_row in info.get("sources", []):
                if src_row.get("known"):
                    continue
                unknowns.append(
                    {
                        "section": section,
                        "stat": stat_name,
                        "bonus": src_row["bonus"],
                        "note": src_row.get("note") or "needs loresong",
                    }
                )
    state["unknowns"] = unknowns


def scan_file(character: str, path: Path, state: dict) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    hits = 0
    i = 0
    while i < len(lines):
        s = strip_line(lines[i]).rstrip()
        ts_m = TS_RE.match(lines[i])
        when = f"{parse_filename_date(path.name)} {ts_m.group(1)}" if ts_m and parse_filename_date(path.name) else (ts_m.group(1) if ts_m else path.name)

        if USAGE_HDR_RE.match(s):
            slots, ni = parse_usage_block(lines, i)
            if slots:
                apply_usage(state, slots, when, path.name)
                hits += 1
            i = ni
            continue

        # After inv enh tot det command, next Stats: starts the dump
        if ENH_TOTAL_CMD_RE.match(s):
            j = i + 1
            while j < len(lines) and not ENH_TOTAL_START_RE.match(strip_line(lines[j])):
                # skip blank / prompt
                sj = strip_line(lines[j]).strip()
                if sj.startswith(">") and not ENH_TOTAL_CMD_RE.match(sj):
                    break
                j += 1
            if j < len(lines) and ENH_TOTAL_START_RE.match(strip_line(lines[j])):
                payload, ni = parse_enh_details_block(lines, j)
                # Only apply if we got real detail sources or totals
                if payload["statistics"].get("total_amount") or any(
                    st.get("sources")
                    for sec in payload["categories"].values()
                    for st in sec.values()
                ):
                    apply_inventory(state, payload, when, path.name)
                    hits += 1
                i = ni
                continue

        # Bare Stats: that looks like enh dump (defensive)
        if ENH_TOTAL_START_RE.match(s) and i + 1 < len(lines):
            nxt = strip_line(lines[i + 1])
            if STAT_HEADER_RE.match(nxt):
                payload, ni = parse_enh_details_block(lines, i)
                if any(
                    st.get("sources")
                    for sec in payload["categories"].values()
                    for st in sec.values()
                ):
                    apply_inventory(state, payload, when, path.name)
                    hits += 1
                i = ni
                continue

        rm = RECALL_START_RE.match(s)
        if rm:
            recall, ni = parse_recall_block(lines, i, rm.group(1))
            if recall:
                apply_recall(state, recall, when, path.name)
                hits += 1
            i = ni
            continue

        i += 1
    if hits:
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return hits


def process_day(day: date) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    states: dict[str, dict] = {}
    for character, path in files_for_date(day):
        state = states.get(character) or load_state(character)
        states[character] = state
        counts[character] += scan_file(character, path, state)
    for character, state in states.items():
        if counts[character]:
            # keep last 30 source notes
            state["sources"] = state.get("sources", [])[-30:]
            save_state(state)
    return dict(counts)


def process_all() -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    # Chronological so later inventory wins
    states: dict[str, dict] = {}
    for day in all_dates():
        for character, path in files_for_date(day):
            state = states.get(character) or empty_state(character)
            states[character] = state
            totals[character] += scan_file(character, path, state)
    for character, state in states.items():
        if not totals.get(character):
            continue
        state["sources"] = state.get("sources", [])[-30:]
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)
    return dict(totals)


def load_all_states() -> list[dict]:
    ENH_ROOT.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(ENH_ROOT.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def render_html(states: list[dict]) -> str:
    payload = json.dumps(states)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Enhancive loadouts</title>
<style>
  :root {{
    --bg: #12110f; --card: #1c1a17; --ink: #e8e0d0; --muted: #9a8f7c;
    --accent: #c4a35a; --line: #3a342c; --warn: #c47a5a;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: "Iosevka", "JetBrains Mono", ui-monospace, monospace;
    background: var(--bg); color: var(--ink); line-height: 1.45; padding: 1.5rem;
  }}
  h1 {{ font-weight: 600; font-size: 1.4rem; color: var(--accent); margin: 0 0 .3rem; }}
  h2 {{ color: var(--accent); font-size: 1.1rem; margin: 1.4rem 0 .5rem; }}
  h3 {{ color: var(--muted); font-size: .95rem; margin: 1rem 0 .35rem; }}
  p.sub {{ color: var(--muted); margin: 0 0 1rem; font-size: .9rem; }}
  a {{ color: var(--accent); }}
  .toggles {{ display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 1rem; }}
  .toggles button {{
    background: var(--card); color: var(--ink); border: 1px solid var(--line);
    padding: .35rem .7rem; font: inherit; cursor: pointer;
  }}
  .toggles button.active {{ background: var(--accent); color: var(--bg); border-color: var(--accent); }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: .6rem; margin-bottom: 1rem; }}
  .card {{ background: var(--card); border: 1px solid var(--line); padding: .7rem .8rem; }}
  .card .k {{ color: var(--muted); font-size: .75rem; }}
  .card .v {{ font-size: 1.2rem; color: var(--accent); }}
  table {{ border-collapse: collapse; width: 100%; background: var(--card); margin-bottom: 1rem; }}
  th, td {{ border-bottom: 1px solid var(--line); padding: .4rem .55rem; text-align: left; vertical-align: top; }}
  th {{ color: var(--muted); font-weight: 500; font-size: .8rem; }}
  td.num {{ text-align: right; white-space: nowrap; }}
  .unknown {{ color: var(--warn); }}
  .muted {{ color: var(--muted); }}
  .note {{ color: var(--muted); font-size: .85rem; max-width: 70rem; }}
</style>
</head>
<body>
  <h1>Enhancive loadouts</h1>
  <p class="sub">
    From Saga logs: <code>inv enh tot det</code>, <code>inv usage enh</code>, and <code>RECALL</code>.
    Unknowns stay flagged until a loresong/RECALL attributes them.
    <a href="index.html">Daily hunt summaries</a>
  </p>
  <div class="toggles" id="chars"></div>
  <div id="summary" class="cards"></div>
  <div id="body"></div>
  <p class="note">
    FUNC/TOTAL columns are worn-location usage slots (functional vs total capacity).
    RECALL fills per-item bonuses; inventory totals remain the source of truth for caps and current sums.
  </p>
  <script type="application/json" id="data">{payload}</script>
  <script>
    const states = JSON.parse(document.getElementById("data").textContent);
    const KEY = "saga-enh-char";
    let selected = localStorage.getItem(KEY) || (states[0] && states[0].character) || "";
    if (selected && !states.find(s => s.character === selected))
      selected = states[0] ? states[0].character : "";

    function esc(s) {{
      return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    }}
    function state() {{ return states.find(s => s.character === selected); }}

    function renderChars() {{
      const box = document.getElementById("chars");
      box.innerHTML = "";
      states.forEach(s => {{
        const b = document.createElement("button");
        b.textContent = (s.character || "").replace(" - Prime", "");
        b.className = s.character === selected ? "active" : "";
        b.onclick = () => {{ selected = s.character; localStorage.setItem(KEY, selected); render(); }};
        box.appendChild(b);
      }});
    }}

    function renderSummary(s) {{
      const st = s.statistics || {{}};
      const unk = (s.unknowns || []).length;
      const items = Object.keys(s.items || {{}}).length;
      const slots = (s.usage_slots || []).filter(u => {{
        const [a,b] = (u.total || "0/0").split("/").map(Number);
        return a > 0;
      }}).length;
      document.getElementById("summary").innerHTML = [
        ["Items", st.items || items],
        ["Properties", st.properties || 0],
        ["Total amount", st.total_amount || 0],
        ["Unknown sources", unk],
        ["Locations used", slots],
        ["Updated", (s.last_inventory_at || s.updated_at || "—")]
      ].map(([k,v]) => `<div class="card"><div class="k">${{esc(k)}}</div><div class="v">${{esc(v)}}</div></div>`).join("");
    }}

    function renderUsage(s) {{
      const rows = (s.usage_slots || []).map(u =>
        `<tr><td class="num">${{esc(u.func)}}</td><td class="num">${{esc(u.total)}}</td><td>${{esc(u.location)}}</td><td class="num">${{esc(u.code)}}</td></tr>`
      ).join("");
      return `<h2>Usage slots</h2>
        <table><thead><tr><th>FUNC</th><th>TOTAL</th><th>Location</th><th>Code</th></tr></thead>
        <tbody>${{rows || "<tr><td colspan=4 class=muted>No inv usage enh dump yet.</td></tr>"}}</tbody></table>`;
    }}

    function renderCategories(s) {{
      let html = "";
      const cats = s.categories || {{}};
      for (const [section, stats] of Object.entries(cats)) {{
        html += `<h2>${{esc(section)}}</h2>`;
        for (const [stat, info] of Object.entries(stats)) {{
          const src = (info.sources || []).map(r => {{
            if (r.known && r.item)
              return `<tr><td class="num">+${{esc(r.bonus)}}</td><td>${{esc(r.item)}}${{r.note ? ` <span class="muted">(${{esc(r.note)}})</span>` : ""}}</td></tr>`;
            return `<tr><td class="num unknown">+${{esc(r.bonus)}}</td><td class="unknown">unknown (needs loresong)</td></tr>`;
          }}).join("");
          html += `<h3>${{esc(stat)}} <span class="muted">${{esc(info.current)}}/${{esc(info.cap)}}</span></h3>
            <table><thead><tr><th>Bonus</th><th>Source</th></tr></thead><tbody>${{src}}</tbody></table>`;
        }}
      }}
      if (!html) html = `<p class="muted">No inv enh tot det dump yet. Run that in-game (or wait for nightly scan after you do).</p>`;
      return html;
    }}

    function renderItems(s) {{
      const entries = Object.values(s.items || {{}}).sort((a,b) => (a.name||"").localeCompare(b.name||""));
      if (!entries.length) return `<h2>Items</h2><p class="muted">No attributed or recalled items yet.</p>`;
      let html = `<h2>Items</h2><table><thead><tr><th>Item</th><th>Bonuses</th><th>Notes</th></tr></thead><tbody>`;
      for (const it of entries) {{
        const bonuses = (it.bonuses || []).map(b =>
          `+${{esc(b.amount)}} ${{esc(b.stat)}}${{b.min_trains != null ? ` (min ${{esc(b.min_trains)}})` : ""}}`
        ).join("<br>");
        const notes = [];
        if (it.from_recall) notes.push("RECALL");
        if (it.from_inventory) notes.push("inventory");
        if (it.crumbly) notes.push("crumbly");
        if (it.charges_note) notes.push(it.charges_note + " charges");
        html += `<tr><td>${{esc(it.name)}}</td><td>${{bonuses || "—"}}</td><td class="muted">${{esc(notes.join(" · ") || "—")}}</td></tr>`;
      }}
      html += `</tbody></table>`;
      return html;
    }}

    function renderUnknowns(s) {{
      const u = s.unknowns || [];
      if (!u.length) return `<h2>Unknowns</h2><p class="muted">None — every bonus is attributed.</p>`;
      const rows = u.map(x =>
        `<tr><td>${{esc(x.section)}}</td><td>${{esc(x.stat)}}</td><td class="num unknown">+${{esc(x.bonus)}}</td><td class="unknown">${{esc(x.note)}}</td></tr>`
      ).join("");
      return `<h2>Unknowns</h2>
        <table><thead><tr><th>Section</th><th>Stat</th><th>Bonus</th><th>Note</th></tr></thead>
        <tbody>${{rows}}</tbody></table>`;
    }}

    function render() {{
      renderChars();
      const s = state();
      if (!s) {{
        document.getElementById("summary").innerHTML = "";
        document.getElementById("body").innerHTML = "<p class=muted>No enhancive data yet.</p>";
        return;
      }}
      renderSummary(s);
      document.getElementById("body").innerHTML =
        renderUnknowns(s) + renderUsage(s) + renderCategories(s) + renderItems(s);
    }}
    render();
  </script>
</body>
</html>
"""


def rebuild_index() -> Path:
    states = load_all_states()
    ENH_ROOT.mkdir(parents=True, exist_ok=True)
    html_path = SUMMARY_ROOT / "enhancives.html"
    html_path.write_text(render_html(states), encoding="utf-8")
    index = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "characters": [s.get("character") for s in states],
    }
    (ENH_ROOT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    # Link from daily index if present
    daily = SUMMARY_ROOT / "index.html"
    if daily.is_file():
        text = daily.read_text(encoding="utf-8")
        if "enhancives.html" not in text:
            text = text.replace(
                '<p class="sub">',
                '<p class="sub"><a href="enhancives.html">Enhancive loadouts</a> · ',
                1,
            )
            daily.write_text(text, encoding="utf-8")
    return html_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Saga enhancive loadout summarizer")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--all", action="store_true", help="Scan every dated log")
    ap.add_argument("--rebuild-index", action="store_true")
    args = ap.parse_args(argv)

    if args.rebuild_index and not args.all and not args.date:
        print(rebuild_index())
        return 0

    if args.all:
        counts = process_all()
        for char, n in sorted(counts.items()):
            print(f"{char}: {n} enhancive event(s)")
        if not counts:
            print("No enhancive dumps found in logs.", file=sys.stderr)
        print(rebuild_index())
        return 0

    day = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    counts = process_day(day)
    for char, n in sorted(counts.items()):
        print(f"{day.isoformat()} {char}: {n} event(s)")
    if not counts:
        print(f"{day.isoformat()}: no enhancive dumps found")
    print(rebuild_index())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
