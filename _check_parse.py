"""Diagnostic v2 — find a real tracklist URL via the DJ index first,
then fetch + dump the FULL post-JS DOM."""
import sys
sys.path.insert(0, r"D:\UltimateDJ - Copie")
from app.engine.tracklists import _playwright_get_html, _get_thread_browser
import re
import time
from collections import Counter

# Step 1: get a DJ's index page to discover a real tracklist URL
print("=" * 60)
print("Step 1: fetch /dj/charlottedewitte/ to find a real tracklist URL")
print("=" * 60)
dj_html = _playwright_get_html(
    "https://www.1001tracklists.com/dj/charlottedewitte/index.html",
    wait_for_selector="body", timeout_ms=30000)
if not dj_html:
    print("FAILED to fetch DJ page")
    sys.exit(1)
print(f"  Got {len(dj_html)} bytes")

# Find a tracklist URL
tl_urls = re.findall(r'href="(/tracklist/[^"]+\.html)"', dj_html)
tl_urls = list(dict.fromkeys(tl_urls))   # dedup, preserve order
print(f"  Found {len(tl_urls)} tracklist URLs")
for u in tl_urls[:3]:
    print(f"    {u}")
if not tl_urls:
    print("  No tracklist URLs found on DJ page — login might not be"
          " active or layout changed")
    print("  First 500 chars of DJ page body:")
    body = re.search(r'<body[^>]*>(.{0,500})', dj_html, re.DOTALL)
    if body:
        print("  ", re.sub(r'\s+', ' ', body.group(1))[:500])
    sys.exit(1)

target = "https://www.1001tracklists.com" + tl_urls[0]
print(f"\n  Will inspect: {target}")

# Step 2: fetch the tracklist + wait long for SPA hydration
print()
print("=" * 60)
print("Step 2: fetch tracklist page with extra wait for JS hydration")
print("=" * 60)

# Use a more specific wait: try waiting for either track containers OR
# a generic "loaded" signal
_, ctx = _get_thread_browser()
page = ctx.new_page()
try:
    page.goto(target, wait_until="domcontentloaded", timeout=30000)
    # Wait an extra 4 seconds for JS hydration
    time.sleep(4)
    html = page.content()
finally:
    try:
        page.close()
    except Exception:
        pass

print(f"Got {len(html)} bytes after 4s extra wait\n")
out = r"D:\UltimateDJ - Copie\_tracklist_dump.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"Saved to: {out}\n")

# Step 3: analyze
print("=" * 60)
print("Step 3: structure analysis")
print("=" * 60)

# All unique div classes
print("\n--- Top 30 div classes overall ---")
divs = re.findall(r'<div[^>]*class="([^"]+)"', html)
for cls, n in Counter(divs).most_common(30):
    print(f"  {n:4d}  {cls[:100]}")

# Any data-* attributes that might hold track IDs
print("\n--- data-* attributes (top 20 by occurrence) ---")
data_attrs = re.findall(r'(data-[a-z-]+)=', html)
for attr, n in Counter(data_attrs).most_common(20):
    print(f"  {n:4d}  {attr}")

# Script tags that look like JSON / NEXT_DATA
print("\n--- Inline scripts that may contain track data ---")
scripts = re.findall(r'<script[^>]*>(.{20,300})', html, re.DOTALL)
for s in scripts[:10]:
    snippet = re.sub(r'\s+', ' ', s).strip()[:150]
    if any(k in snippet.lower() for k in ('track', 'artist', 'title',
                                            '__next_data__',
                                            'window.__')):
        print(f"  > {snippet}")

# Any element containing the literal word "Charlotte de Witte" or
# expected artist name — tells us if the page is actually populated
print("\n--- Looking for known artist/track text ---")
for needle in ("Charlotte de Witte", "Doppler", "Tomorrowland",
                "Track 1", "ID -"):
    matches = html.count(needle)
    if matches:
        print(f"  '{needle}' appears {matches} times")
