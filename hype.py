#!/usr/bin/env python3
"""Helper for the hourly Hype Radar job. Prints only what the job needs, never whole files."""
import datetime
import json
import re
import sys

UTC = datetime.timezone.utc
CATS = ("tiktok", "meme", "movie-tv", "anime", "game", "music", "creator", "toy-fad", "sports", "other")
BANDS = ("5-8", "9-12", "13-18")                                 # age bands a trend is tagged with
SHORT = ("sound", "challenge", "character", "slang", "format")   # kinds of short-form video trend
MOM = {"rising": 1.0, "steady": 0.8, "fading": 0.5}   # weight of each momentum in the hot score
HALF_LIFE_DAYS = 4.0   # freshness halves every 4 days
KEEP = 80              # most rows kept
UNSEEN_DAYS = 5        # dropped when not confirmed for this long
OLD_DAYS = 45          # dropped past this age unless still rising or about to release
BUILDING_BELOW = 25    # with fewer rows than this, every run covers all focus areas
STALE_MINUTES = 180    # feeds older than this are background only
TRAIL_POINTS = 80      # popularity readings kept per row, enough for a week-on-week change
TAG_PER_RUN = 30       # rows without age bands handed to the scout in one run
ASSET = re.compile(r"[0-9a-f]{32}")
COLS = ("id", "title", "cat", "what", "angle", "evidence", "url", "pop", "mom", "fit", "started", "event",
        "first_seen", "last_seen", "checks", "change", "hot", "trail",
        "ages", "short", "keys", "change7", "rb_games", "rb_ccu", "rb_new", "rb_top")
FOCUS = {
    "A": "short-form video: TikTok, YouTube Shorts and Reels trends, sounds, dances, challenges, memes, brainrot characters and slang",
    "B": "films, series, anime and music that are about to release or just released with hype",
    "C": "games, creators, streamers, toys, collectibles, snacks and other fads",
    "D": "what 5 to 8 year olds are into: cartoons and kids' shows, family films, kids' YouTube channels, toys, tablet games, playground crazes",
}
ROTA = "ABACD"   # the focus of each hour in turn: short-form video gets two runs in five


def now_utc():
    return datetime.datetime.now(UTC)


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(text, default):
    try:
        return datetime.datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return default


def parse_date(text, default):
    try:
        return datetime.date.fromisoformat(str(text).strip())
    except ValueError:
        return default


def as_int(value, low, high, default):
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default


def clean(text, limit):
    return " ".join(str(text or "").replace("|", "/").split())[:limit]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")[:60]


