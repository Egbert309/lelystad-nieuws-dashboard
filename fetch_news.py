#!/usr/bin/env python3
"""
Lelystad Nieuws Dashboard - Feed Collector
------------------------------------------
Bronnen:
  - Radio Lelystad  (web scraper - geen RSS beschikbaar)
  - Omroep Flevoland (RSS)
  - De Stentor      (RSS)
  - Nu.nl           (RSS)
  - NOS             (RSS)
"""

import feedparser
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Instellingen
# ---------------------------------------------------------------------------

RSS_FEEDS = {
    "Omroep Flevoland": "https://www.omroepflevoland.nl/RSS",
    "De Stentor":        "https://www.destentor.nl/lelystad/rss.xml",
    "Nu.nl":             "https://www.nu.nl/rss/Algemeen",
    "NOS":               "https://feeds.nos.nl/nosnieuwsalgemeen",
}

DATA_FILE = "data/news.json"
MAX_ITEMS = 25

# ---------------------------------------------------------------------------
# Filtering - Rechtbank-patroon
# ---------------------------------------------------------------------------

_RECHTBANK_RE = re.compile(
    r"rechtbank(?:\s+te|\s+in|\s+midden-nederland)?\s+lelystad"
    r"|rb\.?\s+lelystad"
    r"|rechtbank\b[^.!?\n]{0,40}lelystad",
    re.IGNORECASE,
)
_HTML_TAGS_RE = re.compile(r"<[^>]+>")


def is_lelystad_news(title, summary):
    combined = (title + " " + summary).lower()
    if "lelystad" not in combined:
        return False
    stripped = _RECHTBANK_RE.sub("", combined)
    return "lelystad" in stripped


# ---------------------------------------------------------------------------
# Radio Lelystad - scraper (geen RSS)
# ---------------------------------------------------------------------------

class _RadioLelystadParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self._in = False
        self._depth = 0
        self._cur = {}

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        classes = ad.get("class", "")
        if "el-item" in classes:
            self._in = True
            self._depth = 1
            self._cur = {}
            return
        if not self._in:
            return
        self._depth += 1
        if tag == "a":
            href = ad.get("href", "")
            # Maak relatieve URLs absoluut
            if href.startswith("/"):
                href = "https://radiolelystad.nl" + href
            # Alleen artikellinks, niet de /nieuws/ pagina zelf
            if "radiolelystad.nl" in href and href.rstrip("/") != "https://radiolelystad.nl/nieuws":
                self._cur.setdefault("link", href)

    def handle_endtag(self, tag):
        if not self._in:
            return
        self._depth -= 1
        if self._depth <= 0:
            self._in = False
            if self._cur.get("link") and self._cur.get("title"):
                self.items.append(dict(self._cur))
            self._cur = {}

    def handle_data(self, data):
        if not self._in:
            return
        t = data.strip()
        if not t or t.lower() in ("lees verder", ""):
            return
        if not self._cur.get("date") and re.search(r"\b20\d{2}\b", t) and len(t) < 50:
            self._cur["date"] = t
        elif not self._cur.get("title") and len(t) > 8:
            self._cur["title"] = t


def fetch_radiolelystad(max_items=MAX_ITEMS):
    try:
        req = urllib.request.Request(
            "https://radiolelystad.nl/nieuws/",
            headers={"User-Agent": "Mozilla/5.0 (compatible; LelyNieuws/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        parser = _RadioLelystadParser()
        parser.feed(html)
        results = []
        for item in parser.items[:max_items]:
            results.append({
                "title":     item["title"],
                "link":      item["link"],
                "published": item.get("date", ""),
                "summary":   "",
            })
        print(f"[OK] Radio Lelystad: {len(results)} item(s) (scraper)")
        return results
    except Exception as exc:
        print(f"[!] Fout bij Radio Lelystad scraper: {exc}")
        return []


# ---------------------------------------------------------------------------
# RSS-feeds ophalen
# ---------------------------------------------------------------------------

def fetch_rss(source, url):
    items = []
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "LelyNieuws/1.0"})
        for entry in feed.entries[:MAX_ITEMS]:
            title   = entry.get("title", "").strip()
            summary = _HTML_TAGS_RE.sub("", entry.get("summary", entry.get("description", ""))).strip()
            link    = entry.get("link", "").strip()
            pub     = entry.get("published", "")
            if not is_lelystad_news(title, summary):
                continue
            items.append({
                "title":     title,
                "link":      link,
                "published": pub,
                "summary":   "",
            })
        print(f"[OK] {source}: {len(items)} Lelystad-item(s)")
    except Exception as exc:
        print(f"[!] Fout bij {source}: {exc}")
    return items


# ---------------------------------------------------------------------------
# Alles ophalen
# ---------------------------------------------------------------------------

def fetch_all():
    results = {}
    results["Radio Lelystad"] = fetch_radiolelystad()
    for source, url in RSS_FEEDS.items():
        results[source] = fetch_rss(source, url)
    return results


# ---------------------------------------------------------------------------
# Opslaan
# ---------------------------------------------------------------------------

def save(data):
    os.makedirs("data", exist_ok=True)
    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "sources": data,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    print(f"[OK] Opgeslagen: {DATA_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ams = ZoneInfo("Europe/Amsterdam")
    print(f"\n=== Lelystad Nieuws Collector | {datetime.now(ams).strftime('%Y-%m-%d %H:%M')} ===\n")
    data = fetch_all()
    save(data)
    print("\n=== Klaar ===\n")


if __name__ == "__main__":
    main()
