#!/usr/bin/env python3
"""Collects public trend feeds into one JSON file for the Hype Radar scout. Standard library only."""
import datetime
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UTC = datetime.timezone.utc
UA = "Mozilla/5.0 (compatible; trend-feed-collector/1.0)"
WIKI_SKIP = ("Special:", "Wikipedia:", "Portal:", "File:", "Help:", "Category:", "Template:", "Talk:", "User:")
# Google News searches of the last 24 hours. The letter is the scout's focus area; "" is shown on every run.
NEWS = (
    ("", '"gen z" OR "gen alpha" trend'),
    ("A", '"tiktok trend"'),
    ("A", "viral meme"),
    ("A", '"going viral" tiktok'),
    ("B", "new trailer"),
    ("B", '"box office"'),
    ("B", "anime new season"),
    ("B", "new album OR single"),
    ("C", "roblox"),
    ("C", "fortnite OR minecraft"),
    ("C", "youtuber OR streamer viral"),
    ("C", "viral toy OR collectible"),
)


def clean(text, limit):
    return " ".join(str(text or "").replace("|", "/").split())[:limit]


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(3)


def hours_ago(text, now):
    try:
        then = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return max(0, int((now - then).total_seconds() // 3600))


def traffic_number(text):
    match = re.match(r"(\d+(?:\.\d+)?)\s*([KM]?)", text.replace(",", "").upper())
    return float(match.group(1)) * {"": 1, "K": 1e3, "M": 1e6}[match.group(2)] if match else 0


def google_trends(geo):
    ns = {"ht": "https://trends.google.com/trending/rss"}
    root = ET.fromstring(fetch("https://trends.google.com/trending/rss?geo=" + geo))
    found = []
    for item in root.iter("item"):
        traffic = clean(item.findtext("ht:approx_traffic", "", ns), 12)
        news = clean(item.findtext("ht:news_item/ht:news_item_title", "", ns), 90)
        line = clean(item.findtext("title"), 60) + " (" + traffic + " searches)" + (" - " + news if news else "")
        found.append((traffic_number(traffic), line))
    found.sort(key=lambda pair: -pair[0])
    return [line for _, line in found[:25]]


def wikipedia(now):
    for back in (1, 2):
        day = now - datetime.timedelta(days=back)
        url = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/" + day.strftime("%Y/%m/%d")
        try:
            articles = json.loads(fetch(url))["items"][0]["articles"]
        except Exception:
            continue
        out = []
        for art in articles:
            name = str(art.get("article") or "")
            if not name or name in ("Main_Page", "-") or name.startswith(WIKI_SKIP) or name.startswith(("Deaths_in", "List_of")):
                continue
            out.append(clean(name.replace("_", " "), 70) + " (" + format(int(art.get("views") or 0), ",") + " views)")
            if len(out) == 30:
                break
        return out
    raise OSError("no pageview data for the last two days")


def itunes(path, with_artist):
    entries = json.loads(fetch("https://itunes.apple.com/us/rss/" + path + "/json"))["feed"]["entry"]
    out = []
    for place, entry in enumerate(entries, 1):
        name = clean((entry.get("im:name") or {}).get("label"), 60)
        artist = clean((entry.get("im:artist") or {}).get("label"), 40) if with_artist else ""
        out.append(str(place) + ". " + name + (" - " + artist if artist else ""))
    return out


def rss(url, limit, now, max_hours):
    root = ET.fromstring(fetch(url))
    out = []
    for item in root.iter("item"):
        age = hours_ago(item.findtext("pubDate"), now)
        if age is not None and age > max_hours:
            continue
        out.append(clean(item.findtext("title"), 110) + (" (" + str(age) + "h ago)" if age is not None else ""))
        if len(out) == limit:
            break
    return out


def news(query, now):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:1d") + "&hl=en-US&gl=US&ceid=US:en"
    time.sleep(1)
    return rss(url, 6, now, 30)


def main(out_path):
    now = datetime.datetime.now(UTC)
    jobs = [
        ("", "Google Trends US, most searched today", lambda: google_trends("US")),
        ("", "Google Trends UK, most searched today", lambda: google_trends("GB")),
        ("", "Wikipedia, most viewed articles yesterday", lambda: wikipedia(now)),
        ("", "Apple top songs US", lambda: itunes("topsongs/limit=15", True)),
        ("", "Top free iPhone games US", lambda: itunes("topfreeapplications/limit=15/genre=6014", False)),
        ("", "Know Your Meme newsfeed", lambda: rss("https://knowyourmeme.com/newsfeed.rss", 12, now, 96)),
    ]
    for focus, query in NEWS:
        jobs.append((focus, "Google News, last 24h: " + query, lambda query=query: news(query, now)))
    feeds, failed = [], []
    for focus, label, getter in jobs:
        try:
            items = getter()
        except Exception as error:  # one dead feed must not stop the others
            failed.append(label + " (" + type(error).__name__ + ")")
            continue
        feeds.append({"label": label, "focus": focus, "items": items})
    print("feeds read:", len(feeds), "| failed:", "; ".join(failed) or "none")
    if not any(feed["items"] for feed in feeds):
        sys.exit("every feed failed or was empty: nothing written")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "feeds": feeds, "failed": failed}, handle, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: collect.py OUT.json")
    main(sys.argv[1])
