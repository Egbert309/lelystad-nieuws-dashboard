#!/usr/bin/env python3
"""
Lelystad Nieuws Dashboard – Feed Collector
------------------------------------------
Bronnen:
  - Radio Lelystad  (web scraper – geen RSS beschikbaar)
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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Instellingen
# ---------------------------------------------------------------------------

RSS_FEEDS = {
    "Omroep Flevoland": "https://www.omroepflevoland.nl/RSS",
    "De Stentor":        "https://www.destentor.nl/lelystad/rss.xml",
    "Nu.nl":             "https://www.nu.nl/rss/Algemeen",
}

# NOS heeft meerdere feeds (elk ~20 items); samen geven ze meer dekking.
NOS_FEEDS = [
    "https://feeds.nos.nl/nosnieuwsalgemeen",
    "https://feeds.nos.nl/nosnieuwsbinnenland",
    "https://feeds.nos.nl/nosnieuwsvideo",
]

DATA_FILE   = "data/news.json"
MAX_ITEMS   = 50    # maximaal aantal bewaarde items per bron (rolling window)
FETCH_LIMIT = 100   # hoeveel feed-entries we scannen vóór filtering
DAYS_BACK   = 5     # rolling window voor NOS en Nu.nl (in dagen)

# ---------------------------------------------------------------------------
# Datum-hulpfuncties
# ---------------------------------------------------------------------------

_DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def parse_pub_date(s: str) -> datetime | None:
    """Parset een publicatiedatum (RFC 2822, ISO 8601, of NL-string) naar datetime."""
    if not s:
        return None
    # RFC 2822 (standaard RSS)
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    # ISO 8601
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    # Nederlandse datumstring: "donderdag 30 april 2026"
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", s.lower())
    if m:
        month = _DUTCH_MONTHS.get(m.group(2))
        if month:
            try:
                return datetime(int(m.group(3)), month, int(m.group(1)), 12, 0, 0,
                                tzinfo=timezone.utc)
            except ValueError:
                pass
    return None

# ---------------------------------------------------------------------------
# Filtering – Rechtbank-patroon
#
# Artikelen die Lelystad ALLEEN noemen als zittingsplaats van de rechtbank
# worden uitgefilterd. We strippen alle "Rechtbank Lelystad"-constructies
# en kijken of 'lelystad' dan nog overblijft.
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
# Radio Lelystad – scraper (geen RSS)
#
# Gebruikt regex in plaats van HTMLParser: de HTMLParser-aanpak telde depth
# verkeerd voor void-elementen zoals <img> en <source>, die wel een starttag
# hebben maar geen endtag. Daardoor bereikte _depth nooit 0 en werden items
# nooit opgeslagen.
# ---------------------------------------------------------------------------

def fetch_radiolelystad(max_items: int = MAX_ITEMS) -> list:
    try:
        req = urllib.request.Request(
            "https://www.radiolelystad.nl/nieuws/",
            headers={"User-Agent": "Mozilla/5.0 (compatible; LelyNieuws/2.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Splits de HTML op el-item blokken; elk blok bevat één nieuwsartikel.
        blocks = re.split(r'class="[^"]*\bel-item\b[^"]*"', html)[1:]

        results = []
        for block in blocks:
            # Link: eerste relatief pad in href
            href_m = re.search(r'href="(/[^"]+)"', block)
            if not href_m:
                continue
            href = "https://radiolelystad.nl" + href_m.group(1)
            if href.rstrip("/") in (
                "https://radiolelystad.nl/nieuws",
                "https://radiolelystad.nl",
            ):
                continue

            # Datum uit el-meta div
            date_m = re.search(
                r'class="[^"]*el-meta[^"]*"[^>]*>\s*([^<]{5,50}?)\s*<', block
            )
            date_str = date_m.group(1).strip() if date_m else ""

            # Titel uit el-title element
            title_m = re.search(
                r'class="[^"]*el-title[^"]*"[^>]*>\s*([^<]{5,}?)\s*<', block
            )
            if not title_m:
                continue
            title = title_m.group(1).strip()

            results.append({
                "title":     title,
                "link":      href,
                "published": date_str,
                "summary":   "",
            })
            if len(results) >= max_items:
                break

        # Sorteer nieuwste eerst op basis van de Nederlandse datumstring
        _min_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)
        results.sort(
            key=lambda x: parse_pub_date(x["published"]) or _min_dt,
            reverse=True,
        )

        print(f"[✓] Radio Lelystad: {len(results)} item(s) (scraper)")
        return results
    except Exception as exc:
        print(f"[!] Fout bij Radio Lelystad scraper: {exc}")
        return []


# ---------------------------------------------------------------------------
# RSS-feeds ophalen
# ---------------------------------------------------------------------------

def fetch_rss(source: str, url: str, days_back: int | None = None) -> list:
    """Haalt RSS-feed op, filtert op Lelystad, sorteert op datum (nieuwste eerst).

    Args:
        source:    Naam van de bron (voor logging).
        url:       RSS-feed URL.
        days_back: Alleen items van de laatste N dagen opnemen (None = geen limiet).
    """
    items = []
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days_back)
        if days_back is not None
        else None
    )
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "LelyNieuws/1.0"})
        for entry in feed.entries[:FETCH_LIMIT]:
            title   = entry.get("title", "").strip()
            summary = _HTML_TAGS_RE.sub("", entry.get("summary", entry.get("description", ""))).strip()
            link    = entry.get("link", "").strip()
            pub     = entry.get("published", "")

            if not is_lelystad_news(title, summary):
                continue

            # Datumfilter (alleen van toepassing als days_back is opgegeven)
            if cutoff is not None:
                pub_dt = parse_pub_date(pub)
                if pub_dt is not None and pub_dt < cutoff:
                    continue   # te oud, overslaan

            items.append({
                "title":     title,
                "link":      link,
                "published": pub,
                "summary":   "",
            })

        # Sorteer nieuwste eerst
        _min_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)
        items.sort(
            key=lambda x: parse_pub_date(x["published"]) or _min_dt,
            reverse=True,
        )

        print(f"[✓] {source}: {len(items)} Lelystad-item(s)"
              + (f" (laatste {days_back} dagen)" if days_back else ""))
    except Exception as exc:
        print(f"[!] Fout bij {source}: {exc}")
    return items


# ---------------------------------------------------------------------------
# Alles ophalen
# ---------------------------------------------------------------------------

def fetch_nos(days_back: int) -> list:
    """Haalt alle NOS-feeds op, combineert en dedupliceert op link."""
    all_items: list = []
    for url in NOS_FEEDS:
        all_items.extend(fetch_rss("NOS", url, days_back=days_back))

    # Dedupliceer op link (eerste keer gezien wint)
    seen: dict = {}
    for item in all_items:
        link = item.get("link", "")
        if link and link not in seen:
            seen[link] = item

    combined = list(seen.values())
    _min_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)
    combined.sort(
        key=lambda x: parse_pub_date(x.get("published", "")) or _min_dt,
        reverse=True,
    )
    print(f"[✓] NOS totaal (alle feeds gecombineerd): {len(combined)} Lelystad-item(s)")
    return combined


def fetch_all() -> dict:
    results = {}

    # Radio Lelystad via scraper (geen RSS)
    results["Radio Lelystad"] = fetch_radiolelystad()

    # Overige bronnen via RSS; Nu.nl: beperkt tot de laatste DAYS_BACK dagen
    for source, url in RSS_FEEDS.items():
        days = DAYS_BACK if source == "Nu.nl" else None
        results[source] = fetch_rss(source, url, days_back=days)

    # NOS: meerdere feeds samenvoegen, beperkt tot DAYS_BACK dagen
    results["NOS"] = fetch_nos(days_back=DAYS_BACK)

    return results


# ---------------------------------------------------------------------------
# Mergen en opslaan  (rolling window)
#
# Elke run voegt nieuwe items toe aan de bestaande data zodat een rolling
# window van DAYS_BACK dagen wordt opgebouwd.  Items ouder dan DAYS_BACK
# dagen worden verwijderd voor NOS en Nu.nl; andere bronnen bewaren hun
# items totdat ze worden overschreven door een nieuwe run.
# ---------------------------------------------------------------------------

_DATE_FILTERED_SOURCES = {"NOS", "Nu.nl"}


def load_existing() -> dict:
    """Leest bestaand news.json; geeft lege dict terug als het niet bestaat."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh).get("sources", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def merge_and_prune(existing: dict, fresh: dict) -> dict:
    """Voegt fresh samen met existing, dedupliceert op link en snoeit oud nieuws."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    _min_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)
    merged: dict = {}

    for source in set(existing) | set(fresh):
        # Nieuwe items hebben prioriteit; dan pas de oude
        combined_raw = fresh.get(source, []) + existing.get(source, [])

        # Dedupliceer op link
        seen: dict = {}
        for item in combined_raw:
            link = item.get("link", "")
            if link and link not in seen:
                seen[link] = item

        items = list(seen.values())

        # Tijdsfilter: voor NOS en Nu.nl items ouder dan DAYS_BACK weggooien
        if source in _DATE_FILTERED_SOURCES:
            def keep(it: dict) -> bool:
                pub_dt = parse_pub_date(it.get("published", ""))
                return pub_dt is None or pub_dt >= cutoff
            items = [it for it in items if keep(it)]

        # Sorteer nieuwste eerst, begrens op MAX_ITEMS
        items.sort(
            key=lambda x: parse_pub_date(x.get("published", "")) or _min_dt,
            reverse=True,
        )
        merged[source] = items[:MAX_ITEMS]

    return merged


def save(data: dict) -> None:
    os.makedirs("data", exist_ok=True)
    existing = load_existing()
    merged   = merge_and_prune(existing, data)

    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "sources": merged,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    for src, items in merged.items():
        print(f"  → {src}: {len(items)} item(s) na merge")
    print(f"[✓] Opgeslagen: {DATA_FILE}")


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