def squash(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def bands_of(text):
    """The age bands named in a tag such as "5-8, 9-12" or "all", in order."""
    text = str(text or "").lower()
    if "all" in text:
        return list(BANDS)
    found = set(re.findall(r"\d+-\d+", text))
    return [band for band in BANDS if band in found]


def keys_of(text):
    """Up to four search words or phrases, lowercase letters and digits only."""
    out = []
    for part in re.split(r"[,;]", str(text or "").lower()):
        key = " ".join(re.sub(r"[^a-z0-9]+", " ", part).split())
        if (len(key) >= 3 or (key.isdigit() and len(key) == 2)) and key not in out:
            out.append(key)
    return out[:4]


def tag(row, ages, short, keys):
    bands = bands_of(ages)
    if bands:
        row["ages"] = ",".join(bands)
    kind = str(short).strip().lower()
    row["short"] = kind if kind in SHORT else ""
    row["keys"] = ",".join(keys_of(keys))


def load_rows(path):
    """The rows of the current trends file. A file that cannot be read stops the job: never start over by accident."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        sys.exit(f"PROBLEM: cannot read the current trends file ({type(error).__name__})")
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        sys.exit("PROBLEM: the current trends file has no rows list")
    return [row for row in rows if isinstance(row, dict) and row.get("id")]


def trail_of(row):
    out = []
    for part in str(row.get("trail") or "").split(","):
        ts, _, value = part.partition(":")
        if ts.isdigit() and value.isdigit():
            out.append((int(ts), int(value)))
    return out


# ------------------------------------------------------------------ brief
def load_feeds(path, now):
    """(feeds, note) from the collector's file. A missing or broken file only costs the feeds."""
    if path == "none":
        return [], "not downloaded"
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        feeds = [feed for feed in data["feeds"] if isinstance(feed, dict) and isinstance(feed.get("items"), list)]
        collected = parse_stamp(data.get("at"), None)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [], "unreadable"
    if collected is None:
        return feeds, "collection time unknown"
    age = int((now - collected).total_seconds() // 60)
    return feeds, f"collected {age} minutes ago" + (" - STALE, background only" if age > STALE_MINUTES else "")


def brief(old_path, feeds_path, out_path):
    now = now_utc()
    rows = load_rows(old_path)
    letter = ROTA[int(now.timestamp() // 3600) % len(ROTA)]
    building = len(rows) < BUILDING_BELOW
    lines = [f"NOW: {stamp(now)}"]
    if building:
        lines.append("BUILDING UP: the list is still short. Cover every focus area: " + "; ".join(f"{l} = {t}" for l, t in FOCUS.items()))
    else:
        lines.append(f"FOCUS THIS RUN: {letter} = {FOCUS[letter]}")
    mix = [f"short-form {sum(1 for r in rows if r.get('short') in SHORT)}"]
    mix += [f"ages {band} {sum(1 for r in rows if band in bands_of(r.get('ages')))}" for band in BANDS]
    lines.append(f"MIX NOW, of {len(rows)} rows: " + ", ".join(mix))
    lines += ["", f"KNOWN TRENDS: {len(rows)} (id | title | category | popularity | momentum | took off | hours since last check | ages)"]
    for row in sorted(rows, key=lambda r: -as_int(r.get("hot"), 0, 100, 0)):
        seen = parse_stamp(row.get("last_seen"), now)
        lines.append(" | ".join([str(row["id"]), clean(row.get("title"), 60), str(row.get("cat") or ""), str(row.get("pop") or 0),
                                 str(row.get("mom") or ""), str(row.get("started") or ""), str(int((now - seen).total_seconds() // 3600)),
                                 str(row.get("ages") or "?")]))
    stale = sorted(rows, key=lambda r: str(r.get("last_seen") or ""))[:8]
    lines.append("RE-RATE THESE (checked longest ago): " + (", ".join(str(r["id"]) for r in stale) or "none"))
    untagged = [str(r["id"]) for r in rows if not bands_of(r.get("ages"))][:TAG_PER_RUN]
    lines.append("TAG THESE (no age bands yet): " + (", ".join(untagged) or "none"))
    feeds, note = load_feeds(feeds_path, now)
    lines += ["", f"FEEDS ({note}; raw signals, most lines are not youth trends, keep only what fits the rules)"]
    shown = 0
    for feed in feeds:
        focus = str(feed.get("focus") or "")
        if focus and not building and letter not in focus:
            continue
        items = [clean(item, 200) for item in feed["items"][:30]]
        lines += ["", "[" + clean(feed.get("label"), 110) + "]" + ("" if items else " nothing")] + items
        shown += 1
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    focus_words = "all (building up)" if building else letter
    print(f"brief written: {len(lines)} lines, {len(rows)} known trends, focus {focus_words}, to tag {len(untagged)}, feeds shown {shown} ({note})")


# ------------------------------------------------------------------ merge and score
def score(row, now):
    today = now.date()
    age = max(0, (today - parse_date(row.get("started") or "", today)).days)
    event = parse_date(row.get("event") or "", None)
    if event is not None:
        age = min(age, abs((event - today).days))
    fresh = 0.5 ** (age / HALF_LIFE_DAYS)
    lift = 0.9 + max(-10, min(10, row.get("change") or 0)) / 100
    return round(row["pop"] * (0.3 + 0.7 * fresh) * MOM.get(row.get("mom"), 0.8) * lift)


def roblox_games(path):
    """(spaced name, tight name, name, players, created) per game in the Roblox tracker's file; None when it cannot be used."""
    if path == "none":
        return None, ""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        cols = data["cols"]
        name_at, ccu_at, made_at = cols.index("name"), cols.index("ccu"), cols.index("created")
        games = []
        for game in data["games"]:
            spaced = " " + " ".join(re.sub(r"[^a-z0-9]+", " ", str(game[name_at]).lower()).split()) + " "
            games.append((spaced, spaced.replace(" ", ""), str(game[name_at]), as_int(game[ccu_at], 0, 10 ** 9, 0), as_int(game[made_at], 0, 10 ** 11, 0)))
        read = as_int(data.get("latest"), 0, 10 ** 11, 0)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None, ""
    when = stamp(datetime.datetime.fromtimestamp(read, UTC)) if read else ""
    return (games or None), when


def named_after(key, spaced, tight):
    """Whether a game's name uses a search word: long words anywhere, short ones only as a whole word."""
    if len(key.replace(" ", "")) >= 6:
        return key.replace(" ", "") in tight
    return f" {key} " in spaced or f" {key}s " in spaced


def merge(old_path, found_path, out_path, roblox_path="none"):
    now = now_utc()
    now_ts, now_text, today = int(now.timestamp()), stamp(now), now.date()
    rows = {str(row["id"]): row for row in load_rows(old_path)}
    had = len(rows)
    titles = {squash(row.get("title", "")): rid for rid, row in rows.items()}
    added, updated, tagged, skipped, low_fit = [], [], [], [], 0
    with open(found_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) == 5 and parts[0].lower() == "tag":
                rid = slug(parts[1])
                if rid in rows:
                    tag(rows[rid], parts[2], parts[3], parts[4])
                    tagged.append(rid)
                else:
                    skipped.append(line[:50])
                continue   # a tag is not a sighting: last_seen stays
            rid = slug(parts[0])
            if len(parts) in (12, 15):
                key = squash(parts[1])
                if rid not in rows and key:
                    rid = titles.get(key, rid)
                if len(rid) < 3 or not parts[1]:
                    skipped.append(line[:50])
                    continue
                fit = as_int(parts[7], 1, 5, 3)
                row = rows.get(rid)
                if row is None:
                    if fit < 3:
                        low_fit += 1
                        continue
                    row = {"id": rid, "first_seen": now_text, "checks": 0, "trail": ""}
                    rows[rid] = row
                    titles[key] = rid
                    added.append(rid)
                else:
                    updated.append(rid)
                started = parse_date(parts[5], None)
                if started is None or started > today:
                    started = parse_date(row.get("started") or "", today)
                event = parse_date(parts[6], None)
                url = parts[11] if re.match(r"https?://\S+$", parts[11]) else str(row.get("url") or "")
                row.update({
                    "title": clean(parts[1], 80),
                    "cat": parts[2].lower() if parts[2].lower() in CATS else "other",
                    "pop": as_int(parts[3], 0, 100, 0),
                    "mom": parts[4].lower() if parts[4].lower() in MOM else "steady",
                    "started": started.isoformat(),
                    "event": event.isoformat() if event else "",
                    "fit": fit,
                    "what": clean(parts[8], 220),
                    "angle": clean(parts[9], 220),
                    "evidence": clean(parts[10], 220),
                    "url": url[:300],
                })
                if len(parts) == 15:
                    tag(row, parts[12], parts[13], parts[14])
            elif len(parts) in (3, 4) and rid in rows and re.fullmatch(r"\d+(\.\d+)?", parts[1]):
                row = rows[rid]
                row["pop"] = as_int(parts[1], 0, 100, as_int(row.get("pop"), 0, 100, 0))
                if parts[2].lower() in MOM:
                    row["mom"] = parts[2].lower()
                if len(parts) == 4 and parts[3]:
                    row["evidence"] = clean(parts[3], 220)
                updated.append(rid)
            else:
                skipped.append(line[:50])
                continue
            row["last_seen"] = now_text
            row["checks"] = as_int(row.get("checks"), 0, 10 ** 6, 0) + 1
            trail = trail_of(row)
            if not trail or trail[-1][1] != row["pop"] or now_ts - trail[-1][0] >= 3 * 3600:
                trail.append((now_ts, row["pop"]))
            row["trail"] = ",".join(f"{ts}:{value}" for ts, value in trail[-TRAIL_POINTS:])
    games, games_read = roblox_games(roblox_path)
    if games:   # without the list, the counts stay as the last run left them
        month_ago = now_ts - 30 * 86400
        for row in rows.values():
            keys = keys_of(row.get("keys"))
            hits = sorted((g for g in games if any(named_after(key, g[0], g[1]) for key in keys)), key=lambda g: -g[3])
            row["rb_games"] = len(hits) if keys else ""
            row["rb_ccu"] = sum(g[3] for g in hits) if keys else ""
            row["rb_new"] = sum(1 for g in hits if g[4] >= month_ago) if keys else ""
            row["rb_top"] = clean(hits[0][2], 60) if hits else ""
    kept, dropped = [], []
    for rid, row in rows.items():
        row["pop"] = as_int(row.get("pop"), 0, 100, 0)
        trail = trail_of(row)
        base = [value for ts, value in trail if now_ts - ts >= 20 * 3600]
        row["change"] = row["pop"] - (base[-1] if base else (trail[0][1] if trail else row["pop"]))
        week = [value for ts, value in trail if now_ts - ts >= 6.5 * 86400]
        row["change7"] = row["pop"] - week[-1] if week else ""
        row["hot"] = score(row, now)
        started = parse_date(row.get("started") or "", today)
        event = parse_date(row.get("event") or "", None)
        unseen_days = (now - parse_stamp(row.get("last_seen"), now)).total_seconds() / 86400
        upcoming = event is not None and event >= today
        too_old = (today - started).days > OLD_DAYS and row.get("mom") != "rising" and not upcoming
        if row["pop"] < 15 or unseen_days > UNSEEN_DAYS or (row.get("mom") == "fading" and row["pop"] < 30) or too_old:
            dropped.append(rid)
        else:
            kept.append({col: row.get(col, "") for col in COLS})
    kept.sort(key=lambda r: (-r["hot"], -r["pop"], r["id"]))
    dropped += [r["id"] for r in kept[KEEP:]]
    kept = kept[:KEEP]
    if had and not kept:
        sys.exit("PROBLEM: the merge would empty the list; nothing written")
    with open(__file__, encoding="utf-8") as handle:
        code = handle.read()
    meta = [
        {"label": "Last check", "value": now_text},
        {"label": "Popularity", "value": "0 to 100, judged by Claude from the evidence on each row, among the age bands the row is tagged with. An estimate, not a measured count."},
        {"label": "Hot score", "value": "popularity x (0.3 + 0.7 x 0.5^(days since it took off or to its release / 4)) x momentum (rising 1, steady 0.8, fading 0.5) x (0.9 + 24h change / 100, change limited to 10 either way)"},
        {"label": "Ages and short-form kind", "value": "Tags chosen by Claude: which of the bands 5-8, 9-12 and 13-18 know the trend, and whether it spreads through short videos as a sound, challenge, character, slang word or format."},
        {"label": "On Roblox", "value": "Games in the Roblox Trend Tracker's list whose name contains one of the row's search words (keys, chosen by Claude), the players in them, how many were created in the last 30 days, and the biggest one. " + (f"List read at {games_read}, {len(games)} games." if games else "The list could not be read this run, so the counts are from an earlier run.")},
        {"label": "hype.py", "value": code, "type": "code", "lang": "python"},
    ]
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"rows": kept, "meta": meta}, handle, ensure_ascii=False, separators=(",", ":"))
    print(f"added {len(added)}: " + (", ".join(added) or "none"))
    print(f"updated {len(set(updated))}; tagged {len(set(tagged))}; dropped {len(dropped)}: " + (", ".join(dropped[:12]) or "none"))
    print(f"total now {len(kept)}; skipped for low fit {low_fit}; Roblox list: " + (f"{len(games)} games read" if games else "NOT READ, counts left as they were"))
    print("hottest: " + "; ".join(f"{r['title']} ({r['hot']})" for r in kept[:3]))
    if skipped:
        print(f"PROBLEM LINES ({len(skipped)}), wrong number of fields or unknown id: " + "; ".join(skipped[:6]))


# ------------------------------------------------------------------ dashboard writes
def source_doc(ds_dir):
    try:
        with open(f"{ds_dir}/datasets/trends.json", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as error:
        sys.exit(f"PROBLEM: cannot read the trends source document ({type(error).__name__})")
    if isinstance(doc, dict) and "source" not in doc and isinstance(doc.get("data"), dict):
        doc = doc["data"]
    return doc


def asset_of(doc):
    url = str((doc.get("source") or {}).get("url") or "")
    tail = url.split("/_blob/")[-1] if "/_blob/" in url else ""
    return tail if ASSET.fullmatch(tail) else ""


def state(ds_dir):
    asset = asset_of(source_doc(ds_dir))
    print(f"asset: {asset}" if asset else "PROBLEM: the trends source points at no file")


def writes(ds_dir, new_url, version):
    if not re.fullmatch(r"/_blob/[0-9a-f]{32}", new_url):
        print("PROBLEM: the new file's url must look like /_blob/ followed by 32 hex characters")
        return
    if not version.isdigit():
        print("PROBLEM: give the version of the trends document as a number")
        return
    doc = source_doc(ds_dir)
    new_asset = new_url.split("/_blob/")[-1]
    stale = []
    for item in [asset_of(doc), doc.get("refreshFile") or ""] + list(doc.get("staleFiles") or []):
        if isinstance(item, str) and ASSET.fullmatch(item) and item != new_asset and item not in stale:
            stale.append(item)
    now = stamp(now_utc())
    data = {
        "source": {"url": new_url},
        "updated": {"at": now, "by": "Claude trend scout"},
        "edit": {"via": "claude", "at": now},
        "refreshFile": new_asset,
        "staleFiles": stale,
    }
    print(json.dumps([{"op": "update", "collection": "datasets", "doc_id": "trends", "if_version": int(version), "data": data}]))
    print("delete:", " ".join(stale))


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "state" and len(sys.argv) == 3:
        state(sys.argv[2])
    elif command == "brief" and len(sys.argv) == 5:
        brief(sys.argv[2], sys.argv[3], sys.argv[4])
    elif command == "merge" and len(sys.argv) in (5, 6):
        merge(*sys.argv[2:])
    elif command == "writes" and len(sys.argv) == 5:
        writes(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit("usage: hype.py state DS | brief OLD FEEDS OUT | merge OLD FOUND OUT [ROBLOX] | writes DS NEW_URL VERSION")
