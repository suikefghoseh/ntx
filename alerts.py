#!/usr/bin/env python3
"""Breakout alerts: judges the Hype Radar list and the Roblox tracker's summary, and writes alerts.json for the ntfy relay."""
import datetime
import json
import os
import sys
import urllib.request

UTC = datetime.timezone.utc
ROBLOX_URL = "https://raw.githubusercontent.com/suikefghoseh/syafeyxkmdet/data/summary.json"
KEEP = 40              # alerts kept in the file; the relay sends each id once
QUIET_HOURS = 72       # the same trend or game is not alerted again within this long
# Hype Radar: a trend that enters the list already mainstream, or gains this much popularity in about a day.
HYPE_NEW_POP = 70
HYPE_JUMP = 15
HYPE_JUMP_POP = 60
# Roblox: a game under 30 days old passing one of these player counts, or any big game doubling in a day.
ROBLOX_STEPS = (10000, 25000, 50000, 100000, 250000, 500000, 1000000)
ROBLOX_SURGE_CCU = 20000
ROBLOX_SURGE_TIMES = 2.0
ROBLOX_STALE_HOURS = 12   # a snapshot older than this is not judged


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def hours_since(text, now):
    try:
        then = datetime.datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return 10 ** 9
    return (now - then).total_seconds() / 3600


def number(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def load(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def alert(kind, key, now, title, body, url):
    link = str(url or "")
    return {"id": f"{kind}:{key}:{now.strftime('%Y%m%d%H%M')}", "at": stamp(now), "title": clean(title, 110),
            "body": clean(body, 400), "url": link if link.startswith("https://") else ""}


def hype_alerts(rows, state, now):
    """Trends that joined the list already mainstream since the last look, or jumped in a day."""
    since = str(state.get("hype_at") or "")
    seeding = not since   # the first look only remembers what already qualifies
    sent = {rid: at for rid, at in (state.get("hype") or {}).items() if hours_since(at, now) < QUIET_HOURS}
    out = []
    for row in rows:
        rid, pop, gain = str(row.get("id") or ""), number(row.get("pop")), number(row.get("change"))
        new = bool(since) and str(row.get("first_seen") or "") > since and pop >= HYPE_NEW_POP
        jumped = gain >= HYPE_JUMP and pop >= HYPE_JUMP_POP
        if not rid or not (new or jumped or (seeding and pop >= HYPE_NEW_POP)) or rid in sent:
            continue
        sent[rid] = stamp(now)
        if seeding:
            continue
        ages = str(row.get("ages") or "").replace(",", ", ")
        lead = f"Up {gain} in a day to popularity {pop}" if jumped and not new else f"New on the list at popularity {pop}"
        out.append(alert("hype", rid, now, "Hype Radar: " + clean(row.get("title"), 80),
                         lead + (f", ages {ages}" if ages else "") + ". " + clean(row.get("what"), 220), row.get("url")))
    state["hype"], state["hype_at"] = sent, stamp(now)
    return out


def roblox_alerts(summary, state, now):
    """New games passing a player-count step, and big games that doubled in a day, in a snapshot not judged before."""
    latest = number(summary.get("latest"))
    if not latest or latest == number(state.get("roblox_latest")) or now.timestamp() - latest > ROBLOX_STALE_HOURS * 3600:
        return []
    at = {name: place for place, name in enumerate(summary["cols"])}
    seeding = "roblox" not in state
    steps = state.get("roblox") or {}
    surges = {gid: when for gid, when in (state.get("surge") or {}).items() if hours_since(when, now) < QUIET_HOURS}
    new_seconds = (number(summary.get("newGameDays")) or 30) * 86400
    kept, out = {}, []
    for game in summary["games"]:
        gid, ccu, created, before = str(game[at["id"]]), number(game[at["ccu"]]), number(game[at["created"]]), number(game[at["r24h"]])
        name = clean(game[at["name"]], 70)
        url = "https://www.roblox.com/games/" + str(number(game[at["place"]]))
        stepped = False
        if created and latest - created < new_seconds:
            reached = max([step for step in ROBLOX_STEPS if ccu >= step], default=0)
            kept[gid] = max(reached, number(steps.get(gid)))
            stepped = reached > number(steps.get(gid))
            if stepped and not seeding:
                days = max(0, (latest - created) // 86400)
                was = f", {before:,} a day ago" if before else ""
                out.append(alert("roblox", gid, now, f"Roblox: {name} passed {reached:,} players",
                                 f"{ccu:,} playing now{was}, {days} days after it was created.", url))
        if ccu >= ROBLOX_SURGE_CCU and before > 0 and ccu >= ROBLOX_SURGE_TIMES * before and gid not in surges:
            surges[gid] = stamp(now)
            if not seeding and not stepped:   # one alert per game per look
                out.append(alert("roblox-surge", gid, now, f"Roblox: {name} doubled in a day",
                                 f"{ccu:,} playing now, {before:,} a day ago.", url))
    state["roblox"], state["surge"], state["roblox_latest"] = kept, surges, latest
    return out


def fetch_summary():
    try:
        with urllib.request.urlopen(ROBLOX_URL, timeout=60) as reply:
            return json.loads(reply.read())
    except (OSError, ValueError):
        return None


def main(argv):
    if len(argv) != 2:
        sys.exit("usage: alerts.py DATA_DIR")
    path = os.path.join(argv[1], "alerts.json")
    saved = load(path, {})
    saved = saved if isinstance(saved, dict) else {}
    state = saved.get("state") if isinstance(saved.get("state"), dict) else {}
    now = datetime.datetime.now(UTC)
    found, notes = [], []
    trends = load(os.path.join(argv[1], "trends.json"), {})
    rows = trends.get("rows") if isinstance(trends, dict) else None
    if isinstance(rows, list) and rows:
        found += hype_alerts([row for row in rows if isinstance(row, dict)], state, now)
    else:
        notes.append("no trend list")
    summary = fetch_summary()
    try:
        found += roblox_alerts(summary, state, now) if summary else []
    except (KeyError, TypeError, IndexError, ValueError) as error:   # an unexpected summary must not cost the Hype alerts
        notes.append(f"Roblox summary not understood ({type(error).__name__})")
    if not summary:
        notes.append("Roblox summary not read")
    kept = [item for item in saved.get("alerts") or [] if isinstance(item, dict)] + found
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"at": stamp(now), "alerts": kept[-KEEP:], "notes": notes, "state": state}, handle, ensure_ascii=False, indent=1)
    print(f"alerts: {len(found)} new" + ("".join("; " + item["title"] for item in found)) + ("; " + "; ".join(notes) if notes else ""))


if __name__ == "__main__":
    main(sys.argv)
