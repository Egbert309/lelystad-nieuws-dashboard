#!/usr/bin/env python3
"""
Lelystad Nieuws Dashboard – Feed Collector
------------------------------------------
Haalt elk uur nieuws over Lelystad op uit vijf bronnen,
filtert op echte Lelystad-content (excl. louter rechtbank-vermeldingen),
en slaat het op als data/news.json voor het GitHub Pages dashboard.

Bronnen:
  - Radio Lelystad
  - Omroep Flevoland
  - De Stentor (Lelystad)
  - Nu.nl (Algemeen)
  - NOS (Algemeen)
"""

import feedparser
import json
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Instellingen
# ---------------------------------------------------------------------------

FEEDS = {
    "Radio Lelystad":  "https://www.radiolelystad.nl/feed/",
    "Omroep Flevoland": "https://www.omroepflevoland.nl/RSS",
    "De Stentor":       "https://www.destentor.nl/lelystad/rss.xml",
    "Nu.nl":            "https://www.nu.nl/rss/Algemeen",
    "NOS":              "https://feeds.nos.nl/nosnieuwsalgemeen",
}

DATA_FILE = "data/news.json"
MAX_ITEMS_PER_SOURCE = 25

# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

_RECHTBANK_RE = re.compile(
    r"rechtbank(?:\s+te|\s+in|\s+midden-nederland)?\s+lelystad"
    r"|rb\.?\s+lelystad"
    r"|rechtbank\b[^.!?\n]{0,40}lelystad",
    re.IGNORECASE,
)

_HTML_TAGS_RE = re.compile(r"<[^>]+>")


def is_lelystad_news(title: str, summary: str) -> bool:
    combined = (title + " " + summary).lower()
    if "lelystad" not in combined:
        return False
    stripped = _RECHTBANK_RE.sub("", combined)
    return "lelystad" in stripped


# ---------------------------------------------------------------------------
# Ophalen
# ---------------------------------------------------------------------------

def fetch_all() -> dict:
    results = {}
    for source, url in FEEDS.items():
        items = []
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "LelyNieuws/1.0"})
            for entry in feed.entries[:MAX_ITEMS_PER_SOURCE]:
                title   = entry.get("title", "").strip()
                summary = _HTML_TAGS_RE.sub("", entry.get("summary", entry.get("description", ""))).strip()
                link    = entry.get("link", "").strip()
                pub     = entry.get("published", "")
                if not is_lelystad_news(title, summary):
                    continue
                items.append({"title": title, "link": link, "published": pub, "summary": summary[:280]})
            print(f"[OK] {source}: {len(items)} Lelystad-item(s)")
        except Exception as exc:
            print(f"[!] Fout bij {source}: {exc}")
        results[source] = items
    return results


# ---------------------------------------------------------------------------
# Opslaan
# ---------------------------------------------------------------------------

def save(data: dict) -> None:
    os.makedirs("data", exist_ok=True)
    output = {"updated": datetime.now(timezone.utc).isoformat(), "sources": data}
    with open(DATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    print(f"[OK] Opgeslagen: {DATA_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ams = ZoneInfo("Europe/Amsterdam")
    print(f"\n=== Lelystad Nieuws Collector | {datetime.now(ams).strftime('%Y-%m-%d %H:%M')} ===\n")
    data = fetch_all()
    save(data)
    print("\n=== Klaar ===\n")


if __name__ == "__main__":
    main()
