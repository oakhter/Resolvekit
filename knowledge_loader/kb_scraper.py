"""
kb_scraper.py — Knowledge Base Scraper

Scrapes articles from a Salesforce Community site.
Filters by product, saves to CSV, caches article list, supports resume.
Optionally scrapes release notes into a separate CSV.

Requirements:
    pip install requests beautifulsoup4

Usage:
    python kb_scraper.py              # interactive prompts
    python kb_scraper.py --test       # test one article
    python kb_scraper.py --list       # list matching articles
    python kb_scraper.py --limit 10   # scrape first 10 only
    python kb_scraper.py --refresh    # force re-fetch article list

How resume works:
    Article list is cached to kb_<product>_articles.json on first run.
    On resume, list loads from cache — no pagination needed.
    Already-scraped articles are skipped by reading the existing CSV.
    Only missing articles are fetched.
"""

import requests
import json
import os
import time
import re
import csv
import argparse
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from datetime import datetime

# ── Source topics ─────────────────────────────────────────────
BASE_URL = os.getenv("KB_SCRAPER_BASE_URL", "https://example.test").rstrip("/")
AURA_URL = f"{BASE_URL}/s/sfsites/aura"

TOPICS = {
    "kb":      os.getenv("KB_SCRAPER_KB_TOPIC_ID", "replace-with-kb-topic-id"),
    "release": os.getenv("KB_SCRAPER_RELEASE_TOPIC_ID", "replace-with-release-topic-id"),
}

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept":       "application/json",
    "Origin":       BASE_URL,
    "Referer":      f"{BASE_URL}/s/",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

PAGE_LIMIT     = 11
PAGE_STEP      = 10
REQUEST_DELAY  = 0.5
FALLBACK_FWUID = "TXFWNVprQUZzQnEtNXVXYTFLQ2ppdzJEa1N5enhOU3R5QWl2VzNveFZTbGcxMy4tMjE0NzQ4MzY0OC4xMzEwNzIwMA"


# ── Filename helpers ──────────────────────────────────────────

# ── Base folder — sits next to kb_scraper.py ─────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
CSV_DIR      = os.path.join(BASE_DIR, "processed")
CACHE_DIR    = os.path.join(BASE_DIR, "cache")

def ensure_dirs():
    """Creates folder structure if it does not exist."""
    for d in [CSV_DIR, CACHE_DIR]:
        os.makedirs(d, exist_ok=True)

def to_slug(text):
    if not text:
        return "all"
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", text)
    return clean.strip().lower().replace(" ", "_")

def csv_file(slug, suffix=""):
    tag = f"_{suffix}" if suffix else ""
    return os.path.join(CSV_DIR, f"kb_{slug}{tag}.csv")

def cache_file(slug, suffix=""):
    tag = f"_{suffix}" if suffix else ""
    return os.path.join(CACHE_DIR, f"kb_{slug}{tag}_articles.json")


# ── Cache helpers ─────────────────────────────────────────────

def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        return []

def sync_cache(path, fresh_articles):
    """
    Compares fresh article list from API against cached list.
    Appends only new articles to the cache file.
    Returns (all_articles, new_articles).
    """
    cached      = load_cache(path)
    cached_urls = {a["urlName"] for a in cached}
    new_ones    = [a for a in fresh_articles if a["urlName"] not in cached_urls]

    if new_ones:
        updated = cached + new_ones
        with open(path, "w", encoding="utf-8") as f:
            json.dump(updated, f, indent=2)
        print(f"  Cache updated: +{len(new_ones)} new  ({len(updated)} total) -> {path}", flush=True)
    else:
        updated = cached
        print(f"  Cache up to date: {len(cached)} articles — no new ones found", flush=True)

    return updated, new_ones

def load_already_scraped(path):
    already = set()
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                already.add(row["url_name"])
        if already:
            print(f"  Resume: {len(already)} already scraped", flush=True)
    except FileNotFoundError:
        pass
    return already


# ── Payload builder ───────────────────────────────────────────

def build_payload(action_id, descriptor, calling_descriptor, params, fwuid, storable=False):
    message = {
        "actions": [{
            "id":                action_id,
            "descriptor":        descriptor,
            "callingDescriptor": calling_descriptor,
            "params":            params,
            **({"storable": True} if storable else {})
        }]
    }
    context = {
        "mode":    "PROD",
        "fwuid":   fwuid,
        "app":     "siteforce:communityApp",
        "loaded":  {"APPLICATION@markup://siteforce:communityApp": ""},
        "dn":      [],
        "globals": {}
    }
    return urlencode({
        "message":      json.dumps(message),
        "aura.context": json.dumps(context),
        "aura.token":   "null"
    })


