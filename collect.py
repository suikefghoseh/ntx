#!/usr/bin/env python3
"""Collects public trend feeds into one JSON file for the Hype Radar scout. Standard library only."""
import csv
import datetime
import html
import http.client
import io
import json
import os
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
# Google News searches of the last 24 hours. The letters are the scout's focus areas (A short-form video, B screen and
# music, C games, creators and fads, D what 5 to 8 year olds like); "" is shown on every run.
NEWS = (
    ("", '"gen z" OR "gen alpha" trend'),
    ("A", '"tiktok trend"'),
    ("A", "viral meme"),
    ("A", '"going viral" tiktok'),
    ("A", "brainrot"),
    ("A", '"youtube shorts" trend OR viral'),
    ("A", '"tiktok sound" OR "tiktok song" OR "tiktok dance"'),
    ("A", 'slang "gen alpha" OR "gen z" meaning'),
    ("B", "new trailer"),
    ("B", '"box office"'),
    ("B", "anime new season"),
    ("B", "new album OR single"),
    ("C", "roblox"),
    ("C", "fortnite OR minecraft"),
    ("C", "youtuber OR streamer viral"),
    ("C", "viral toy OR collectible"),
    ("D", 'cartoon OR "kids show" new season OR episodes'),
    ("D", '"hottest toys" OR "toy craze" OR "must-have toy"'),
    ("D", '"animated movie" OR "family movie" trailer'),
    ("D", '"youtube kids" OR "kids youtuber" OR "kids channel"'),
)
# YouTube Data API key, from the repository secret of the same name. Without it the YouTube feeds are left out.
YT_KEY = os.environ.get("YT_API_KEY", "").strip()
# Shorts searches cost 100 of the 10,000 free quota units a day each, so every run does one half: even hours, odd hours.
YT_SEARCHES = (("brainrot", "tiktok trend", "roblox #shorts"), ("dance challenge", "toys #shorts", "meme #shorts"))


def clean(text, limit):
    return " ".join(str(text or "").replace("|", "/").split())[:limit]


def fetch(url, head=0, extra=None):
    """The body of a URL. With head, only about that many bytes from its start are wanted, so a cut-off download will do."""
    headers = {"User-Agent": UA, "Accept": "*/*"}
    headers.update(extra or {})
    if head:
        headers["Range"] = "bytes=0-" + str(head - 1)
    request = urllib.request.Request(url, headers=headers)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read()
        except http.client.IncompleteRead as error:
            if head and error.partial:
                return error.partial
            if attempt == 2:
                raise
            time.sleep(3)
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


def number(text):
    try:
        return int(re.sub(r"[^\d-]", "", str(text or "")))
    except ValueError:
        return 0


def words_of(markup):
    """The visible pieces of text in a piece of HTML, in order."""
    markup = re.sub(r"(?s)<(svg|script|style)\b.*?</\1>", " ", markup)
    pieces = (" ".join(html.unescape(piece).split()) for piece in re.split(r"<[^>]+>", markup))
    return [piece for piece in pieces if piece]


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


