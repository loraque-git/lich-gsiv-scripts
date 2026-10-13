#!/usr/bin/env python3
"""Parse Saga session logs into daily JSON summaries and a static viewer.

Usage:
  saga_log_summary.py                 # yesterday (nightly default)
  saga_log_summary.py --date 2026-09-09
  saga_log_summary.py --all           # every date present in logs
  saga_log_summary.py --rebuild-index # viewer only, no parse
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
VIEWER_DIR = Path(__file__).resolve().parent

# Weekly account-wide loot-cap ceiling (subscriber figure). ledger.lic's ~lootcap
# column is estimated used amount only; remaining = WEEKLY_LOOT_CAP − used.
# Simu has not published the real number; this is a working guess only.
WEEKLY_LOOT_CAP = 2_000_000

FILE_RE = re.compile(
    r"^(?P<stem>.+) (?P<mm>\d{2})\.(?P<dd>\d{2})\.(?P<yyyy>\d{4}) - (?P<n>\d+)\.txt$"
)
TS_RE = re.compile(r"^\[(\d{1,2}:\d{2}:\d{2} [AP]M)\] ")
SESSION_RE = re.compile(
    r"^=== (?P<who>.+) — session started (?P<when>.+) ==="
)
AS_RE = re.compile(r"AS: \+")

# Player EBP evade/dodge (not creature evades, not "remain steadfast").
PLAYER_EVADE_RE = re.compile(
    r"^(?:"
    r"By amazing chance, you evade the attack!"
    r"|You move at the last moment to evade the attack!"
    r"|You evade the attack "
    r"|You stumble dazedly, somehow managing to evade the attack!"
    r"|You barely dodge the attack!"
    r"|You dodge just in the nick of time!"
    r"|You skillfully dodge the attack!"
    r"|You manage to dodge "
    r")"
)
# Shield Block only (Rowling-style). Weapon "fend off" / "block with maul" is Parry.
SHIELD_NOUN_RE = r"(?:buckler|shield|targe|aegis)\b"
PLAYER_BLOCK_RE = re.compile(
    r"^(?:"
    r"You barely manage to block the attack with your .*" + SHIELD_NOUN_RE +
    r"|You stumble dazedly, (?:somehow managing to|but manage to) block the attack with your .*"
    + SHIELD_NOUN_RE
    + r"|You easily block the attack with your .*"
    + SHIELD_NOUN_RE
    + r"|You block the bolt with your .*"
    + SHIELD_NOUN_RE
    + r")"
)
PLAYER_PARRY_RE = re.compile(
    r"^(?:"
    r"You gauge the attack and expertly parry"
    r"|You easily parry the attack"
    r"|You roll to one side and parry"
    r"|You barely manage to fend off the attack"
    r"|You barely manage to block the attack"  # weapon; shield lines already matched above
    r"|You stumble dazedly, (?:somehow managing to|but manage to) block the attack"
    r"|You easily block the attack"
    r")"
)
OUTGOING_RE = re.compile(
    r"^You (?:take aim and )?(?:swing|jab|punch|kick|grapple|attack|lunge|"
    r"hurl|throw|fire|cast|gesture|incant|try to|attempt|spin around|"
    r"slash|thrust|feint|pound|bash)"
)
BLINK_CAST_RE = re.compile(r"focusing your mind upon the Blink spell")
BLINK_UP_RE = re.compile(
    r"A momentary eruption of starlit darkness engulfs you"
)
BLINK_TRIGGER_RE = re.compile(
    r"You find yourself moved several feet from your original position!"
)
BLINK_END_RE = re.compile(
    r"splinters away with a sound like shattering crystal"
)
PROMPT_RE = re.compile(r"^[!R]?>")

NUM = r"([\d,]+)"
SEARCH_RE = re.compile(r"^You search the (.+)\.$")
SEARCH_THROUGH_RE = re.compile(r"^You search through")
GATHER_COINS_RE = re.compile(rf"^You gather the remaining {NUM} coins\.$")
GATHER_BOX_RE = re.compile(
    rf"^You gather the remaining {NUM} coins from inside"
)
SKIN_RE = re.compile(r"^You skinned the (.+), yielding (.+)\.$")
BOTCH_RE = re.compile(r"^You botched the job\.\s+The (.+) is ruined\.$")
DUST_MOTE_RE = re.compile(
    r"scintillating mote of Gemstone Dust on the ground and gather"
)
WEALTH_DUST_RE = re.compile(rf"^You are carrying {NUM} Dust in your reserves\.$")
# Bulk cloak/container sell at any town furrier (Grundael, Bramblefist, Ghaerdish, …).
FURRIER_BULK_RE = re.compile(
    rf"^(\w+) takes the .+?, inspects the contents carefully and removes the "
    rf"items? (?:he|she) is interested in\.\s+\1 hands it back to you, along "
    rf"with {NUM} silver"
)
# Bulk furrier payout as a promissory note (amount is on this follow-up line).
FURRIER_NOTE_RE = re.compile(
    rf"^(\w+) removes the items and hands you a note for {NUM} silvers?\.$"
)
# Single-skin sell (Cold River Bramblefist style).
FURRIER_ACCEPT_RE = re.compile(
    rf"^(\w+) accepts the (.+?) hesitantly before paying you {NUM} silvers?\.$"
)
# Single-skin sell (Vaalor Ghaerdish style).
FURRIER_EXAMINE_RE = re.compile(
    rf"^(\w+) takes the (.+?), examines it carefully, then hands you {NUM} silvers?\.$"
)
JEWELER_ITEM_RE = re.compile(
    rf"The jeweler \w+ takes the (.+), .* hands you {NUM} silver"
)
PROMISSORY_RE = re.compile(
    rf"hands you a \w+(?: \w+)* promissory note for {NUM} silvers"
)
CEND_RE = re.compile(
    rf"^Cendadric takes your (.+), .* hands you {NUM} silver coins"
)
PAWN_RE = re.compile(
    rf"^The pawnbroker takes your (.+), .* hands you {NUM} silver coins"
)
LTE_RE = re.compile(rf"You have been awarded {NUM} Long Term Experience")
BOX_FOUND_RE = re.compile(
    r"carried a .+ (?:box|coffer|chest|strongbox|trunk) on"
)
INTERESTING_RE = re.compile(
    r"^Interesting, (?:he|she|it) carried (.+) on (?:him|her|it)\.$"
)
LOCKSMITH_RE = re.compile(rf"totaling {NUM} silvers\.  You will need")
# ledger.lic appends this table after BANK ACCOUNT; ~lootcap is weekly used estimate.
LOOTCAP_HEADER_RE = re.compile(
    r"^\|\s*\w+\s*\|\s*hourly\s*\|\s*daily\s*\|\s*monthly\s*\|\s*yearly\s*\|\s*~lootcap\s*\|",
    re.I,
)
LOOTCAP_SILVER_RE = re.compile(
    rf"^\|\s*silver\s*\|\s*-?{NUM}\s*\|\s*-?{NUM}\s*\|\s*-?{NUM}\s*\|\s*-?{NUM}\s*\|\s*{NUM}\s*\|",
    re.I,
)
# GEM FIXATE: trades silver earnings for better Gemstone odds (through week or until find).
GEM_FIXATE_ACTIVE_RE = re.compile(
    r"^GEM FIXATE is active until the end of the week or until you find a Gemstone\.?$"
)
GEM_FIXATE_ON_RE = re.compile(
    r"^You fixate on the possibility of finding a Gemstone\."
)
GEM_FIXATE_ALREADY_RE = re.compile(
    r"^You are already under the effects of GEM FIXATE\."
)
GEM_FIXATE_TOO_MUCH_RE = re.compile(
    r"^You have earned too much treasure this week to fixate"
)
GEM_GEODE_FIND_RE = re.compile(
    r"\*\* A glint of light catches your eye, and you notice a rough crystalline geode "
    r"at your feet! \*\*"
)
# Working guess: fixate roughly halves weekly soft-cap earnings potential.
FIXATE_CAP_RATIO = 0.5
TELLER_RE = re.compile(r"^The teller scribbles the transaction")
HIT_FOR_RE = re.compile(r"and hits for (\d+) points of damage!")
CLEAN_MISS_RE = re.compile(r"A clean miss")
STUN_RE = re.compile(r"You are stunned for (\d+) rounds?")
STUN_ON_RE = re.compile(
    r"^You are stunned(?: for \d+ rounds?| \d+ rounds?)?!$|^You are still stunned\.$"
)
STUN_STRUGGLE_RE = re.compile(
    r"^You struggle valiantly against the effects of the stun"
)
PRONE_ON_RE = re.compile(r"^You are knocked to the ground!$")
PRONE_OFF_RE = re.compile(
    r"^You stand back up\.$|^You leap to your feet\.$|^You stand up\.$"
)
WEB_ON_RE = re.compile(
    r"^You are webbed!$|webs? (?:envelop|engulf|coat|cover) you|sticky (?:strands|webbing).+you",
    re.I,
)
WEB_OFF_RE = re.compile(
    r"free yourself from the webs?|webbing dissolves|webs? dissolve",
    re.I,
)
UNCON_ON_RE = re.compile(
    r"^You go limp as you are rendered unconscious!$|^You (?:slump to the ground in an unconscious heap|fall unconscious)!"
)
UNCON_OFF_RE = re.compile(
    r"^You slowly come back to alertness"
)
ROOT_ON_RE = re.compile(r"^You are rooted!$|feet (?:are|feel) rooted", re.I)
ROOT_OFF_RE = re.compile(r"no longer rooted|roots? (?:release|loosen)", re.I)
RT_RE = re.compile(r"^Roundtime: (\d+) sec")
CAST_RT_RE = re.compile(r"^Cast Roundtime (\d+)")
BOUNTY_PAY_RE = re.compile(
    rf"You have earned {NUM} bounty points, {NUM} experience points, and {NUM} silver"
)
LTE_CONVERT_RE = re.compile(
    rf"deducted {NUM} experience points from your field experience and have added {NUM} experience points to your Long-Term"
)
ROOM_RE = re.compile(r"^\[([^,\]]+) - \d+\]")
JEWEL_RE = re.compile(
    r"A glint of light catches your eye, and you notice an? .+ at your feet"
)
ELIGIBLE_RE = re.compile(r"^You are eligible to find Gemstones")
OUTMANEUVER_RE = re.compile(r"^You outmaneuver the attack")
HERB_RE = re.compile(r"^Your .+ feels better\.$")
SIGIL_RE = re.compile(r"Sigil of (\w+)")
PREP_RE = re.compile(r"focusing your mind upon the (.+) spell")
BOUNTY_DONE_RE = re.compile(r"You have completed your task to my satisfaction")
FRIED_RE = re.compile(r"You must rest!|Your mind is (?:saturated|numbed)")
GHOST_RE = re.compile(
    r"^You are a ghost!\s+You must wait until someone resurrects you"
)
DEATHS_CHAN_RE = re.compile(r"^\[Deaths\]\s+\*\s+(\S+)")
HUNTING_ROOM_RE = re.compile(
    r"Hinterwilds|Sailor's Grief|The Rift|Shadow Valley|Melgorehn",
    re.I,
)


def parse_num(s: str) -> int:
    return int(s.replace(",", ""))


def ts_sort_key(ts: str | None) -> tuple:
    """Order Saga wall-clock stamps like '3:45:23 PM' within a single day."""
    if not ts:
        return (99, 0, 0, 0)
    m = re.match(r"(\d{1,2}):(\d{2}):(\d{2}) (AM|PM)$", ts)
    if not m:
        return (99, 0, 0, 0)
    h, mi, sec, ap = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    if ap == "AM":
        h = 0 if h == 12 else h
    else:
        h = 12 if h == 12 else h + 12
    return (0, h, mi, sec)


def sunday_week_start(day: date) -> date:
    """Ledger / Simu weekly loot-cap week: Sunday 00:00 through Saturday (EST)."""
    # date.weekday(): Mon=0 … Sun=6 → days since Sunday
    return day - timedelta(days=(day.weekday() + 1) % 7)


def lootcap_fields(snapshots: list) -> dict:
    """Derive used / remaining / daily-toward-cap from ledger ~lootcap snapshots."""
    snaps = sorted(snapshots or [], key=lambda s: ts_sort_key(s.get("ts")))
    if not snaps:
        return {
            "lootcap_snapshots": [],
            "lootcap_used": None,
            "lootcap_remaining": None,
            "lootcap_weekly_cap": WEEKLY_LOOT_CAP,
            "silver_loot_toward_cap": None,
        }
    used = snaps[-1]["used"]
    toward = snaps[-1]["used"] - snaps[0]["used"] if len(snaps) >= 2 else None
    return {
        "lootcap_snapshots": snaps,
        "lootcap_used": used,
        "lootcap_remaining": WEEKLY_LOOT_CAP - used,
        "lootcap_weekly_cap": WEEKLY_LOOT_CAP,
        "silver_loot_toward_cap": toward,
    }


def _loot_actual(st: dict) -> int:
    actual = st.get("silver_loot_actual")
    if actual is None:
        actual = st.get("silver_gross") or 0
    return int(actual)


def _rate_bundle(actual: int, searches: int) -> dict:
    return {
        "actual": actual,
        "searches": searches,
        "actual_per_search": round(actual / searches, 2) if searches > 0 else None,
    }


def enrich_lootcap_week_stats(docs: list[dict]) -> dict:
    """Add week-to-date lootcap/fixate fields; return cross-week discovery summary.

    Mutates docs in place. Sunday–Saturday weeks.

    Two competing models for GEM FIXATE vs weekly silver soft-cap:
      ends_on_geode — status messaging ends after a geode; silver reduction ends too
      week_long     — once fixate starts, silver reduction lasts the whole week
                      even after a geode (status may still clear)

    Pre/post-geode actual/search rates inside fixate-started weeks are compared
    to never-fixate weeks to score which model fits better.
    """
    by_date = {d["date"]: d for d in docs if d.get("date")}

    def week_slice(iso: str) -> list[dict]:
        day = date.fromisoformat(iso)
        start = sunday_week_start(day)
        out = []
        cur = start
        while cur <= day:
            hit = by_date.get(cur.isoformat())
            if hit:
                out.append(hit)
            cur += timedelta(days=1)
        return out

    def full_week_docs(iso: str) -> list[dict]:
        """All known days in the Sunday–Saturday week containing iso."""
        day = date.fromisoformat(iso)
        start = sunday_week_start(day)
        out = []
        for i in range(7):
            hit = by_date.get((start + timedelta(days=i)).isoformat())
            if hit:
                out.append(hit)
        return out

    def stats_for(doc: dict, char: str | None) -> dict | None:
        if char is None:
            return doc.get("totals")
        return (doc.get("characters") or {}).get(char)

    def day_fixate_status(st: dict | None) -> bool:
        if not st:
            return False
        return bool(st.get("gem_fixate") or st.get("gem_fixate_activated"))

    def week_metrics(week_docs: list[dict], char: str | None) -> dict:
        actual_sum = 0
        peak: int | None = None
        fixate = False
        geodes = 0
        blocked = False
        for wd in week_docs:
            st = stats_for(wd, char)
            if not st:
                continue
            actual_sum += _loot_actual(st)
            used = st.get("lootcap_used")
            if used is not None:
                peak = int(used) if peak is None else max(peak, int(used))
            if day_fixate_status(st):
                fixate = True
            geodes += int(st.get("gemstone_geodes_found") or 0)
            if st.get("gem_fixate_blocked"):
                blocked = True
        return {
            "silver_loot_week_actual": actual_sum,
            "lootcap_week_peak": peak,
            "gem_fixate_week": fixate,
            "gemstone_geodes_week": geodes,
            "gem_fixate_blocked_week": blocked,
        }

    def annotate_phases(full: list[dict], char: str | None) -> None:
        """Tag each day with fixate phase under both status and week-long models."""
        committed = False
        geode_seen = False
        for wd in full:
            st = stats_for(wd, char)
            if not st:
                continue
            if day_fixate_status(st):
                committed = True
            geodes = int(st.get("gemstone_geodes_found") or 0)
            # Phase is status/find oriented. Re-fixating after a geode returns to
            # active. Days with no status after a geode are post_geode (silver
            # effect may still be week-long — see discovery verdict).
            if not committed:
                phase = "none"
            elif geodes > 0:
                phase = "geode_day"
            elif day_fixate_status(st):
                phase = "active"
            elif geode_seen:
                phase = "post_geode"
            else:
                phase = "active"
            if geodes > 0:
                geode_seen = True
            # Status on after a prior geode without a new geode today (re-fixate
            # or sticky messaging).
            status_after = bool(
                geode_seen and geodes == 0 and day_fixate_status(st)
            )
            st["gem_fixate_week_committed"] = committed
            st["gem_fixate_phase"] = phase
            st["gem_fixate_status_after_geode"] = status_after

    for doc in docs:
        iso = doc.get("date")
        if not iso:
            continue
        # Annotate phases using the full week so post_geode is correct mid-week.
        full = full_week_docs(iso)
        annotate_phases(full, None)
        for name in (doc.get("characters") or {}):
            annotate_phases(full, name)
        week_docs = week_slice(iso)
        totals = doc.setdefault("totals", {})
        metrics = week_metrics(week_docs, None)
        totals.update(metrics)
        # Carry phase fields from totals annotation onto the day totals if present.
        for name, ch in (doc.get("characters") or {}).items():
            ch.update(week_metrics(week_docs, name))

    # Completed-week analysis.
    week_peaks: dict[str, dict] = {}
    pre_actual = pre_searches = 0
    post_actual = post_searches = 0
    active_actual = active_searches = 0
    never_actual = never_searches = 0
    status_after_geode_days = 0
    post_geode_days = 0

    for doc in docs:
        iso = doc.get("date")
        if not iso:
            continue
        start = sunday_week_start(date.fromisoformat(iso)).isoformat()
        if start in week_peaks:
            continue
        full = full_week_docs(iso)
        # Ensure phases annotated (docs may only hit this loop once per week).
        annotate_phases(full, None)
        m = week_metrics(full, None)

        status_fixate_peak = None
        status_clear_peak = None
        committed_peak = None
        never_peak = None
        first_geode_iso = None
        status_post_geode = 0
        post_days = 0
        geode_seen_w = False

        for wd in full:
            st = stats_for(wd, None)
            if not st:
                continue
            phase = st.get("gem_fixate_phase") or "none"
            actual = _loot_actual(st)
            searches = int(st.get("searches") or 0)
            used = st.get("lootcap_used")
            used_i = int(used) if used is not None else None
            geodes = int(st.get("gemstone_geodes_found") or 0)
            if geodes > 0 and first_geode_iso is None:
                first_geode_iso = wd.get("date")

            if m["gem_fixate_week"]:
                if used_i is not None:
                    committed_peak = (
                        used_i if committed_peak is None else max(committed_peak, used_i)
                    )
                if day_fixate_status(st):
                    if used_i is not None:
                        status_fixate_peak = (
                            used_i
                            if status_fixate_peak is None
                            else max(status_fixate_peak, used_i)
                        )
                    if not geode_seen_w or geodes > 0:
                        active_actual += actual
                        active_searches += searches
                else:
                    if used_i is not None:
                        status_clear_peak = (
                            used_i
                            if status_clear_peak is None
                            else max(status_clear_peak, used_i)
                        )
                # Pre = before/on first geode; post = after with status clear.
                # Re-fixate after a find is excluded from both (separate window).
                if phase == "post_geode":
                    post_actual += actual
                    post_searches += searches
                    post_days += 1
                    if st.get("gem_fixate_status_after_geode"):
                        status_post_geode += 1
                elif phase in ("active", "geode_day") and not geode_seen_w:
                    pre_actual += actual
                    pre_searches += searches
                elif phase == "geode_day":
                    pre_actual += actual
                    pre_searches += searches
            else:
                never_actual += actual
                never_searches += searches
                if used_i is not None:
                    never_peak = used_i if never_peak is None else max(never_peak, used_i)

            if geodes > 0:
                geode_seen_w = True

        status_after_geode_days += status_post_geode
        post_geode_days += post_days

        week_peaks[start] = {
            "week_start": start,
            "days_present": len(full),
            "peak": m["lootcap_week_peak"],
            "actual": m["silver_loot_week_actual"],
            "fixate": m["gem_fixate_week"],
            "geodes": m["gemstone_geodes_week"],
            "blocked": m["gem_fixate_blocked_week"],
            "first_geode": first_geode_iso,
            # ends_on_geode model (status messaging)
            "fixate_day_peak": status_fixate_peak,
            "normal_day_peak": status_clear_peak,
            # week_long model (committed once fixate starts)
            "committed_week_peak": committed_peak,
            "never_fixate_peak": never_peak,
            "status_after_geode_days": status_post_geode,
            "post_geode_days": post_days,
        }

    pre_rate = _rate_bundle(pre_actual, pre_searches)
    post_rate = _rate_bundle(post_actual, post_searches)
    active_rate = _rate_bundle(active_actual, active_searches)
    never_rate = _rate_bundle(never_actual, never_searches)

    def _closer(a: float | None, b: float | None, c: float | None) -> str | None:
        """Is a closer to b or to c?"""
        if a is None or b is None or c is None:
            return None
        return "b" if abs(a - b) <= abs(a - c) else "c"

    pre_aps = pre_rate["actual_per_search"]
    post_aps = post_rate["actual_per_search"]
    never_aps = never_rate["actual_per_search"]
    closer = _closer(post_aps, pre_aps, never_aps)

    # Score models. Status clearing after geode is expected under both models for
    # the *find bonus*; the silver question is about post-geode earnings.
    verdict = "inconclusive"
    verdict_reason = "Need more fixate weeks with hunting both before and after a geode."
    # Primary signal: does the geode find change earnings rate?
    # Secondary: never-fixate baseline (often thin / different hunts).
    if post_aps is not None and pre_aps is not None:
        vs_pre = abs(post_aps - pre_aps) / pre_aps if pre_aps else 1.0
        never_n = never_rate["searches"]
        never_usable = never_aps is not None and never_n >= 1000
        if vs_pre <= 0.15:
            verdict = "supports_week_long"
            verdict_reason = (
                f"Post-geode actual/search ({post_aps}) is within 15% of pre-geode "
                f"({pre_aps}) while status messaging is usually clear — geode find "
                f"does not look like it changes silver rate; fits week-long silver "
                f"effect (or fixate not moving rate much)."
            )
            if never_usable:
                verdict_reason += (
                    f" Never-fixate baseline ({never_aps} over {never_n} searches) "
                    f"is for context only."
                )
        elif never_usable and closer == "c":
            vs_never = abs(post_aps - never_aps) / never_aps if never_aps else 1.0
            if vs_never <= 0.15 and post_aps > pre_aps * 1.10:
                verdict = "supports_ends_on_geode"
                verdict_reason = (
                    f"Post-geode actual/search ({post_aps}) rises from pre-geode "
                    f"({pre_aps}) toward never-fixate ({never_aps}) — silver "
                    f"reduction looks tied to the status/find window."
                )
            else:
                verdict = "inconclusive"
                verdict_reason = (
                    f"Post-geode ({post_aps}) differs from pre-geode ({pre_aps}) but "
                    f"does not cleanly match never-fixate ({never_aps})."
                )
        else:
            verdict = "inconclusive"
            verdict_reason = (
                f"Post-geode actual/search ({post_aps}) differs from pre-geode "
                f"({pre_aps})"
                + (
                    f"; never-fixate sample too small ({never_n} searches)."
                    if not never_usable
                    else f" vs never-fixate ({never_aps})."
                )
                + " Keep collecting fixate weeks with hunts before and after a geode."
            )

    # Peaks under each model
    status_fixate_peaks = [
        w["fixate_day_peak"]
        for w in week_peaks.values()
        if w.get("fixate_day_peak") is not None
    ]
    status_clear_peaks = [
        w["normal_day_peak"]
        for w in week_peaks.values()
        if w.get("normal_day_peak") is not None
    ]
    committed_peaks = [
        w["committed_week_peak"]
        for w in week_peaks.values()
        if w.get("committed_week_peak") is not None
    ]
    never_peaks = [
        w["never_fixate_peak"]
        for w in week_peaks.values()
        if w.get("never_fixate_peak") is not None
    ]
    max_status_fixate = max(status_fixate_peaks) if status_fixate_peaks else None
    max_status_clear = max(status_clear_peaks) if status_clear_peaks else None
    max_committed = max(committed_peaks) if committed_peaks else None
    max_never = max(never_peaks) if never_peaks else None

    ratio_status = None
    if max_status_fixate and max_status_clear and max_status_clear > 0:
        ratio_status = round(max_status_fixate / max_status_clear, 3)
    ratio_committed = None
    if max_committed and max_never and max_never > 0:
        ratio_committed = round(max_committed / max_never, 3)

    est_from_fixate = (
        int(round(max_committed / FIXATE_CAP_RATIO))
        if max_committed is not None
        else (
            int(round(max_status_fixate / FIXATE_CAP_RATIO))
            if max_status_fixate is not None
            else None
        )
    )

    return {
        "weeks": week_peaks,
        "fixate_duration_models": {
            "ends_on_geode": {
                "fixate_day_peak_max": max_status_fixate,
                "clear_day_peak_max": max_status_clear,
                "ratio": ratio_status,
            },
            "week_long": {
                "committed_week_peak_max": max_committed,
                "never_fixate_peak_max": max_never,
                "ratio_vs_never": ratio_committed,
            },
        },
        "pre_geode_rate": pre_rate,
        "post_geode_rate": post_rate,
        "active_status_rate": active_rate,
        "never_fixate_rate": never_rate,
        "post_geode_closer_to": (
            "pre_geode"
            if closer == "b"
            else ("never_fixate" if closer == "c" else None)
        ),
        "status_messaging_after_geode_days": status_after_geode_days,
        "post_geode_days": post_geode_days,
        "fixate_duration_verdict": verdict,
        "fixate_duration_reason": verdict_reason,
        # Back-compat / subtitle fields
        "fixate_day_peak_max": max_status_fixate,
        "normal_day_peak_max": max_status_clear,
        "fixate_week_peak_max": max_committed or max_status_fixate,
        "normal_week_peak_max": max_never or max_status_clear,
        "observed_fixate_to_normal_ratio": ratio_committed or ratio_status,
        "assumed_fixate_ratio": FIXATE_CAP_RATIO,
        "estimated_normal_cap_from_fixate": est_from_fixate,
        "estimated_normal_cap_from_normal_peak": max_never or max_status_clear,
    }


def avg(n: int, d: int) -> float | None:
    if d <= 0:
        return None
    return round(n / d, 2)


def strip_line(raw: str) -> tuple[str | None, str]:
    raw = raw.rstrip("\n")
    m = TS_RE.match(raw)
    if m:
        return m.group(1), raw[m.end() :]
    return None, raw


def parse_filename_date(name: str) -> date | None:
    m = FILE_RE.match(name)
    if not m:
        return None
    return date(int(m.group("yyyy")), int(m.group("mm")), int(m.group("dd")))


def character_key(folder: str) -> str:
    return folder.strip()


def pct(n: int, d: int) -> float | None:
    if d <= 0:
        return None
    return round(100.0 * n / d, 2)


def empty_char() -> dict:
    return {
        "files": [],
        "sessions": [],
        "line_count": 0,
        "incoming_attacks": 0,
        "evades": 0,
        "blocks": 0,
        "parries": 0,
        "blink_casts": 0,
        "blink_fires": 0,
        "blink_successes": 0,
        "evade_messages": defaultdict(int),
        "attacks_blink_up": 0,
        "attacks_blink_down": 0,
        "hits_blink_up": 0,
        "hits_blink_down": 0,
        "damage_blink_up": 0,
        "damage_blink_down": 0,
        "ebp_blink_up": 0,
        "ebp_blink_down": 0,
        "attacks_clean": 0,
        "attacks_impaired": 0,
        "ebp_clean": 0,
        "ebp_impaired": 0,
        "hits_clean": 0,
        "hits_impaired": 0,
        "impaired_stun": 0,
        "impaired_prone": 0,
        "impaired_webbed": 0,
        "impaired_unconscious": 0,
        "impaired_rooted": 0,
        "deaths": 0,
        "searches": 0,
        "silver_corpses": 0,
        "silver_boxes": 0,
        "silver_jeweler": 0,
        "silver_pawn": 0,
        "silver_alchemist": 0,
        "silver_furrier": 0,
        "silver_promissory": 0,
        "locksmith_fees": 0,
        "lootcap_snapshots": [],
        "dust_motes": 0,
        "dust_reserve_snapshots": [],
        "skins_yielded": 0,
        "skins_botched": 0,
        "skins_pending": 0,
        "skins_sold": 0,
        "skins_by_type": defaultdict(int),
        "skins_by_creature": defaultdict(int),
        "skin_sales": [],
        "boxes_found": 0,
        "lte": 0,
        "interesting_items": defaultdict(int),
        "searches_by_creature": defaultdict(int),
        "roundtime_sec": 0,
        "roundtime_events": 0,
        "cast_rt_sec": 0,
        "stuns": 0,
        "stun_rounds": 0,
        "herbs": 0,
        "incoming_hits": 0,
        "incoming_misses": 0,
        "outgoing_hits": 0,
        "outgoing_misses": 0,
        "damage_taken": 0,
        "damage_dealt": 0,
        "outmaneuvers": 0,
        "bounty_turnins": 0,
        "bounty_points": 0,
        "bounty_xp": 0,
        "bounty_silver": 0,
        "bounty_tasks_done": 0,
        "lte_converted": 0,
        "sigils": defaultdict(int),
        "spells_prepped": defaultdict(int),
        "rooms": defaultdict(int),
        "hunting_room_lines": 0,
        "gem_eligible": 0,
        "gemstones_found": 0,
        "fried_mentions": 0,
        "gem_fixate": False,
        "gem_fixate_activated": False,
        "gem_fixate_blocked": False,
        "gemstone_geodes_found": 0,
        "as_expect": None,
    }


def classify_teleport(lines: list[str], i: int) -> str:
    """blink_success | blink_fire for 1215-style teleport messaging."""
    follow: list[str] = []
    for s in lines[i + 1 : i + 16]:
        t = s.strip()
        if not t or PROMPT_RE.match(t) or t.startswith("---") or t.startswith("["):
            continue
        if t.startswith(">") or t.startswith("R>") or t.startswith("!>"):
            continue
        follow.append(t)
        if len(follow) >= 4:
            break
    joined = "\n".join(follow)
    if BLINK_END_RE.search(joined):
        return "blink_success"
    for t in follow:
        if OUTGOING_RE.match(t):
            break
        if AS_RE.search(t):
            return "blink_fire"
    return "blink_fire"


def update_status_flags(line: str, status: dict) -> None:
    if STUN_ON_RE.match(line) or STUN_STRUGGLE_RE.match(line):
        status["stunned"] = True
    if PRONE_ON_RE.match(line):
        status["prone"] = True
    if PRONE_OFF_RE.match(line):
        status["prone"] = False
        status["stunned"] = False
    if WEB_ON_RE.search(line):
        status["webbed"] = True
    if WEB_OFF_RE.search(line):
        status["webbed"] = False
    if UNCON_ON_RE.match(line):
        status["unconscious"] = True
    if UNCON_OFF_RE.match(line):
        status["unconscious"] = False
        status["stunned"] = False
        status["prone"] = False
    if ROOT_ON_RE.search(line):
        status["rooted"] = True
    if ROOT_OFF_RE.search(line):
        status["rooted"] = False


def is_impaired(status: dict) -> bool:
    return any(status.get(k) for k in ("stunned", "prone", "webbed", "unconscious", "rooted"))


def note_incoming(
    bucket: dict,
    *,
    blink_up: bool,
    impaired: bool,
    status: dict,
    ebp: bool = False,
    hit: bool = False,
    dmg: int = 0,
) -> None:
    """Count an incoming attack (and optional EBP/hit) with blink/status splits."""
    bucket["incoming_attacks"] += 1
    if blink_up:
        bucket["attacks_blink_up"] += 1
        if ebp:
            bucket["ebp_blink_up"] += 1
    else:
        bucket["attacks_blink_down"] += 1
        if ebp:
            bucket["ebp_blink_down"] += 1
    if impaired:
        bucket["attacks_impaired"] += 1
        if ebp:
            bucket["ebp_impaired"] += 1
        if status.get("stunned"):
            bucket["impaired_stun"] += 1
        if status.get("prone"):
            bucket["impaired_prone"] += 1
        if status.get("webbed"):
            bucket["impaired_webbed"] += 1
        if status.get("unconscious"):
            bucket["impaired_unconscious"] += 1
        if status.get("rooted"):
            bucket["impaired_rooted"] += 1
    else:
        bucket["attacks_clean"] += 1
        if ebp:
            bucket["ebp_clean"] += 1
    if hit:
        bucket["incoming_hits"] += 1
        if blink_up:
            bucket["hits_blink_up"] += 1
            bucket["damage_blink_up"] += dmg
        else:
            bucket["hits_blink_down"] += 1
            bucket["damage_blink_down"] += dmg
        if impaired:
            bucket["hits_impaired"] += 1
        else:
            bucket["hits_clean"] += 1


def looks_incoming_as(prev_combat: str) -> bool:
    if not prev_combat:
        return True
    if OUTGOING_RE.match(prev_combat):
        return False
    if prev_combat.startswith("You are now targeting"):
        return False
    return True


def parse_file(
    path: Path, bucket: dict, blink_up: bool = False, character: str | None = None
) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    raw_lines = text.splitlines()
    lines = [strip_line(x)[1] for x in raw_lines]
    bucket["files"].append(path.name)
    bucket["line_count"] += len(raw_lines)

    session_start = None
    first_ts = None
    last_ts = None
    prev_combat = ""
    skip_as_as_new_attack = False
    status = {
        "stunned": False,
        "prone": False,
        "webbed": False,
        "unconscious": False,
        "rooted": False,
        "dead": False,
    }
    char_first = (character or "").split()[0] if character else ""
    awaiting_lootcap_row = False

    for i, raw in enumerate(raw_lines):
        ts, line = strip_line(raw)
        if ts:
            first_ts = first_ts or ts
            last_ts = ts

        if LOOTCAP_HEADER_RE.match(line):
            awaiting_lootcap_row = True
            continue
        if awaiting_lootcap_row:
            if line.startswith("+"):
                continue
            m = LOOTCAP_SILVER_RE.match(line)
            if m:
                bucket["lootcap_snapshots"].append(
                    {"ts": ts, "used": parse_num(m.group(5))}
                )
                awaiting_lootcap_row = False
                continue
            awaiting_lootcap_row = False

        if (
            GEM_FIXATE_ACTIVE_RE.match(line)
            or GEM_FIXATE_ALREADY_RE.match(line)
            or GEM_FIXATE_ON_RE.match(line)
        ):
            bucket["gem_fixate"] = True
            if GEM_FIXATE_ON_RE.match(line):
                bucket["gem_fixate_activated"] = True
        if GEM_FIXATE_TOO_MUCH_RE.match(line):
            bucket["gem_fixate_blocked"] = True
        if GEM_GEODE_FIND_RE.search(line):
            bucket["gemstone_geodes_found"] += 1

        sm = SESSION_RE.match(line)
        if sm:
            if session_start:
                bucket["sessions"].append(
                    {"started": session_start, "first_ts": first_ts, "last_ts": last_ts}
                )
            session_start = sm.group("when")
            first_ts = ts
            last_ts = ts
            continue

        # Unique death: rising edge of ghost state (not every DEAD> line).
        if GHOST_RE.match(line):
            if not status["dead"]:
                bucket["deaths"] += 1
                status["dead"] = True
        elif char_first and DEATHS_CHAN_RE.match(line):
            who = DEATHS_CHAN_RE.match(line).group(1)
            if who.lower() == char_first.lower() and not status["dead"]:
                bucket["deaths"] += 1
                status["dead"] = True
        elif status["dead"] and (
            (line.startswith(">") and not line.startswith("DEAD"))
            or line.startswith("R>")
            or line.startswith("!>")
        ):
            status["dead"] = False

        update_status_flags(line, status)
        impaired = is_impaired(status)

        if BLINK_CAST_RE.search(line):
            bucket["blink_casts"] += 1

        if BLINK_UP_RE.search(line):
            blink_up = True

        if BLINK_END_RE.search(line):
            blink_up = False

        if BLINK_TRIGGER_RE.search(line):
            kind = classify_teleport(lines, i)
            note_incoming(bucket, blink_up=blink_up, impaired=impaired, status=status)
            bucket["blink_fires"] += 1
            if kind == "blink_success":
                bucket["blink_successes"] += 1
                blink_up = False
            skip_as_as_new_attack = True
            prev_combat = line
            continue

        if PLAYER_EVADE_RE.match(line):
            bucket["evades"] += 1
            note_incoming(
                bucket, blink_up=blink_up, impaired=impaired, status=status, ebp=True
            )
            bucket["evade_messages"][line] += 1
            skip_as_as_new_attack = True
            prev_combat = line
            continue

        if PLAYER_BLOCK_RE.match(line):
            bucket["blocks"] += 1
            note_incoming(
                bucket, blink_up=blink_up, impaired=impaired, status=status, ebp=True
            )
            skip_as_as_new_attack = True
            prev_combat = line
            continue

        if PLAYER_PARRY_RE.match(line):
            bucket["parries"] += 1
            note_incoming(
                bucket, blink_up=blink_up, impaired=impaired, status=status, ebp=True
            )
            skip_as_as_new_attack = True
            prev_combat = line
            continue

        smatch = SEARCH_RE.match(line)
        if smatch and not SEARCH_THROUGH_RE.match(line):
            bucket["searches"] += 1
            bucket["searches_by_creature"][smatch.group(1)] += 1
        m = GATHER_BOX_RE.match(line)
        if m:
            bucket["silver_boxes"] += parse_num(m.group(1))
            continue
        m = GATHER_COINS_RE.match(line)
        if m:
            bucket["silver_corpses"] += parse_num(m.group(1))
            continue
        m = SKIN_RE.match(line)
        if m:
            creature = m.group(1).strip()
            product = m.group(2).strip().rstrip(".")
            bucket["skins_yielded"] += 1
            bucket["skins_pending"] += 1
            bucket["skins_by_type"][product] += 1
            bucket["skins_by_creature"][creature] += 1
            continue
        m = BOTCH_RE.match(line)
        if m:
            bucket["skins_botched"] += 1
            continue
        if DUST_MOTE_RE.search(line):
            bucket["dust_motes"] += 1
            continue
        m = WEALTH_DUST_RE.match(line)
        if m:
            bucket["dust_reserve_snapshots"].append(parse_num(m.group(1)))
            continue
        m = FURRIER_BULK_RE.match(line) or FURRIER_NOTE_RE.match(line)
        if m:
            amount = parse_num(m.group(2))
            pending = bucket["skins_pending"]
            bucket["silver_furrier"] += amount
            bucket["skins_sold"] += pending
            bucket["skin_sales"].append(
                {
                    "silver": amount,
                    "skins": pending,
                    "est_per_skin": avg(amount, pending),
                    "via": "bulk",
                    "furrier": m.group(1),
                }
            )
            bucket["skins_pending"] = 0
            continue
        m = FURRIER_ACCEPT_RE.match(line) or FURRIER_EXAMINE_RE.match(line)
        if m:
            product = m.group(2).strip()
            amount = parse_num(m.group(3))
            bucket["silver_furrier"] += amount
            bucket["skins_sold"] += 1
            if bucket["skins_pending"] > 0:
                bucket["skins_pending"] -= 1
            bucket["skin_sales"].append(
                {
                    "silver": amount,
                    "skins": 1,
                    "est_per_skin": float(amount),
                    "via": "single",
                    "product": product,
                    "furrier": m.group(1),
                }
            )
            continue
        if TELLER_RE.match(line):
            continue
        m = PROMISSORY_RE.search(line)
        if m:
            bucket["silver_promissory"] += parse_num(m.group(1))
            continue
        m = JEWELER_ITEM_RE.search(line)
        if m:
            bucket["silver_jeweler"] += parse_num(m.group(2))
            continue
        m = CEND_RE.match(line)
        if m:
            bucket["silver_alchemist"] += parse_num(m.group(2))
            continue
        m = PAWN_RE.match(line)
        if m:
            bucket["silver_pawn"] += parse_num(m.group(2))
            continue
        m = LTE_RE.search(line)
        if m:
            bucket["lte"] += parse_num(m.group(1))
            continue
        if BOX_FOUND_RE.search(line):
            bucket["boxes_found"] += 1
        m = INTERESTING_RE.match(line)
        if m:
            bucket["interesting_items"][m.group(1).strip()] += 1
        m = LOCKSMITH_RE.search(line)
        if m:
            bucket["locksmith_fees"] += parse_num(m.group(1))
            continue

        if OUTMANEUVER_RE.match(line):
            bucket["outmaneuvers"] += 1
            note_incoming(
                bucket, blink_up=blink_up, impaired=impaired, status=status, ebp=True
            )
            skip_as_as_new_attack = True
            prev_combat = line
            continue
        m = RT_RE.match(line)
        if m:
            bucket["roundtime_events"] += 1
            bucket["roundtime_sec"] += int(m.group(1))
            continue
        m = CAST_RT_RE.match(line)
        if m:
            bucket["cast_rt_sec"] += int(m.group(1))
            continue
        m = STUN_RE.search(line)
        if m:
            bucket["stuns"] += 1
            bucket["stun_rounds"] += int(m.group(1))
            continue
        if HERB_RE.match(line):
            bucket["herbs"] += 1
            continue
        m = BOUNTY_PAY_RE.search(line)
        if m:
            bucket["bounty_turnins"] += 1
            bucket["bounty_points"] += parse_num(m.group(1))
            bucket["bounty_xp"] += parse_num(m.group(2))
            bucket["bounty_silver"] += parse_num(m.group(3))
            continue
        m = LTE_CONVERT_RE.search(line)
        if m:
            bucket["lte_converted"] += parse_num(m.group(2))
            continue
        if BOUNTY_DONE_RE.search(line):
            bucket["bounty_tasks_done"] += 1
            continue
        if JEWEL_RE.search(line):
            bucket["gemstones_found"] += 1
            continue
        if ELIGIBLE_RE.search(line):
            bucket["gem_eligible"] += 1
            continue
        if FRIED_RE.search(line):
            bucket["fried_mentions"] += 1
        m = ROOM_RE.match(line)
        if m:
            name = m.group(1).strip()
            bucket["rooms"][name] += 1
            if HUNTING_ROOM_RE.search(name):
                bucket["hunting_room_lines"] += 1
        m = SIGIL_RE.search(line)
        if m and line.startswith("[signore]"):
            bucket["sigils"][m.group(1)] += 1
        m = PREP_RE.search(line)
        if m:
            bucket["spells_prepped"][m.group(1)] += 1
        hm = HIT_FOR_RE.search(line)
        if hm:
            dmg = int(hm.group(1))
            if bucket["as_expect"] == "out":
                bucket["outgoing_hits"] += 1
                bucket["damage_dealt"] += dmg
            else:
                # Hit resolves after the AS line already counted the incoming
                # attack; only split hit/damage here.
                bucket["incoming_hits"] += 1
                bucket["damage_taken"] += dmg
                if blink_up:
                    bucket["hits_blink_up"] += 1
                    bucket["damage_blink_up"] += dmg
                else:
                    bucket["hits_blink_down"] += 1
                    bucket["damage_blink_down"] += dmg
                if impaired:
                    bucket["hits_impaired"] += 1
                else:
                    bucket["hits_clean"] += 1
            continue
        if CLEAN_MISS_RE.search(line):
            if bucket["as_expect"] == "out":
                bucket["outgoing_misses"] += 1
            else:
                bucket["incoming_misses"] += 1
            continue

        if AS_RE.search(line):
            if skip_as_as_new_attack:
                skip_as_as_new_attack = False
                bucket["as_expect"] = "in"
            elif looks_incoming_as(prev_combat):
                note_incoming(
                    bucket, blink_up=blink_up, impaired=impaired, status=status
                )
                bucket["as_expect"] = "in"
            else:
                bucket["as_expect"] = "out"
            prev_combat = line
            continue

        if line and not PROMPT_RE.match(line) and not line.startswith("---") and not line.startswith("["):
            if line.startswith(">") or line.startswith("R>") or line.startswith("!>"):
                pass
            else:
                prev_combat = line
                skip_as_as_new_attack = False

    if session_start or first_ts:
        bucket["sessions"].append(
            {"started": session_start, "first_ts": first_ts, "last_ts": last_ts}
        )
    return blink_up


def finalize_char(bucket: dict) -> dict:
    incoming = bucket["incoming_attacks"]
    evades = bucket["evades"]
    blocks = bucket["blocks"]
    parries = bucket["parries"]
    ebp = evades + blocks + parries
    blink_ok = bucket["blink_successes"]
    hits = bucket["incoming_hits"]
    out = {
        "files": bucket["files"],
        "sessions": bucket["sessions"],
        "line_count": bucket["line_count"],
        "incoming_attacks": incoming,
        "evades": evades,
        "blocks": blocks,
        "parries": parries,
        "blink_casts": bucket["blink_casts"],
        "blink_fires": bucket["blink_fires"],
        "blink_successes": blink_ok,
        "evade_pct_of_attacks": pct(evades, incoming),
        "block_pct_of_attacks": pct(blocks, incoming),
        "parry_pct_of_attacks": pct(parries, incoming),
        "ebp_events": ebp,
        "ebp_pct_of_attacks": pct(ebp, incoming),
        "outmaneuvers": bucket["outmaneuvers"],
        # Blink uptime / downtime (by attack counts while 1215 tracked up).
        "attacks_blink_up": bucket["attacks_blink_up"],
        "attacks_blink_down": bucket["attacks_blink_down"],
        "hits_blink_up": bucket["hits_blink_up"],
        "hits_blink_down": bucket["hits_blink_down"],
        "damage_blink_up": bucket["damage_blink_up"],
        "damage_blink_down": bucket["damage_blink_down"],
        "ebp_blink_up": bucket["ebp_blink_up"],
        "ebp_blink_down": bucket["ebp_blink_down"],
        "blink_uptime_pct": pct(bucket["attacks_blink_up"], incoming),
        "hit_rate_blink_up": pct(bucket["hits_blink_up"], bucket["attacks_blink_up"]),
        "hit_rate_blink_down": pct(
            bucket["hits_blink_down"], bucket["attacks_blink_down"]
        ),
        # Share of all landed hits that happened while Blink was down — lower is better.
        "pct_hits_while_blink_down": pct(bucket["hits_blink_down"], hits),
        "pct_damage_while_blink_down": pct(
            bucket["damage_blink_down"], bucket["damage_taken"]
        ),
        "ebp_pct_blink_up": pct(bucket["ebp_blink_up"], bucket["attacks_blink_up"]),
        "ebp_pct_blink_down": pct(
            bucket["ebp_blink_down"], bucket["attacks_blink_down"]
        ),
        # Status-impaired vs clean (stun/prone/web/unconscious/rooted).
        "attacks_clean": bucket["attacks_clean"],
        "attacks_impaired": bucket["attacks_impaired"],
        "ebp_clean": bucket["ebp_clean"],
        "ebp_impaired": bucket["ebp_impaired"],
        "hits_clean": bucket["hits_clean"],
        "hits_impaired": bucket["hits_impaired"],
        "ebp_pct_clean": pct(bucket["ebp_clean"], bucket["attacks_clean"]),
        "ebp_pct_impaired": pct(bucket["ebp_impaired"], bucket["attacks_impaired"]),
        "hit_rate_clean": pct(bucket["hits_clean"], bucket["attacks_clean"]),
        "hit_rate_impaired": pct(bucket["hits_impaired"], bucket["attacks_impaired"]),
        "impaired_stun": bucket["impaired_stun"],
        "impaired_prone": bucket["impaired_prone"],
        "impaired_webbed": bucket["impaired_webbed"],
        "impaired_unconscious": bucket["impaired_unconscious"],
        "impaired_rooted": bucket["impaired_rooted"],
        "deaths": bucket["deaths"],
        "evade_messages": dict(bucket["evade_messages"]),
        **loot_snapshot(bucket),
        **extra_snapshot(bucket),
    }
    return out


def loot_snapshot(bucket: dict) -> dict:
    searches = bucket["searches"]
    skins = bucket["skins_yielded"]
    sold = bucket["skins_sold"]
    attempts = skins + bucket["skins_botched"]
    sales = list(bucket["skin_sales"])
    # Weighted average: total furrier silver / skins actually sold (not unsold pending).
    est_mean = avg(bucket["silver_furrier"], sold) if sold else avg(bucket["silver_furrier"], skins)
    gross = (
        bucket["silver_corpses"]
        + bucket["silver_boxes"]
        + bucket["silver_jeweler"]
        + bucket["silver_pawn"]
        + bucket["silver_alchemist"]
        + bucket["silver_furrier"]
        + bucket["silver_promissory"]
        + bucket["bounty_silver"]
    )
    return {
        "searches": searches,
        "silver_corpses": bucket["silver_corpses"],
        "silver_boxes": bucket["silver_boxes"],
        "silver_jeweler": bucket["silver_jeweler"],
        "silver_pawn": bucket["silver_pawn"],
        "silver_alchemist": bucket["silver_alchemist"],
        "silver_furrier": bucket["silver_furrier"],
        "silver_promissory": bucket["silver_promissory"],
        "locksmith_fees": bucket["locksmith_fees"],
        "silver_gross": gross,
        "silver_net": gross - bucket["locksmith_fees"],
        # Log-event loot income (same as gross). Always available; compare to
        # ledger silver_loot_toward_cap when bank account was checked ≥2×.
        "silver_loot_actual": gross,
        "avg_silver_per_search": avg(bucket["silver_corpses"], searches),
        **lootcap_fields(bucket.get("lootcap_snapshots") or []),
        "dust_motes": bucket["dust_motes"],
        "avg_dust_per_search": avg(bucket["dust_motes"], searches),
        "dust_reserve_snapshots": list(bucket["dust_reserve_snapshots"]),
        "skins_yielded": skins,
        "skins_botched": bucket["skins_botched"],
        "skins_sold": sold,
        "skins_pending": bucket["skins_pending"],
        "skin_success_pct": pct(skins, attempts),
        "skins_by_type": dict(bucket["skins_by_type"]),
        "skins_by_creature": dict(bucket["skins_by_creature"]),
        "skin_sales": sales,
        "est_silver_per_skin": est_mean,
        "boxes_found": bucket["boxes_found"],
        "lte": bucket["lte"],
        "interesting_items": dict(bucket["interesting_items"]),
    }


def extra_snapshot(bucket: dict) -> dict:
    rt = bucket["roundtime_sec"]
    searches = bucket["searches"]
    rooms = dict(
        sorted(bucket["rooms"].items(), key=lambda kv: -kv[1])[:12]
    )
    return {
        "searches_by_creature": dict(bucket["searches_by_creature"]),
        "roundtime_sec": rt,
        "roundtime_events": bucket["roundtime_events"],
        "cast_rt_sec": bucket["cast_rt_sec"],
        "stuns": bucket["stuns"],
        "stun_rounds": bucket["stun_rounds"],
        "herbs": bucket["herbs"],
        "incoming_hits": bucket["incoming_hits"],
        "incoming_misses": bucket["incoming_misses"],
        "outgoing_hits": bucket["outgoing_hits"],
        "outgoing_misses": bucket["outgoing_misses"],
        "damage_taken": bucket["damage_taken"],
        "damage_dealt": bucket["damage_dealt"],
        "hit_pct_outgoing": pct(
            bucket["outgoing_hits"],
            bucket["outgoing_hits"] + bucket["outgoing_misses"],
        ),
        "outmaneuvers": bucket["outmaneuvers"],
        "bounty_turnins": bucket["bounty_turnins"],
        "bounty_points": bucket["bounty_points"],
        "bounty_xp": bucket["bounty_xp"],
        "bounty_silver": bucket["bounty_silver"],
        "bounty_tasks_done": bucket["bounty_tasks_done"],
        "lte_converted": bucket["lte_converted"],
        "lte_total": bucket["lte"] + bucket["lte_converted"],
        "sigils": dict(bucket["sigils"]),
        "spells_prepped": dict(bucket["spells_prepped"]),
        "rooms_top": rooms,
        "hunting_room_lines": bucket["hunting_room_lines"],
        "gem_eligible": bucket["gem_eligible"],
        "gemstones_found": bucket["gemstones_found"],
        "fried_mentions": bucket["fried_mentions"],
        "gem_fixate": bool(bucket.get("gem_fixate")),
        "gem_fixate_activated": bool(bucket.get("gem_fixate_activated")),
        "gem_fixate_blocked": bool(bucket.get("gem_fixate_blocked")),
        "gemstone_geodes_found": bucket.get("gemstone_geodes_found", 0),
        "silver_per_rt_hour": avg(bucket["silver_corpses"] * 3600, rt)
        if rt
        else None,
        "searches_per_rt_hour": avg(searches * 3600, rt) if rt else None,
        # Field XP is not printed per kill; LTE from loot is ~1 per 10 field.
        "xp_est": bucket["bounty_xp"] + bucket["lte"] * 10,
    }


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
                found.append((character_key(folder.name), path))
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


def summarize_day(day: date) -> dict:
    chars: dict[str, dict] = defaultdict(empty_char)
    pairs = files_for_date(day)
    blink_state: dict[str, bool] = {}
    for char, path in pairs:
        blink_state[char] = parse_file(
            path, chars[char], blink_state.get(char, False), character=char
        )
    characters = {k: finalize_char(v) for k, v in chars.items()}
    totals = empty_char()
    loot_keys = (
        "searches",
        "silver_corpses",
        "silver_boxes",
        "silver_jeweler",
        "silver_pawn",
        "silver_alchemist",
        "silver_furrier",
        "silver_promissory",
        "locksmith_fees",
        "dust_motes",
        "skins_yielded",
        "skins_botched",
        "skins_sold",
        "skins_pending",
        "boxes_found",
        "lte",
        "roundtime_sec",
        "roundtime_events",
        "cast_rt_sec",
        "stuns",
        "stun_rounds",
        "herbs",
        "incoming_hits",
        "incoming_misses",
        "outgoing_hits",
        "outgoing_misses",
        "damage_taken",
        "damage_dealt",
        "outmaneuvers",
        "bounty_turnins",
        "bounty_points",
        "bounty_xp",
        "bounty_silver",
        "bounty_tasks_done",
        "lte_converted",
        "hunting_room_lines",
        "gem_eligible",
        "gemstones_found",
        "fried_mentions",
        "gemstone_geodes_found",
        "attacks_blink_up",
        "attacks_blink_down",
        "hits_blink_up",
        "hits_blink_down",
        "damage_blink_up",
        "damage_blink_down",
        "ebp_blink_up",
        "ebp_blink_down",
        "attacks_clean",
        "attacks_impaired",
        "ebp_clean",
        "ebp_impaired",
        "hits_clean",
        "hits_impaired",
        "impaired_stun",
        "impaired_prone",
        "impaired_webbed",
        "impaired_unconscious",
        "impaired_rooted",
        "deaths",
    )
    for c in characters.values():
        totals["incoming_attacks"] += c["incoming_attacks"]
        totals["evades"] += c["evades"]
        totals["blocks"] += c["blocks"]
        totals["parries"] += c["parries"]
        totals["blink_casts"] += c["blink_casts"]
        totals["blink_fires"] += c["blink_fires"]
        totals["blink_successes"] += c["blink_successes"]
        totals["line_count"] += c["line_count"]
        for msg, n in c["evade_messages"].items():
            totals["evade_messages"][msg] += n
        for k in loot_keys:
            totals[k] += c[k]
        totals["dust_reserve_snapshots"].extend(c["dust_reserve_snapshots"])
        # ~lootcap is account-wide; merge all BANK ACCOUNT snapshots then re-derive.
        totals["lootcap_snapshots"].extend(c.get("lootcap_snapshots") or [])
        if c.get("gem_fixate"):
            totals["gem_fixate"] = True
        if c.get("gem_fixate_activated"):
            totals["gem_fixate_activated"] = True
        if c.get("gem_fixate_blocked"):
            totals["gem_fixate_blocked"] = True
        totals["skin_sales"].extend(c["skin_sales"])
        for name, n in c["skins_by_type"].items():
            totals["skins_by_type"][name] += n
        for name, n in c.get("skins_by_creature", {}).items():
            totals["skins_by_creature"][name] += n
        for name, n in c["interesting_items"].items():
            totals["interesting_items"][name] += n
        for name, n in c.get("searches_by_creature", {}).items():
            totals["searches_by_creature"][name] += n
        for name, n in c.get("sigils", {}).items():
            totals["sigils"][name] += n
        for name, n in c.get("spells_prepped", {}).items():
            totals["spells_prepped"][name] += n
        for name, n in c.get("rooms_top", {}).items():
            totals["rooms"][name] += n
    fin = finalize_char(totals)
    fin.pop("files", None)
    fin.pop("sessions", None)
    return {
        "date": day.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file_count": len(pairs),
        "characters": characters,
        "totals": fin,
        "notes": {
            "evade": "Player evade/dodge lines only (not creature evades).",
            "ebp": "Block + parry + evade/dodge as a share of incoming attacks. Block is shield-only (buckler/shield/targe/aegis); weapon fend-off counts as parry. EBP% clean is the apples-to-apples rate while not stunned/prone/webbed/unconscious/rooted — compare to in-game dodge/EBP expectations.",
            "blink": "1215 uptime tracked from cast eruption through splinter fade / successful blink. pct_hits_while_blink_down is the share of landed hits that occurred with Blink down — lower is better.",
            "impaired": "Incoming attacks while stunned, prone, webbed, unconscious, or rooted. EBP% impaired vs EBP% clean shows how much status tanks avoidance.",
            "deaths": "Unique player deaths (rising edge of 'You are a ghost!' / Deaths-channel self line), not every DEAD> prompt.",
            "silver": "Gross = corpse coins + box coins + jeweler + pawn + Cendadric + furrier + promissory notes. Net subtracts locksmith pool fees. Bank teller change ignored. This is event-sum loot, not wealth delta.",
            "lootcap": (
                "silver_loot_actual = log-event loot/sales/bounty silver for the day "
                "(always available). silver_loot_toward_cap = last − first ledger "
                "~lootcap snapshot the same day (needs ≥2 BANK ACCOUNT checks). "
                "lootcap_used is ledger's ~lootcap (deposits <1M this week, account-wide). "
                f"lootcap_remaining uses assumed ceiling {WEEKLY_LOOT_CAP:,} (unpublished "
                "by Simu — treat as a working guess). silver_loot_week_actual / "
                "lootcap_week_peak are Sunday–Saturday week-to-date; when peak stalls "
                "while actual keeps rising, the real soft-cap is near that peak. "
                "gem_fixate marks GEM FIXATE active that day (status/activate lines); "
                "gemstone_geodes_found counts self geode drops (ends fixate). "
                f"Assumed fixate≈{FIXATE_CAP_RATIO:.0%} of normal cap for extrapolation."
            ),
            "dust": "Gemstone Dust motes gathered from the ground.",
            "skins": "Yields match 'You skinned the <creature>, yielding <product>.' Furrier silver includes any town furrier bulk cash/note sale plus single-skin accepts. est_silver_per_skin = silver_furrier / skins_sold (skins still in the cloak at day end are skins_pending).",
            "hunt": "Searches-by-creature, RT, stuns, herbs, bounty turn-ins, LTE convert, gemstone eligibility.",
            "xp_est": "Bounty XP plus loot LTE × 10 (field XP is not logged per kill; LTE is ~1/10 of field).",
        },
    }


def write_day(doc: dict) -> Path:
    SUMMARY_ROOT.mkdir(parents=True, exist_ok=True)
    path = SUMMARY_ROOT / f"{doc['date']}.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def load_all_summaries() -> list[dict]:
    docs = []
    if not SUMMARY_ROOT.is_dir():
        return docs
    for path in sorted(SUMMARY_ROOT.glob("20*.json")):
        try:
            docs.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return docs


def fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}%"


def fmt_num(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return f"{int(v):,}"


def render_index(docs: list[dict], lootcap_discovery: dict | None = None) -> str:
    payload = json.dumps(docs)
    discovery = json.dumps(lootcap_discovery or {})
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Saga log summaries</title>
<style>
  :root {{
    --bg: #12110f; --card: #1c1a17; --ink: #e8e0d0; --muted: #9a8f7c;
    --accent: #c4a35a; --line: #3a342c;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: "Iosevka", "JetBrains Mono", ui-monospace, monospace;
    background: var(--bg); color: var(--ink); line-height: 1.45; padding: 1.5rem;
  }}
  h1 {{ font-weight: 600; font-size: 1.4rem; color: var(--accent); margin: 0 0 .3rem; }}
  p.sub {{ color: var(--muted); margin: 0 0 1rem; font-size: .9rem; }}
  .toggles {{ display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 1rem; }}
  .toggles button {{
    background: var(--card); color: var(--ink); border: 1px solid var(--line);
    padding: .35rem .7rem; font: inherit; cursor: pointer;
  }}
  .toggles button:hover {{ border-color: var(--accent); color: var(--accent); }}
  .toggles button.active {{ background: var(--accent); color: var(--bg); border-color: var(--accent); }}
  .chart-wrap {{
    position: relative;
    width: 100%;
    max-width: 100%;
  }}
  /* Custom always-on horizontal bars — native overlay scrollbars fade on this desktop. */
  .hbar {{
    height: 18px;
    background: var(--line);
    border: 1px solid #4a4338;
    border-radius: 9px;
    position: relative;
    cursor: pointer;
    flex-shrink: 0;
    margin: 0 0 6px;
    overflow: hidden;
  }}
  .hbar.bottom {{
    margin: 6px 0 0;
    position: sticky;
    bottom: 0;
    z-index: 2;
  }}
  .hbar.top {{
    position: sticky;
    top: 0;
    z-index: 2;
    background: var(--line);
  }}
  .hbar-thumb {{
    position: absolute;
    top: 2px;
    left: 0;
    height: calc(100% - 4px);
    min-width: 48px;
    background: var(--accent);
    border-radius: 7px;
    cursor: grab;
    box-sizing: border-box;
  }}
  .hbar-thumb:active {{ cursor: grabbing; }}
  .hbar.disabled {{
    opacity: 0.35;
    pointer-events: none;
  }}
  .hbar.disabled .hbar-thumb {{ display: none; }}
  /* Hide native bars; scrolling still works via drag / shift-wheel / custom thumbs. */
  .scroll {{
    width: 100%;
    max-width: 100%;
    overflow-x: auto;
    overflow-y: auto;
    cursor: default;
    max-height: calc(100vh - 16rem);
    scrollbar-width: none; /* Firefox */
  }}
  .scroll::-webkit-scrollbar {{
    width: 0;
    height: 0;
    display: none;
  }}
  .scroll.panning {{ cursor: grabbing; user-select: none; }}
  table {{ border-collapse: collapse; width: max-content; min-width: 100%; background: var(--card); }}
  th, td {{ border-bottom: 1px solid var(--line); padding: .45rem .6rem; text-align: right; white-space: nowrap; }}
  th:first-child, td:first-child {{ text-align: left; }}
  table.show-char th:nth-child(2), table.show-char td:nth-child(2) {{ text-align: left; }}
  th {{ color: var(--muted); font-weight: 500; font-size: .8rem; cursor: pointer; user-select: none; }}
  th:hover {{ color: var(--accent); }}
  th.sorted {{ color: var(--accent); }}
  tr:hover td {{ background: #26231e; }}
  .note {{ margin-top: 1.2rem; color: var(--muted); font-size: .85rem; max-width: 70rem; }}
  code {{ color: var(--accent); }}
</style>
</head>
<body>
  <h1>Saga log summaries</h1>
  <p class="sub">Derived from <code>~/.config/saga/logs</code>. EBP% clean ≈ unmodified avoidance; lower <b>Hits blink-down %</b> means fewer hits during 1215 downtime. Click a column header to sort. Gold bars at top and bottom pan the table; middle-drag or Shift+wheel also work.</p>
  <div class="toggles" id="periods"></div>
  <div class="toggles" id="toggles"></div>
  <div class="chart-wrap">
  <div class="hbar top" id="hbar-top" title="Scroll table sideways"><div class="hbar-thumb" id="hbar-top-thumb"></div></div>
  <div class="scroll" id="scroll-main">
  <table id="days">
    <thead></thead>
    <tbody></tbody>
  </table>
  </div>
  <div class="hbar bottom" id="hbar-bottom" title="Scroll table sideways"><div class="hbar-thumb" id="hbar-bottom-thumb"></div></div>
  </div>
  <p class="note">
    EBP% clean = evade/block/parry while not stunned/prone/webbed/unconscious/rooted (best compare to in-game dodge).
    Hits blink-down % = share of landed hits that occurred with 1215 down (goal: lower this).
    Hit rate blink up/down and EBP% impaired split the same attacks another way.
    XP is bounty experience plus loot LTE × 10. Silver net = loot/sales − locksmith fees (event sum, not wealth).
    <b>Loot actual</b> = same event-sum loot for the day (always available from the log).
    <b>Loot toward (ledger)</b> = change in ledger ~lootcap between the first and last BANK ACCOUNT check that day.
    <b>Lootcap used / left</b> = latest ~lootcap vs assumed {WEEKLY_LOOT_CAP:,} ceiling (Simu has not published the real number).
    <b>Week actual / Week peak</b> = Sunday–Saturday week-to-date actual loot and highest ~lootcap seen; when peak stalls while actual keeps rising, the real soft-cap is near that peak.
    <b>Fixate</b> phase: <code>active</code> (status on), <code>geode</code> (found that day), <code>post</code> (after geode in a week that started fixate — status usually clear; silver effect may still be week-long), or —.
    <b>Geodes</b> = self crystalline-geode finds. Wiki says find-bonus ends on geode; whether silver soft-cap reduction also ends is tested in the subtitle (pre vs post vs never-fixate actual/search).
    Est/skin = furrier silver ÷ skins sold (bulk cloak sales at any furrier, promissory notes, and single-skin sells).
  </p>
  <p class="sub" id="lootcap-obs"></p>
  <h2 style="color:var(--accent);font-size:1.1rem;margin-top:1.5rem;">Creature searches</h2>
  <table>
    <thead><tr><th>Creature</th><th>Searches</th></tr></thead>
    <tbody id="creatures"></tbody>
  </table>
  <h2 style="color:var(--accent);font-size:1.1rem;margin-top:1.5rem;">Skins by product</h2>
  <table>
    <thead><tr><th>Product</th><th>Yielded</th></tr></thead>
    <tbody id="skins-type"></tbody>
  </table>
  <h2 style="color:var(--accent);font-size:1.1rem;margin-top:1.5rem;">Skins by creature</h2>
  <table>
    <thead><tr><th>Creature</th><th>Yielded</th></tr></thead>
    <tbody id="skins-creature"></tbody>
  </table>
  <script type="application/json" id="data">{payload}</script>
  <script type="application/json" id="lootcap-discovery">{discovery}</script>
  <script>
    const days = JSON.parse(document.getElementById("data").textContent);
    const lootcapDiscovery = JSON.parse(document.getElementById("lootcap-discovery").textContent || "{{}}");
    const names = [...new Set(days.flatMap(d => Object.keys(d.characters || {{}})))].sort();
    const KEY = "saga-log-char";
    const PERIOD_KEY = "saga-log-period";
    let selected = localStorage.getItem(KEY) || "all";
    if (selected !== "all" && !names.includes(selected)) selected = "all";
    let period = localStorage.getItem(PERIOD_KEY) || "day";
    if (!["day","week","month"].includes(period)) period = "day";

    function pct(v) {{ return v == null ? "—" : Number(v).toFixed(2) + "%"; }}
    function num(v) {{
      if (v == null) return "—";
      if (typeof v === "number" && !Number.isInteger(v)) return v.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
      return Number(v).toLocaleString();
    }}
    function stats(d) {{
      if (selected === "all") return d.totals || {{}};
      return (d.characters && d.characters[selected]) || null;
    }}
    function xp(t) {{
      if (t.xp_est != null) return t.xp_est;
      return (t.bounty_xp || 0) + 10 * (t.lte || 0);
    }}
    function esc(s) {{
      return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    }}

    function ymd(d) {{
      return d.getFullYear() + "-" + String(d.getMonth()+1).padStart(2,"0") + "-" + String(d.getDate()).padStart(2,"0");
    }}
    function parseDay(iso) {{
      const [y,m,dd] = iso.split("-").map(Number);
      return new Date(y, m-1, dd);
    }}
    function weekStart(iso) {{
      const d = parseDay(iso);
      const dow = (d.getDay() + 6) % 7;
      d.setDate(d.getDate() - dow);
      return ymd(d);
    }}
    function weekLabel(start) {{
      const a = parseDay(start);
      const b = new Date(a);
      b.setDate(a.getDate() + 6);
      const fmt = d => d.toLocaleString(undefined, {{month:"short", day:"numeric"}});
      return fmt(a) + " – " + fmt(b) + " " + b.getFullYear();
    }}
    function monthKey(iso) {{ return iso.slice(0, 7); }}
    function monthLabel(ym) {{
      const [y,m] = ym.split("-").map(Number);
      return new Date(y, m-1, 1).toLocaleString(undefined, {{month:"long", year:"numeric"}});
    }}

    const SUM_KEYS = ["incoming_attacks","evades","blocks","parries","blink_casts","blink_fires",
      "blink_successes","line_count","searches","silver_corpses","silver_boxes",
      "silver_jeweler","silver_pawn","silver_alchemist","silver_furrier","silver_promissory",
      "locksmith_fees","silver_gross","silver_net","dust_motes","skins_yielded","skins_botched",
      "skins_sold","skins_pending","boxes_found","lte","roundtime_sec","roundtime_events","cast_rt_sec","stuns","stun_rounds",
      "herbs","incoming_hits","incoming_misses","outgoing_hits","outgoing_misses","damage_taken",
      "damage_dealt","outmaneuvers","bounty_turnins","bounty_points","bounty_xp","bounty_silver",
      "bounty_tasks_done","lte_converted","hunting_room_lines","gem_eligible","gemstones_found",
      "fried_mentions","attacks_blink_up","attacks_blink_down","hits_blink_up","hits_blink_down",
      "damage_blink_up","damage_blink_down","ebp_blink_up","ebp_blink_down","attacks_clean",
      "attacks_impaired","ebp_clean","ebp_impaired","hits_clean","hits_impaired",
      "impaired_stun","impaired_prone","impaired_webbed","impaired_unconscious","impaired_rooted","deaths"];

    function mergeStats(list) {{
      const t = {{}};
      for (const k of SUM_KEYS) t[k] = 0;
      const creatures = {{}};
      for (const s of list) {{
        if (!s) continue;
        for (const k of SUM_KEYS) t[k] += Number(s[k] || 0);
        const m = s.searches_by_creature || {{}};
        for (const [name, n] of Object.entries(m)) creatures[name] = (creatures[name]||0) + n;
      }}
      const incoming = t.incoming_attacks;
      const hits = t.incoming_hits || 0;
      t.evade_pct_of_attacks = incoming ? 100 * t.evades / incoming : null;
      t.block_pct_of_attacks = incoming ? 100 * t.blocks / incoming : null;
      t.parry_pct_of_attacks = incoming ? 100 * t.parries / incoming : null;
      t.ebp_events = t.evades + t.blocks + t.parries;
      t.ebp_pct_of_attacks = incoming ? 100 * t.ebp_events / incoming : null;
      t.blink_uptime_pct = incoming ? 100 * t.attacks_blink_up / incoming : null;
      t.hit_rate_blink_up = t.attacks_blink_up ? 100 * t.hits_blink_up / t.attacks_blink_up : null;
      t.hit_rate_blink_down = t.attacks_blink_down ? 100 * t.hits_blink_down / t.attacks_blink_down : null;
      t.pct_hits_while_blink_down = hits ? 100 * t.hits_blink_down / hits : null;
      t.pct_damage_while_blink_down = t.damage_taken ? 100 * t.damage_blink_down / t.damage_taken : null;
      t.ebp_pct_blink_up = t.attacks_blink_up ? 100 * t.ebp_blink_up / t.attacks_blink_up : null;
      t.ebp_pct_blink_down = t.attacks_blink_down ? 100 * t.ebp_blink_down / t.attacks_blink_down : null;
      t.ebp_pct_clean = t.attacks_clean ? 100 * t.ebp_clean / t.attacks_clean : null;
      t.ebp_pct_impaired = t.attacks_impaired ? 100 * t.ebp_impaired / t.attacks_impaired : null;
      t.hit_rate_clean = t.attacks_clean ? 100 * t.hits_clean / t.attacks_clean : null;
      t.hit_rate_impaired = t.attacks_impaired ? 100 * t.hits_impaired / t.attacks_impaired : null;
      t.avg_silver_per_search = t.searches ? t.silver_corpses / t.searches : null;
      const sold = t.skins_sold || 0;
      t.est_silver_per_skin = sold ? t.silver_furrier / sold
        : (t.skins_yielded ? t.silver_furrier / t.skins_yielded : null);
      t.xp_est = (t.bounty_xp || 0) + 10 * (t.lte || 0);
      // Account-wide lootcap: sum actual + ledger toward; keep latest used / max peak.
      let towardSum = 0, hasToward = false, used = null, actualSum = 0, peak = null;
      let fixate = false, geodes = 0, blocked = false;
      for (const s of list) {{
        if (!s) continue;
        actualSum += Number(s.silver_loot_actual != null ? s.silver_loot_actual : (s.silver_gross || 0));
        if (s.silver_loot_toward_cap != null) {{
          towardSum += Number(s.silver_loot_toward_cap);
          hasToward = true;
        }}
        if (s.lootcap_used != null) {{
          const u = Number(s.lootcap_used);
          used = u;
          peak = peak == null ? u : Math.max(peak, u);
        }}
        if (s.lootcap_week_peak != null) {{
          const p = Number(s.lootcap_week_peak);
          peak = peak == null ? p : Math.max(peak, p);
        }}
        if (s.gem_fixate || s.gem_fixate_activated || s.gem_fixate_week) fixate = true;
        geodes += Number(s.gemstone_geodes_found || 0);
        if (s.gem_fixate_blocked || s.gem_fixate_blocked_week) blocked = true;
      }}
      t.silver_loot_actual = actualSum;
      t.silver_loot_toward_cap = hasToward ? towardSum : null;
      t.silver_loot_week_actual = actualSum;
      t.lootcap_used = used;
      t.lootcap_week_peak = peak;
      t.lootcap_weekly_cap = {WEEKLY_LOOT_CAP};
      t.lootcap_remaining = used != null ? ({WEEKLY_LOOT_CAP} - used) : null;
      t.gem_fixate = fixate;
      t.gem_fixate_week = fixate;
      t.gemstone_geodes_found = geodes;
      t.gemstone_geodes_week = geodes;
      t.gem_fixate_blocked = blocked;
      t.searches_by_creature = creatures;
      return t;
    }}

    function groupedRows() {{
      if (period === "day") {{
        return [...days].reverse().map(d => {{
          const t = stats(d);
          if (!t) return null;
          return {{
            label: d.date || "",
            chars: Object.keys(d.characters || {{}}).join(", ") || "—",
            t
          }};
        }}).filter(Boolean);
      }}
      const buckets = new Map();
      for (const d of days) {{
        const t = stats(d);
        if (!t) continue;
        const key = period === "week" ? weekStart(d.date) : monthKey(d.date);
        if (!buckets.has(key)) buckets.set(key, {{ days: [], stats: [] }});
        const b = buckets.get(key);
        b.days.push(d);
        b.stats.push(t);
      }}
      const keys = [...buckets.keys()].sort().reverse();
      return keys.map(key => {{
        const b = buckets.get(key);
        const chars = [...new Set(b.days.flatMap(d => Object.keys(d.characters || {{}})))].join(", ") || "—";
        return {{
          label: period === "week" ? weekLabel(key) : monthLabel(key),
          chars,
          t: mergeStats(b.stats)
        }};
      }});
    }}

    function bindButtons(box, items, current, onPick) {{
      box.innerHTML = "";
      items.forEach(([id, label]) => {{
        const b = document.createElement("button");
        b.textContent = label;
        b.className = id === current ? "active" : "";
        b.addEventListener("click", () => onPick(id));
        box.appendChild(b);
      }});
    }}

    function renderToggles() {{
      bindButtons(document.getElementById("periods"),
        [["day","Daily"],["week","Weekly"],["month","Monthly"]],
        period,
        id => {{ period = id; localStorage.setItem(PERIOD_KEY, period); render(); }});
      bindButtons(document.getElementById("toggles"),
        [["all", "All"], ...names.map(n => [n, n.replace(" - Prime", "")])],
        selected,
        id => {{ selected = id; localStorage.setItem(KEY, selected); render(); }});
    }}

    function renderCountTable(id, field) {{
      const c = {{}};
      for (const d of days) {{
        const t = stats(d);
        if (!t) continue;
        const m = t[field] || {{}};
        for (const [k,v] of Object.entries(m)) c[k] = (c[k]||0)+v;
      }}
      const body = document.getElementById(id);
      body.innerHTML = "";
      const rows = Object.entries(c).sort((a,b)=>b[1]-a[1]);
      if (!rows.length) {{
        const tr = document.createElement("tr");
        tr.innerHTML = "<td colspan='2'>None for this filter.</td>";
        body.appendChild(tr);
        return;
      }}
      rows.forEach(([k,v]) => {{
        const tr = document.createElement("tr");
        tr.innerHTML = "<td>"+esc(k)+"</td><td>"+num(v)+"</td>";
        body.appendChild(tr);
      }});
    }}
    function renderCreatures() {{ renderCountTable("creatures", "searches_by_creature"); }}
    function renderSkins() {{
      renderCountTable("skins-type", "skins_by_type");
      renderCountTable("skins-creature", "skins_by_creature");
    }}

    function renderTable() {{
      const table = document.getElementById("days");
      const showChar = selected === "all";
      table.classList.toggle("show-char", showChar);
      const heads = [period === "week" ? "Week" : period === "month" ? "Month" : "Date"];
      if (showChar) heads.push("Characters");
      heads.push("Incoming","Deaths","Evade %","Block %","Parry %","EBP %","EBP% clean","EBP% impaired",
        "Blink uptime %","Hits blink-down %","Hit rate blink-up","Hit rate blink-down",
        "Dmg blink-down %","Blink fire / ok","Impaired atks","Silver net","Loot actual",
        "Loot toward (ledger)","Lootcap used","Lootcap left","Week actual","Week peak",
        "Fixate","Geodes","Avg silvers/search","XP",
        "Skins ok/botch","Est/skin","Boxes","Searches","Bounties","Bounty XP","RT sec",
        "Stuns","Dmg taken","Jewel/dust");
      table.tHead.innerHTML = "<tr>" + heads.map(h => "<th>"+h+"</th>").join("") + "</tr>";
      const tbody = table.tBodies[0];
      tbody.innerHTML = "";
      const rows = [];
      for (const row of groupedRows()) {{
        const t = row.t;
        const cells = [row.label];
        if (showChar) cells.push(row.chars);
        const lootActual = t.silver_loot_actual != null ? t.silver_loot_actual : t.silver_gross;
        let phase = t.gem_fixate_phase || null;
        if (!phase && (t.gem_fixate || t.gem_fixate_activated)) phase = "active";
        const fixateLabel = phase === "active" ? "active"
          : phase === "geode_day" ? "geode"
          : phase === "post_geode" ? "post"
          : "—";
        const geodes = t.gemstone_geodes_found != null ? t.gemstone_geodes_found
          : (t.gemstone_geodes_week || 0);
        cells.push(
          t.incoming_attacks || 0,
          t.deaths || 0,
          pct(t.evade_pct_of_attacks),
          pct(t.block_pct_of_attacks),
          pct(t.parry_pct_of_attacks),
          pct(t.ebp_pct_of_attacks),
          pct(t.ebp_pct_clean),
          pct(t.ebp_pct_impaired),
          pct(t.blink_uptime_pct),
          pct(t.pct_hits_while_blink_down),
          pct(t.hit_rate_blink_up),
          pct(t.hit_rate_blink_down),
          pct(t.pct_damage_while_blink_down),
          (t.blink_fires || 0) + " / " + (t.blink_successes || 0),
          t.attacks_impaired || 0,
          num(t.silver_net),
          num(lootActual),
          num(t.silver_loot_toward_cap),
          num(t.lootcap_used),
          num(t.lootcap_remaining),
          num(t.silver_loot_week_actual),
          num(t.lootcap_week_peak),
          fixateLabel,
          geodes || 0,
          num(t.avg_silver_per_search),
          num(xp(t)),
          (t.skins_yielded || 0) + "/" + (t.skins_botched || 0),
          num(t.est_silver_per_skin),
          t.boxes_found || 0,
          t.searches || 0,
          t.bounty_turnins || 0,
          num(t.bounty_xp),
          t.roundtime_sec || 0,
          t.stuns || 0,
          num(t.damage_taken),
          (t.gemstones_found || 0) + "/" + (t.dust_motes || 0)
        );
        const tr = document.createElement("tr");
        tr.innerHTML = cells.map(v => "<td>"+esc(v)+"</td>").join("");
        rows.push(tr);
      }}
      if (!rows.length) {{
        const tr = document.createElement("tr");
        tr.innerHTML = "<td colspan='" + heads.length + "'>No days for this character.</td>";
        tbody.appendChild(tr);
      }} else {{
        rows.forEach(r => tbody.appendChild(r));
      }}
      table.querySelectorAll("thead th").forEach((th, col) => {{
        th.addEventListener("click", () => {{
          const dir = th.dataset.dir === "asc" ? "desc" : "asc";
          table.querySelectorAll("th").forEach(h => {{ h.dataset.dir = ""; h.classList.remove("sorted"); }});
          th.dataset.dir = dir;
          th.classList.add("sorted");
          const bodyRows = Array.from(tbody.rows);
          bodyRows.sort((a, b) => {{
            const av = cellKey(a.cells[col], col);
            const bv = cellKey(b.cells[col], col);
            if (av < bv) return dir === "asc" ? -1 : 1;
            if (av > bv) return dir === "asc" ? 1 : -1;
            return 0;
          }});
          bodyRows.forEach(r => tbody.appendChild(r));
        }});
      }});
    }}

    function cellKey(td, i) {{
      const t = (td.textContent || "").replace(/,/g, "").trim();
      if (t === "—" || t === "") return i < 2 ? "" : -Infinity;
      if (t.includes("/")) {{
        const [a,b] = t.split("/").map(Number);
        return (a || 0) * 100000 + (b || 0);
      }}
      const n = parseFloat(t);
      return Number.isNaN(n) ? t.toLowerCase() : n;
    }}

    function renderLootcapObs() {{
      let peak = null;
      for (const d of days) {{
        const buckets = [d.totals || {{}}, ...Object.values(d.characters || {{}})];
        for (const s of buckets) {{
          if (!s) continue;
          for (const k of ["lootcap_used", "lootcap_week_peak"]) {{
            if (s[k] != null) peak = peak == null ? Number(s[k]) : Math.max(peak, Number(s[k]));
          }}
        }}
      }}
      const el = document.getElementById("lootcap-obs");
      if (!el) return;
      const parts = [];
      if (peak == null) {{
        parts.push("No ledger ~lootcap snapshots in the loaded days yet.");
      }} else {{
        parts.push("Observed max ~lootcap: " + Number(peak).toLocaleString()
          + " (assumed ceiling " + Number({WEEKLY_LOOT_CAP}).toLocaleString() + ").");
      }}
      const disc = lootcapDiscovery || {{}};
      const models = disc.fixate_duration_models || {{}};
      const weekLong = models.week_long || {{}};
      const endsOn = models.ends_on_geode || {{}};
      if (disc.fixate_duration_verdict) {{
        const label = {{
          supports_week_long: "leans week-long silver effect",
          supports_ends_on_geode: "leans ends-on-geode for silver",
          inconclusive: "inconclusive on fixate duration"
        }}[disc.fixate_duration_verdict] || disc.fixate_duration_verdict;
        parts.push("Fixate duration: " + label + ".");
        if (disc.fixate_duration_reason) parts.push(disc.fixate_duration_reason);
      }}
      const fmtRate = (r) => r && r.actual_per_search != null
        ? (Number(r.actual_per_search).toLocaleString() + "/search over "
          + Number(r.searches).toLocaleString() + " searches")
        : null;
      const rates = [];
      const pre = fmtRate(disc.pre_geode_rate);
      const post = fmtRate(disc.post_geode_rate);
      const never = fmtRate(disc.never_fixate_rate);
      if (pre) rates.push("pre-geode " + pre);
      if (post) rates.push("post-geode " + post);
      if (never) rates.push("never-fixate " + never);
      if (rates.length) parts.push("Rates: " + rates.join("; ") + ".");
      if (disc.post_geode_days != null) {{
        parts.push("Post-geode days: " + disc.post_geode_days
          + " (status still active on "
          + Number(disc.status_messaging_after_geode_days || 0) + ").");
      }}
      if (weekLong.committed_week_peak_max != null) {{
        parts.push("Week-long model peak (any day after fixate started): "
          + Number(weekLong.committed_week_peak_max).toLocaleString() + ".");
      }}
      if (endsOn.fixate_day_peak_max != null) {{
        parts.push("Ends-on-geode model status-on peak: "
          + Number(endsOn.fixate_day_peak_max).toLocaleString()
          + "; status-clear peak: "
          + (endsOn.clear_day_peak_max != null
            ? Number(endsOn.clear_day_peak_max).toLocaleString() : "—") + ".");
      }}
      if (disc.estimated_normal_cap_from_fixate != null) {{
        parts.push("Est. normal cap @"
          + Number(disc.assumed_fixate_ratio || 0.5).toLocaleString(undefined, {{style:"percent"}})
          + " of fixate peak: "
          + Number(disc.estimated_normal_cap_from_fixate).toLocaleString() + ".");
      }}
      el.textContent = parts.join(" ");
    }}

    function render() {{
      renderToggles();
      renderTable();
      renderCreatures();
      renderSkins();
      renderLootcapObs();
      syncHBars();
    }}

    const scrollMain = document.getElementById("scroll-main");
    const hbars = [
      {{ bar: document.getElementById("hbar-top"), thumb: document.getElementById("hbar-top-thumb") }},
      {{ bar: document.getElementById("hbar-bottom"), thumb: document.getElementById("hbar-bottom-thumb") }},
    ];

    function syncHBars() {{
      const max = Math.max(0, scrollMain.scrollWidth - scrollMain.clientWidth);
      const track = hbars[0].bar.clientWidth || 1;
      const ratio = scrollMain.clientWidth / Math.max(scrollMain.scrollWidth, 1);
      const thumbW = Math.max(48, Math.min(track, Math.round(track * ratio)));
      const maxLeft = Math.max(0, track - thumbW);
      const left = max > 0 ? Math.round((scrollMain.scrollLeft / max) * maxLeft) : 0;
      for (const {{ bar, thumb }} of hbars) {{
        if (max <= 0) {{
          bar.classList.add("disabled");
          continue;
        }}
        bar.classList.remove("disabled");
        thumb.style.width = thumbW + "px";
        thumb.style.transform = "translateX(" + left + "px)";
      }}
    }}

    scrollMain.addEventListener("scroll", syncHBars);
    window.addEventListener("resize", syncHBars);

    let hDrag = null;
    function bindHBar({{ bar, thumb }}) {{
      thumb.addEventListener("mousedown", (e) => {{
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const max = Math.max(0, scrollMain.scrollWidth - scrollMain.clientWidth);
        const maxLeft = Math.max(0, bar.clientWidth - thumb.offsetWidth);
        hDrag = {{
          startX: e.clientX,
          startLeft: scrollMain.scrollLeft,
          max,
          maxLeft,
        }};
      }});
      bar.addEventListener("mousedown", (e) => {{
        if (e.button !== 0 || e.target === thumb) return;
        e.preventDefault();
        const max = Math.max(0, scrollMain.scrollWidth - scrollMain.clientWidth);
        if (max <= 0) return;
        const rect = bar.getBoundingClientRect();
        const thumbW = thumb.offsetWidth;
        const maxLeft = Math.max(0, bar.clientWidth - thumbW);
        const x = e.clientX - rect.left - thumbW / 2;
        const left = Math.max(0, Math.min(maxLeft, x));
        scrollMain.scrollLeft = maxLeft > 0 ? (left / maxLeft) * max : 0;
      }});
    }}
    hbars.forEach(bindHBar);
    window.addEventListener("mousemove", (e) => {{
      if (!hDrag) return;
      const dx = e.clientX - hDrag.startX;
      const delta = hDrag.maxLeft > 0 ? (dx / hDrag.maxLeft) * hDrag.max : 0;
      scrollMain.scrollLeft = hDrag.startLeft + delta;
    }});
    window.addEventListener("mouseup", () => {{ hDrag = null; }});

    // Middle-button drag pans horizontally; Shift+wheel also pans.
    let pan = null;
    scrollMain.addEventListener("mousedown", (e) => {{
      if (e.button !== 1) return;
      e.preventDefault();
      pan = {{ x: e.clientX, left: scrollMain.scrollLeft }};
      scrollMain.classList.add("panning");
    }});
    window.addEventListener("mousemove", (e) => {{
      if (!pan) return;
      scrollMain.scrollLeft = pan.left - (e.clientX - pan.x);
    }});
    window.addEventListener("mouseup", (e) => {{
      if (e.button !== 1 && !pan) return;
      pan = null;
      scrollMain.classList.remove("panning");
    }});
    scrollMain.addEventListener("auxclick", (e) => {{
      if (e.button === 1) e.preventDefault();
    }});
    scrollMain.addEventListener("wheel", (e) => {{
      if (!e.shiftKey) return;
      if (Math.abs(e.deltaY) < Math.abs(e.deltaX)) return;
      e.preventDefault();
      scrollMain.scrollLeft += e.deltaY;
    }}, {{ passive: false }});

    render();
    requestAnimationFrame(() => {{
      syncHBars();
      setTimeout(syncHBars, 100);
      setTimeout(syncHBars, 500);
    }});
  </script>
</body>
</html>
"""