# ── fwuid ─────────────────────────────────────────────────────

def fetch_fwuid():
    try:
        payload = build_payload(
            "1;a",
            "serviceComponent://ui.force.components.controllers.hostConfig.HostConfigController/ACTION$getConfigData",
            "markup://force:hostConfig",
            {}, FALLBACK_FWUID
        )
        resp  = requests.post(AURA_URL, data=payload, headers=HEADERS, timeout=10)
        fwuid = resp.json().get("context", {}).get("fwuid", FALLBACK_FWUID)
        return fwuid
    except Exception:
        return FALLBACK_FWUID


# ── Article list ──────────────────────────────────────────────

def fetch_article_list(fwuid, topic_id, label=""):
    articles = []
    seen     = set()
    offset   = 0

    tag = f" [{label}]" if label else ""
    print(f"Fetching article list{tag}...", flush=True)

    while True:
        payload = build_payload(
            "159;a",
            "serviceComponent://ui.self.service.components.controller.TopicArticleListDataProviderController/ACTION$loadMoreArticles",
            "markup://selfService:topicArticleListDataProvider",
            {"limit": PAGE_LIMIT, "offset": offset, "topicIds": topic_id},
            fwuid, storable=True
        )
        try:
            resp  = requests.post(AURA_URL, data=payload, headers=HEADERS, timeout=15)
            batch = resp.json()["actions"][0]["returnValue"]
        except Exception as e:
            print(f"  Failed at offset {offset}: {e}", flush=True)
            break

        new_count = 0
        for item in batch:
            art = item["article"]
            if art["urlName"] not in seen:
                seen.add(art["urlName"])
                articles.append({
                    "id":      art["id"],
                    "title":   art["title"],
                    "urlName": art["urlName"],
                })
                new_count += 1

        print(f"  offset {offset:4d} -> {new_count} new  (total: {len(articles)})", flush=True)

        if new_count == 0 or len(batch) < PAGE_LIMIT:
            break

        offset += PAGE_STEP
        time.sleep(0.3)

    print(f"\n  Done — {len(articles)} total articles\n", flush=True)
    return articles


# ── Article content ───────────────────────────────────────────

def fetch_article_content(article_id, fwuid):
    FIELDS = "Summary,LastModifiedDate,Detail__c,CreatedDate,Title,UrlName,Article__c,Id,CurrencyIsoCode,LastModifiedById,SystemModstamp"
    descriptor = f"{article_id}.undefined.FULL.null.null.Summary.VIEW.true.null.{FIELDS}.null"

    payload = build_payload(
        "183;a",
        "serviceComponent://ui.force.components.controllers.recordGlobalValueProvider.RecordGvpController/ACTION$getRecord",
        "UNKNOWN",
        {"recordDescriptor": descriptor},
        fwuid
    )

    try:
        resp_data = requests.post(AURA_URL, data=payload, headers=HEADERS, timeout=15).json()
        gvps      = resp_data.get("context", {}).get("globalValueProviders", [])

        for gvp in gvps:
            if gvp.get("type") != "$Record":
                continue
            records = gvp.get("values", {}).get("records", {})
            rec     = records.get(article_id, {})

            for obj_type in ["Knowledge__kav"] + [k for k in rec if k != "Knowledge__kav"]:
                if obj_type not in rec:
                    continue
                fields = rec[obj_type].get("record", {}).get("fields", {})

                # Known content fields first
                for field in ["Article__c", "Detail__c"]:
                    val = fields.get(field, {}).get("value")
                    if val:
                        cleaned = html_to_text(val)
                        if len(cleaned.split()) >= 5:
                            return field, cleaned

                # Fallback: any field with HTML content
                for fname, fdata in fields.items():
                    if not isinstance(fdata, dict):
                        continue
                    val = fdata.get("value")
                    if val and isinstance(val, str) and len(val) > 100 and "<" in val:
                        cleaned = html_to_text(val)
                        if len(cleaned.split()) >= 5:
                            return fname, cleaned

        return None, None

    except Exception:
        return None, None