def rss(url, limit, now, max_hours, skip=()):
    root = ET.fromstring(fetch(url))
    out = []
    for item in root.iter("item"):
        title = clean(item.findtext("title"), 110)
        age = hours_ago(item.findtext("pubDate"), now)
        if not title or title.startswith(skip) or (age is not None and age > max_hours):
            continue
        when = "" if age is None else " (" + (str(age) + "h ago)" if age < 48 else str(age // 24) + "d ago)")
        out.append(title + when)
        if len(out) == limit:
            break
    return out


def news(query, now):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:1d") + "&hl=en-US&gl=US&ceid=US:en"
    time.sleep(1)
    return rss(url, 6, now, 30)


def tokchart(path, limit):
    """Rows of a tokchart.com chart: the name, then the page's other columns in brackets."""
    page = fetch("https://tokchart.com/" + path).decode("utf-8", "replace")
    body = re.search(r"(?s)<tbody[^>]*>(.*?)</tbody>", page)
    out = []
    for row in re.findall(r"(?s)<tr\b[^>]*>(.*?)</tr>", body.group(1) if body else ""):
        cells = [cell for cell in words_of(row) if not cell.startswith(("↑", "↓"))]
        for _ in range(2):   # the rank, then the places moved since yesterday
            if cells and re.fullmatch(r"[\d,]+|[–—-]", cells[0]):
                cells = cells[1:]
        if cells:
            out.append(clean(cells[0], 70) + (" (" + clean(", ".join(cells[1:]), 90) + ")" if len(cells) > 1 else ""))
        if len(out) == limit:
            break
    if not out:
        raise ValueError("no chart rows on the page")
    return out


def shazam(limit):
    text = fetch("https://www.shazam.com/services/charts/csv/top-200/united-states").decode("utf-8-sig", "replace")
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 3 and row[0].strip().isdigit():
            out.append(row[0].strip() + ". " + clean(row[2], 60) + " - " + clean(row[1], 40))
            if len(out) == limit:
                break
    if not out:
        raise ValueError("no chart rows in the file")
    return out


def spotify_gainers(limit):
    """The songs of the Spotify US daily chart that gained the most plays since the day before."""
    page = fetch("https://kworb.net/spotify/country/us_daily.html").decode("utf-8", "replace")
    rows = []
    for row in re.findall(r"(?s)<tr\b[^>]*>(.*?)</tr>", page):
        cells = [" ".join(words_of(cell)) for cell in re.findall(r"(?s)<td\b[^>]*>(.*?)</td>", row)]
        if len(cells) >= 8 and cells[0].isdigit() and number(cells[7]) > 0:
            rows.append(cells)
    if not rows:
        raise ValueError("no chart rows on the page")
    rows.sort(key=lambda cells: -number(cells[7]))
    return [clean(cells[2], 80) + " (#" + cells[0] + ", " + cells[6] + " plays a day, " + cells[7] + ")" for cells in rows[:limit]]


def netflix():
    # The file holds every week since 2021, latest first: the first 80 KB cover the latest week many times over.
    text = fetch("https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv", 80000).decode("utf-8", "replace")
    week, out = None, []
    for row in csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        week = week or row.get("week")
        if row.get("week") != week:
            break   # the file starts with the latest week
        kind = str(row.get("category") or "")
        if kind not in ("Films (English)", "TV (English)"):
            continue
        season = str(row.get("season_title") or "")
        name = clean(season if season not in ("", "N/A") else row.get("show_title"), 70)
        out.append(kind.split(" ")[0] + " " + str(row.get("weekly_rank")) + ". " + name + " (" + format(number(row.get("weekly_views")), ",")
                   + " views, week " + str(row.get("cumulative_weeks_in_top_10")) + " in the top 10)")
    if not out:
        raise ValueError("no rows for the latest week")
    return out


def fortnite_shop(now):
    """The sets on sale in today's Fortnite item shop, biggest first; most are brand, music or anime collabs."""
    entries = json.loads(fetch("https://fortnite-api.com/v2/shop"))["data"]["entries"]
    sets = {}
    for entry in entries:
        name = clean((entry.get("layout") or {}).get("name"), 50)
        if not name or name == "Jam Tracks":
            continue
        try:
            hours = (now - datetime.datetime.fromisoformat(str(entry.get("inDate")).replace("Z", "+00:00"))).total_seconds() / 3600
        except (TypeError, ValueError):
            hours = None
        count, longest = sets.get(name, (0, None))
        sets[name] = (count + 1, hours if longest is None or (hours is not None and hours > longest) else longest)
    if not sets:
        raise ValueError("no sets in the shop")
    ranked = sorted(sets.items(), key=lambda pair: (-pair[1][0], pair[0]))[:25]
    return [name + " (" + str(count) + (" items" if count > 1 else " item") + (", new in the shop" if longest is not None and longest < 72 else "") + ")"
            for name, (count, longest) in ranked]


def youtube(path):
    # The key travels in a header, never in the address, so it cannot end up in an error message or a log.
    return json.loads(fetch("https://www.googleapis.com/youtube/v3/" + path, extra={"X-Goog-Api-Key": YT_KEY}))


def seconds_of(duration):
    """Seconds in an ISO 8601 duration such as PT1M5S."""
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(duration or ""))
    if not match:
        return 0
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def compact(count):
    return f"{count / 1e6:.1f}M" if count >= 1e6 else f"{count / 1e3:.0f}K" if count >= 1e3 else str(count)


def video_lines(items, now, limit, longest=0, english=False):
    """One line per video, most viewed first: title (channel, views, age)."""
    rows = []
    for item in items:
        info = item.get("snippet") or {}
        title = clean(info.get("title"), 90)
        languages = [str(info.get(name)).lower() for name in ("defaultAudioLanguage", "defaultLanguage") if info.get(name)]
        foreign = sum(1 for letter in title if letter.isalpha() and ord(letter) > 0x24F)   # letters outside the Latin alphabet
        if not title or (english and (foreign >= 3 or (languages and not any(code.startswith("en") for code in languages)))):
            continue
        if longest and seconds_of((item.get("contentDetails") or {}).get("duration")) > longest:
            continue
        try:
            age = int((now - datetime.datetime.fromisoformat(str(info.get("publishedAt")).replace("Z", "+00:00"))).total_seconds() // 3600)
        except (TypeError, ValueError):
            age = None
        views = number((item.get("statistics") or {}).get("viewCount"))
        rows.append((views, title + " (" + clean(info.get("channelTitle"), 30) + ", " + compact(views) + " views" + ("" if age is None else ", " + str(age) + "h old") + ")"))
    rows.sort(key=lambda pair: -pair[0])
    return [line for _, line in rows[:limit]]


def youtube_trending(now):
    videos = youtube("videos?part=snippet,contentDetails,statistics&chart=mostPopular&regionCode=US&maxResults=50")
    return video_lines(videos.get("items") or [], now, 20)


def youtube_shorts(query, now):
    """The most viewed videos of up to 3 minutes posted in the last 48 hours that match a search."""
    after = (now - datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = youtube("search?part=id&type=video&videoDuration=short&order=viewCount&regionCode=US&relevanceLanguage=en&maxResults=25"
                    + "&publishedAfter=" + urllib.parse.quote(after) + "&q=" + urllib.parse.quote(query))
    ids = [item["id"]["videoId"] for item in found.get("items") or [] if (item.get("id") or {}).get("videoId")]
    if not ids:
        return []
    videos = youtube("videos?part=snippet,contentDetails,statistics&maxResults=50&id=" + ",".join(ids))
    return video_lines(videos.get("items") or [], now, 10, longest=180, english=True)


def carried_youtube(now):
    """(feeds, time) of the YouTube feeds in the published file while they are under 50 minutes old, else None.
    The searches use most of the day's free quota at one round an hour, and this workflow can start more often than that."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        return None
    try:
        old = json.loads(fetch("https://raw.githubusercontent.com/" + repo + "/data/feeds.json"))
        then = datetime.datetime.strptime(str(old["youtube_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        feeds = [feed for feed in old["feeds"] if str(feed.get("label") or "").startswith("YouTube ") and feed.get("items")]
    except Exception:   # no published file yet, no YouTube feeds in it, or it cannot be read: collect afresh
        return None
    return (feeds, str(old["youtube_at"])) if feeds and 0 <= (now - then).total_seconds() < 50 * 60 else None


def main(out_path):
    now = datetime.datetime.now(UTC)
    jobs = [
        ("", "Google Trends US, most searched today", lambda: google_trends("US")),
        ("", "Google Trends UK, most searched today", lambda: google_trends("GB")),
        ("", "Wikipedia, most viewed articles yesterday", lambda: wikipedia(now)),
        ("", "Apple top songs US", lambda: itunes("topsongs/limit=15", True)),
        ("", "Top free iPhone games US", lambda: itunes("topfreeapplications/limit=15/genre=6014", False)),
        ("", "Know Your Meme newsfeed", lambda: rss("https://knowyourmeme.com/newsfeed.rss", 12, now, 96)),
        ("A", "TikTok hashtags growing fastest this week, worldwide (growth)", lambda: tokchart("tiktok-trending-hashtags", 10)),
        ("A", "TikTok songs trending in the US today (artist, genre, new videos a day, growth)", lambda: tokchart("tiktok-trending-songs/united-states", 10)),
        ("A", "Shazam US top 15 of the last 7 days (songs people hear in videos and look up)", lambda: shazam(15)),
        ("A", "Know Your Meme, memes confirmed in the last 30 days", lambda: rss("https://knowyourmeme.com/memes.rss", 12, now, 720)),
        ("B", "Spotify US, songs that gained the most plays since yesterday", lambda: spotify_gainers(12)),
        ("BD", "Netflix worldwide top 10 of last week, English films and series", netflix),
        ("C", "Fortnite item shop today, sets on sale (collabs show which brands, artists and anime teens buy into)", lambda: fortnite_shop(now)),
        ("D", "Top free iPad games US (younger children play on tablets)", lambda: itunes("topfreeipadapplications/limit=15/genre=6014", False)),
        ("D", "BBC Newsround, news written for 6 to 12 year olds", lambda: rss("https://feeds.bbci.co.uk/newsround/rss.xml", 10, now, 72, ("Watch Newsround",))),
    ]
    kept = carried_youtube(now) if YT_KEY else None
    if YT_KEY and not kept:
        jobs.append(("BC", "YouTube trending in the US now (channel, views, age)", lambda: youtube_trending(now)))
        for query in YT_SEARCHES[now.hour % 2]:
            jobs.append(("A", "YouTube Shorts, most viewed of the last 48 hours for the search: " + query, lambda query=query: youtube_shorts(query, now)))
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
    youtube_at = now.strftime("%Y-%m-%dT%H:%M:%SZ") if any(feed["label"].startswith("YouTube ") for feed in feeds) else ""
    if kept:
        feeds += kept[0]
        youtube_at = kept[1]
    print("feeds read:", len(feeds), "| failed:", "; ".join(failed) or "none",
          "| YouTube:", "no key" if not YT_KEY else "kept from " + youtube_at if kept else "collected")
    if not any(feed["items"] for feed in feeds):
        sys.exit("every feed failed or was empty: nothing written")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "youtube_at": youtube_at, "feeds": feeds, "failed": failed}, handle, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: collect.py OUT.json")
    main(sys.argv[1])