def rebuild_index() -> Path:
    docs = load_all_summaries()
    discovery = enrich_lootcap_week_stats(docs)
    html_path = SUMMARY_ROOT / "index.html"
    html_path.write_text(render_index(docs, discovery), encoding="utf-8")
    observed = [
        int(u)
        for d in docs
        for src in [d.get("totals") or {}, *(d.get("characters") or {}).values()]
        for u in [src.get("lootcap_used"), src.get("lootcap_week_peak")]
        if u is not None
    ]
    index = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": [d["date"] for d in docs],
        "lootcap_weekly_cap_assumed": WEEKLY_LOOT_CAP,
        "lootcap_observed_max": max(observed) if observed else None,
        "lootcap_discovery": discovery,
    }
    (SUMMARY_ROOT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    return html_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Saga log daily summarizer")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--all", action="store_true", help="Backfill every dated log")
    ap.add_argument("--rebuild-index", action="store_true")
    args = ap.parse_args(argv)

    if args.rebuild_index and not args.all and not args.date:
        path = rebuild_index()
        print(path)
        return 0

    if args.all:
        days = all_dates()
        if not days:
            print("No dated logs found.", file=sys.stderr)
            rebuild_index()
            return 0
        for day in days:
            doc = summarize_day(day)
            path = write_day(doc)
            t = doc["totals"]
            print(
                f"{path.name}  files={doc['source_file_count']}  "
                f"in={t['incoming_attacks']} evade={t['evades']} "
                f"({fmt_pct(t['evade_pct_of_attacks'])})  "
                f"blink={t['blink_fires']}/{t['blink_successes']}  "
                f"blink_up={fmt_pct(t.get('blink_uptime_pct'))}  "
                f"hits_blink_down={fmt_pct(t.get('pct_hits_while_blink_down'))}  "
                f"ebp_clean={fmt_pct(t.get('ebp_pct_clean'))}  "
                f"deaths={t.get('deaths', 0)}  "
                f"silver_net={fmt_num(t.get('silver_net'))}  "
                f"loot_actual={fmt_num(t.get('silver_loot_actual'))}  "
                f"loot_toward={fmt_num(t.get('silver_loot_toward_cap'))}  "
                f"lootcap={fmt_num(t.get('lootcap_used'))}/"
                f"{fmt_num(t.get('lootcap_remaining'))} left  "
                f"avg/search={fmt_num(t.get('avg_silver_per_search'))}  "
                f"dust={t.get('dust_motes', 0)}  "
                f"skins={t.get('skins_yielded', 0)} est/skin={fmt_num(t.get('est_silver_per_skin'))}"
            )
        print(rebuild_index())
        return 0

    if args.date:
        day = date.fromisoformat(args.date)
    else:
        day = date.today() - timedelta(days=1)

    doc = summarize_day(day)
    path = write_day(doc)
    html = rebuild_index()
    # Week-to-date fields live on the enriched index payload; re-derive for CLI.
    docs = load_all_summaries()
    enrich_lootcap_week_stats(docs)
    enriched = next((d for d in docs if d.get("date") == day.isoformat()), doc)
    t = enriched.get("totals") or doc["totals"]
    print(path)
    print(
        f"files={doc['source_file_count']} incoming={t['incoming_attacks']} "
        f"evades={t['evades']} ({fmt_pct(t['evade_pct_of_attacks'])}) "
        f"blink_fires={t['blink_fires']} blink_ok={t['blink_successes']} "
        f"blink_up={fmt_pct(t.get('blink_uptime_pct'))} "
        f"hits_blink_down={fmt_pct(t.get('pct_hits_while_blink_down'))} "
        f"ebp_clean={fmt_pct(t.get('ebp_pct_clean'))} "
        f"ebp_impaired={fmt_pct(t.get('ebp_pct_impaired'))} "
        f"deaths={t.get('deaths', 0)} "
        f"silver_net={fmt_num(t.get('silver_net'))} "
        f"loot_actual={fmt_num(t.get('silver_loot_actual'))} "
        f"loot_toward={fmt_num(t.get('silver_loot_toward_cap'))} "
        f"lootcap={fmt_num(t.get('lootcap_used'))}/"
        f"{fmt_num(t.get('lootcap_remaining'))} left "
        f"week_actual={fmt_num(t.get('silver_loot_week_actual'))} "
        f"week_peak={fmt_num(t.get('lootcap_week_peak'))} "
        f"fixate={'yes' if t.get('gem_fixate') else 'no'} "
        f"geodes={t.get('gemstone_geodes_found') or 0}"
    )
    print(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