def html_to_text(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")

    # Remove script/style tags entirely
    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(separator=" ")

    # Normalize typographic characters to clean ASCII equivalents
    replacements = {
        "\u2019": "'",   # right single quote -> apostrophe
        "\u2018": "'",   # left single quote
        "\u201c": '"',   # left double quote
        "\u201d": '"',   # right double quote
        "\u2014": " - ", # em dash
        "\u2013": " - ", # en dash
        "\u2192": "->",  # right arrow
        "\u2190": "<-",  # left arrow
        "\u2022": "-",   # bullet
        "\u00a0": " ",   # non-breaking space
        "\u200b": "",    # zero-width space
        "\u200c": "",    # zero-width non-joiner
        "\u200d": "",    # zero-width joiner
        "\ufeff": "",    # BOM
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)

    # Strip emojis and other non-ASCII symbols (optional — comment out to keep)
    text = text.encode("ascii", "ignore").decode("ascii")

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Scrape one topic ──────────────────────────────────────────

def scrape_topic(fwuid, topic_id, product_filter, slug,
                 output_csv, cache_json, label,
                 limit=None, refresh=False, list_only=False, test=False, skip_check=False):

    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}\n")
    print(f"  Output:  {output_csv}")
    print(f"  Cache:   {cache_json}\n")



    # Article list: use cache (skip_check) or fetch fresh to detect new
    if skip_check and not refresh:
        cached = load_cache(cache_json)
        if cached:
            print(f"  Using cache ({len(cached)} articles) - skipping new article check", flush=True)
            print(flush=True)
            all_articles = cached
            new_articles = []  # rely on CSV resume to find what's missing
        else:
            print("  No cache found - fetching article list...", flush=True)
            fresh = fetch_article_list(fwuid, topic_id, label=label)
            all_articles, new_articles = sync_cache(cache_json, fresh)
            print(flush=True)
    else:
        # Default: fetch fresh to detect new articles
        fresh = fetch_article_list(fwuid, topic_id, label=label)
        if not limit and not refresh:
            all_articles, new_articles = sync_cache(cache_json, fresh)
            print(flush=True)
        else:
            all_articles = fresh
            new_articles = fresh

    # Filter by product
    if product_filter:
        pf           = product_filter.strip().lower()
        articles     = [a for a in all_articles if pf in a["title"].lower()]
        # If skip_check, check against CSV for what's missing
        if skip_check and not new_articles:
            new_filtered = articles  # let CSV resume handle dedup
        else:
            new_filtered = [a for a in new_articles if pf in a["title"].lower()]
        print(f"  Matching '{product_filter}': {len(articles)} total, {len(new_filtered)} new\n", flush=True)
    else:
        articles     = all_articles
        new_filtered = all_articles if (skip_check and not new_articles) else new_articles


    if limit:
        articles     = articles[:limit]
        new_filtered = new_filtered[:limit]

        articles = articles[:limit]

    if not articles:
        print(f"  No articles found matching '{product_filter}'.")
        return

    if list_only:
        print(f"{'#':>4}  Title")
        print("-" * 80)
        for i, a in enumerate(articles, 1):
            print(f"{i:4}.  {a['title']}")
        print(f"\nTotal: {len(articles)} articles")
        return

    if test:
        a = articles[0]
        print(f"Testing: {a['title']}\n", flush=True)
        field, content = fetch_article_content(a["id"], fwuid)
        if content:
            print(f"Field:      {field}")
            print(f"Characters: {len(content)}")
            print(f"Words:      {len(content.split())}")
            print(f"\nPreview:\n{'-' * 60}")
            print(content[:600])
        else:
            print("No content returned")
        return


    # Only scrape new articles (not yet in CSV)
    already   = load_already_scraped(output_csv)
    to_scrape = [a for a in new_filtered if a["urlName"] not in already]
    existing  = len(articles) - len(new_filtered)

    if existing:
        print(f"  {existing} existing articles untouched", flush=True)
    if len(new_filtered) - len(to_scrape):
        print(f"  {len(new_filtered) - len(to_scrape)} new articles already in CSV", flush=True)

    if not to_scrape:
        print("  Nothing new to scrape - CSV is up to date.")
        return

    file_mode  = "a" if already else "w"
    total      = len(to_scrape)
    skipped    = len(articles) - total
    est        = round(total * 1.5 / 60, 1)
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    success    = skipped
    failed     = []
    run_start  = time.time()

    print(f"\nScraping {total} new articles - est. {est} min\n", flush=True)

    print(f"Scraping {total} articles - est. {est} min\n", flush=True)

    with open(output_csv, file_mode, newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=[
            "id", "title", "url_name", "url",
            "content", "content_field",
            "word_count", "char_count", "scraped_at"
        ])
        if file_mode == "w":
            writer.writeheader()

        for i, article in enumerate(to_scrape, 1):
            filled  = int(30 * i / total)
            bar     = "\u2588" * filled + "\u2591" * (30 - filled)
            elapsed = time.time() - run_start
            eta_s   = int(elapsed / i * (total - i)) if i > 1 else 0
            eta_str = f"{eta_s // 60}m {eta_s % 60:02d}s" if i > 1 else "--:--"
            label_p = f"{i + skipped}/{len(articles)}" if skipped else f"{i}/{total}"
            print(f"\r  [{bar}] {label_p}  ETA {eta_str}  ", end="", flush=True)

            field, content = fetch_article_content(article["id"], fwuid)

            if not content or len(content.split()) < 5:
                failed.append(article["urlName"])
                continue

            writer.writerow({
                "id":            article["id"],
                "title":         article["title"],
                "url_name":      article["urlName"],
                "url":           f"{BASE_URL}/s/article/{article['urlName']}",
                "content":       content,
                "content_field": field,
                "word_count":    len(content.split()),
                "char_count":    len(content),
                "scraped_at":    scraped_at
            })
            success += 1
            time.sleep(REQUEST_DELAY)

    print()
    print(f"\n-- Complete -----------------------------------------")
    print(f"  Saved to:  {output_csv}")
    print(f"  Articles:  {success}/{len(articles)}")
    print(f"  Failed:    {len(failed)}")
    if failed:
        print("\n  No content found for:")
        for f in failed:
            print(f"    x {f}")


# ── Main ──────────────────────────────────────────────────────

def scrape(list_only=False, limit=None, test=False, refresh=False, skip_check=False):

    ensure_dirs()

    print("=" * 50)
    print("  Knowledge Base Scraper")
    print("=" * 50)
    print()

    # ── Q1: Check for new articles? ──────────────────────────
    print("  Check for new articles? (y/n)")
    print("  y = fetch latest article list from site (slower)")
    print("  n = use cached list, skip to scraping missing content")
    print()
    skip_check = input("  > ").strip().lower() != "y"
    print()

    # ── Q2: Product filter ───────────────────────────────────
    print("  Filter by product name (matches article titles).")
    print("  Examples:  Example Product")
    print("             Your Product Name")
    print("  Leave blank to scrape ALL articles.")
    print()
    product_filter = input("  Product name: ").strip()
    print()

    # ── Q3: Release notes? ───────────────────────────────────
    print("  Include release notes? (y/n)")
    include_release = input("  > ").strip().lower() == "y"
    print()

    # ── Q4: Check release notes for new articles? ────────────
    skip_check_release = skip_check  # default: same as KB answer
    if include_release and not list_only and not test:
        print("  Check release notes for new articles? (y/n)")
        print("  y = fetch latest release notes list from site")
        print("  n = use cached list, skip to scraping missing content")
        print()
        skip_check_release = input("  > ").strip().lower() != "y"
        print()

    slug  = to_slug(product_filter) if product_filter else "all"
    fwuid = fetch_fwuid()

    # ── Scrape main KB ────────────────────────────────────────
    scrape_topic(
        fwuid          = fwuid,
        topic_id       = TOPICS["kb"],
        product_filter = product_filter,
        slug           = slug,
        output_csv     = csv_file(slug),
        cache_json     = cache_file(slug),
        label          = f"Knowledge Base - {product_filter or 'All'}",
        limit          = limit,
        refresh        = refresh,
        list_only      = list_only,
        test           = test,
        skip_check     = skip_check
    )

    # ── Scrape release notes ──────────────────────────────────
    if include_release and not list_only and not test:
        scrape_topic(
            fwuid          = fwuid,
            topic_id       = TOPICS["release"],
            product_filter = product_filter,
            slug           = slug,
            output_csv     = csv_file(slug, "release_notes"),
            cache_json     = cache_file(slug, "release_notes"),
            label          = f"Release Notes - {product_filter or 'All'}",
            limit          = limit,
            refresh        = refresh,
            list_only      = False,
            test           = False,
            skip_check     = skip_check_release
        )


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Knowledge Base Scraper")
    parser.add_argument("--test",    action="store_true", help="Test one article")
    parser.add_argument("--list",    action="store_true", help="List articles only")
    parser.add_argument("--limit",   type=int, default=None, help="Max articles per topic")
    parser.add_argument("--refresh",    action="store_true", help="Force re-fetch article list")
    parser.add_argument("--skip-check", action="store_true", help="Skip checking for new articles, use cache directly")
    args = parser.parse_args()
    scrape(list_only=args.list, limit=args.limit, test=args.test, refresh=args.refresh, skip_check=args.skip_check)
