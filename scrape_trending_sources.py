from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin, urlparse, urlunparse
import argparse
import csv
import html
import json
import re
import ssl
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

UA_DESKTOP_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

SITE_JOBS = [
    {
        "site_key": "indianexpress",
        "source": "Indian Express",
        "url": "https://indianexpress.com/section/trending/",
        "base_url": "https://indianexpress.com/",
        "selectors": ["h2 a", "h3 a", "article a", "main a"],
        "allow_patterns": [],
        "exclude_patterns": [
            r"/section/trending/?$",
            r"/photos/",
            r"/videos/",
            r"/web-stories/",
            r"/explained/",
        ],
    },
    {
        "site_key": "hindustantimes",
        "source": "Hindustan Times",
        "mode": "rss",
        "url": "https://www.hindustantimes.com/feeds/rss/trending/rssfeed.xml",
        "base_url": "https://www.hindustantimes.com/",
        "selectors": [],
        "allow_patterns": [],
        "exclude_patterns": [
            r"/trending/?$",
            r"/photos/",
            r"/videos/",
            r"/web-stories/",
        ],
    },
    {
        "site_key": "livemint",
        "source": "LiveMint",
        "url": "https://www.livemint.com/us/trending",
        "base_url": "https://www.livemint.com/",
        "selectors": ["h2 a", "h3 a", "article a", "main a", "li a"],
        "allow_patterns": [],
        "exclude_patterns": [
            r"/us/trending/?$",
            r"/photos/",
            r"/videos/",
            r"/mint-lounge/",
        ],
    },
    {
        "site_key": "indiatoday",
        "source": "India Today",
        "url": "https://www.indiatoday.in/trending-news",
        "base_url": "https://www.indiatoday.in/",
        "selectors": ["h2 a", "h3 a", "article a", "main a"],
        "allow_patterns": [],
        "exclude_patterns": [
            r"/trending-news/?$",
            r"/video/",
            r"/livetv",
            r"/topic/",
            r"/newsletter",
            r"/photo/",
        ],
    },
    {
        "site_key": "toi_etimes",
        "source": "TOI ETimes",
        "url": "https://timesofindia.indiatimes.com/etimes/trending",
        "base_url": "https://timesofindia.indiatimes.com/",
        "selectors": [],
        "allow_patterns": [r"/articleshow/\d+\.cms"],
        "exclude_patterns": [r"/etimes/trending/?$", r"/photostory/", r"/videoshow/"],
    },
    {
        "site_key": "toi_viral",
        "source": "TOI Viral News",
        "url": "https://timesofindia.indiatimes.com/viral-news",
        "base_url": "https://timesofindia.indiatimes.com/",
        "selectors": [],
        "allow_patterns": [r"/articleshow/\d+\.cms"],
        "exclude_patterns": [r"/viral-news/?$", r"/photostory/", r"/videoshow/"],
    },
    {
        "site_key": "ndtv",
        "source": "NDTV",
        "url": "https://www.ndtv.com/trends",
        "base_url": "https://www.ndtv.com/",
        "selectors": [],
        "allow_patterns": [],
        "exclude_patterns": [
            r"/trends/?$",
            r"/video/",
            r"/photos/",
            r"/live-updates/",
            r"/topic/",
            r"/indian-railway/pnr-status",
            r"/TermsAndConditions\.aspx",
            r"/codeofethics\.aspx",
        ],
        "prefer_channel": "chrome",
    },
]

BLOCK_TITLE_PATTERNS = [
    r"^\s*$",
    r"^advertisement$",
    r"^sponsored$",
    r"^recommended stories$",
    r"^follow us on",
    r"^read epaper$",
    r"^subscribe",
    r"^newsletter$",
    r"^privacy policy$",
    r"^terms and conditions$",
    r"^live updates",
    r"^latest news$",
    r"^news$",
]


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def add_stealth(ctx_or_page):
    if hasattr(ctx_or_page, "add_init_script"):
        ctx_or_page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            const gp = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(p) {
              if (p === 37445) return 'Intel Inc.';
              if (p === 37446) return 'ANGLE (Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0)';
              return gp.call(this, p);
            };
            """
        )


def click_cookie_banners(page):
    selectors = [
        "button:has-text('Accept')",
        "button:has-text('I Agree')",
        "button:has-text('AGREE')",
        "button:has-text('Got it')",
        "button:has-text('Continue')",
        "button[aria-label='Accept']",
        "[data-testid='accept']",
        "#onetrust-accept-btn-handler",
        "#wzrk-confirm",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible():
                locator.click(timeout=1200)
                time.sleep(0.2)
                break
        except Exception:
            pass


def clean_headline(text):
    title = re.sub(r"\s+", " ", text or "").strip()
    title = re.sub(r"<[^>]+>", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def canonicalize_url(link, job):
    parsed = urlparse(link)
    clean_path = re.sub(r"/+", "/", parsed.path or "/")
    canonical = parsed._replace(query="", fragment="", path=clean_path)
    canonical_url = urlunparse(canonical)

    if job["site_key"] in {"toi_etimes", "toi_viral"}:
        match = re.search(r"/articleshow/(\d+)\.cms", clean_path, re.I)
        if match:
            article_id = match.group(1)
            return f"{job['base_url'].rstrip('/')}/articleshow/{article_id}.cms"

    return canonical_url


def dedupe_key(job, link):
    if job["site_key"] in {"toi_etimes", "toi_viral"}:
        match = re.search(r"/articleshow/(\d+)\.cms", link, re.I)
        if match:
            return f"{job['site_key']}:{match.group(1)}"
    return link


def fetch_rss_items(job):
    request = urllib.request.Request(
        job["url"],
        headers={
            "User-Agent": UA_DESKTOP_CHROME,
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(request, timeout=30, context=ssl_context) as response:
        payload = response.read()

    root = ET.fromstring(payload)
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()
        link = item.findtext("link", default="").strip()
        if title and link:
            items.append({"title": title, "href": link})
    return items


def extract_anchors(page, selectors=None):
    return page.evaluate(
        """
        ({ selectors }) => {
          const out = [];
          const seen = new Set();
          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const allAnchors = selectors && selectors.length
            ? selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)))
            : Array.from(document.querySelectorAll('a[href]'));

          allAnchors.forEach((a) => {
            const href = a.href || a.getAttribute('href') || '';
            let title = norm(a.getAttribute('title') || a.getAttribute('aria-label') || '');
            if (!title) {
              const img = a.querySelector('img[alt]');
              if (img) title = norm(img.getAttribute('alt') || '');
            }
            if (!title) {
              title = norm(a.textContent || '');
            }
            if (!href || !title) return;
            const key = href + '|' + title;
            if (seen.has(key)) return;
            seen.add(key);
            out.push({ href, title });
          });

          return out;
        }
        """,
        {"selectors": selectors or []},
    )


def is_valid_candidate(link, title, job):
    lower_title = title.lower()
    parsed = urlparse(link)
    path = parsed.path.rstrip("/")

    if parsed.scheme not in ("http", "https"):
        return False
    if any(re.search(pattern, link, re.I) for pattern in job["exclude_patterns"]):
        return False
    if job["allow_patterns"] and not any(re.search(pattern, link, re.I) for pattern in job["allow_patterns"]):
        return False
    if any(re.search(pattern, lower_title, re.I) for pattern in BLOCK_TITLE_PATTERNS):
        return False
    if len(title) < 12 or len(title.split()) < 3:
        return False
    if "taboola" in link.lower():
        return False
    if parsed.netloc and urlparse(job["base_url"]).netloc not in parsed.netloc:
        return False
    if path in ("", "/"):
        return False
    if len([part for part in path.split("/") if part]) < 1:
        return False
    return True


def normalize_rows(job, candidates):
    rows = []
    seen_links = set()
    for item in candidates:
        raw_link = urljoin(job["base_url"], item["href"])
        link = canonicalize_url(raw_link, job)
        title = clean_headline(item["title"])
        key = dedupe_key(job, link)
        if key in seen_links:
            continue
        if not is_valid_candidate(link, title, job):
            continue
        seen_links.add(key)
        rows.append(
            {
                "source": job["source"],
                "rank": len(rows) + 1,
                "headline": title,
                "link": link,
            }
        )
    return rows


def render_html(rows):
    data_json = json.dumps(rows, ensure_ascii=False)
    return f"""<!doctype html>
<meta charset="utf-8">
<title>News Track Trends</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {{
    --paper: #f3ead8;
    --paper-deep: #e7d8bb;
    --ink: #181411;
    --muted: #6c6257;
    --line: rgba(24, 20, 17, 0.12);
    --panel: rgba(255,255,255,0.76);
    --accent: #c64824;
    --accent-soft: rgba(198,72,36,0.13);
    --forest: #445739;
    --shadow: 0 18px 44px rgba(85, 61, 34, 0.12);
    --radius-xl: 30px;
    --radius-lg: 22px;
    --radius-md: 16px;
    --display: Georgia, "Times New Roman", serif;
    --body: "Avenir Next", "Segoe UI", sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    color: var(--ink);
    font-family: var(--body);
    background:
      radial-gradient(circle at top left, rgba(198,72,36,0.16), transparent 25%),
      radial-gradient(circle at 90% 12%, rgba(68,87,57,0.12), transparent 22%),
      linear-gradient(180deg, #f7f0e2 0%, var(--paper) 55%, var(--paper-deep) 100%);
  }}
  body::before {{
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    opacity: 0.25;
    background-image:
      linear-gradient(rgba(255,255,255,0.18) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.18) 1px, transparent 1px);
    background-size: 32px 32px;
  }}
  a {{ color: inherit; text-decoration: none; }}
  .page {{
    width: min(1400px, calc(100% - 32px));
    margin: 0 auto;
    padding: 24px 0 40px;
  }}
  .hero {{
    position: relative;
    overflow: hidden;
    padding: 28px;
    border-radius: var(--radius-xl);
    background: linear-gradient(135deg, rgba(29,23,18,0.98), rgba(84,44,25,0.95) 45%, rgba(126,54,29,0.92) 100%);
    color: #fff8f0;
    box-shadow: var(--shadow);
  }}
  .hero::after {{
    content: "";
    position: absolute;
    right: -60px;
    top: -60px;
    width: 240px;
    height: 240px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(255,223,185,0.28), transparent 68%);
  }}
  .eyebrow {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-radius: 999px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-size: 12px;
    background: rgba(255,255,255,0.1);
    border: 1px solid rgba(255,255,255,0.12);
  }}
  .hero-grid {{
    display: grid;
    grid-template-columns: minmax(0, 1.3fr) minmax(280px, 0.8fr);
    gap: 22px;
    margin-top: 18px;
    position: relative;
    z-index: 1;
  }}
  .hero h1 {{
    margin: 14px 0 10px;
    max-width: 12ch;
    font: 700 clamp(42px, 6vw, 72px)/0.94 var(--display);
    letter-spacing: -0.05em;
  }}
  .hero p {{
    margin: 0;
    max-width: 58ch;
    font-size: 16px;
    line-height: 1.65;
    color: rgba(255,248,240,0.82);
  }}
  .hero-stack {{
    display: grid;
    gap: 14px;
  }}
  .hero-note {{
    padding: 18px;
    border-radius: 22px;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.12);
    backdrop-filter: blur(8px);
  }}
  .hero-note strong {{
    display: block;
    margin-bottom: 8px;
    font-size: 15px;
  }}
  .hero-note p {{
    font-size: 14px;
    line-height: 1.55;
  }}
  .metrics {{
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    margin-top: 22px;
    position: relative;
    z-index: 1;
  }}
  .metric {{
    padding: 16px 18px;
    border-radius: 18px;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.12);
  }}
  .metric-label {{
    display: block;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: rgba(255,248,240,0.72);
  }}
  .metric-value {{
    display: block;
    margin-top: 6px;
    font-size: clamp(24px, 3.2vw, 34px);
    font-weight: 700;
  }}
  .controls {{
    margin-top: 18px;
    padding: 18px;
    border-radius: var(--radius-xl);
    background: rgba(255,255,255,0.56);
    border: 1px solid rgba(255,255,255,0.6);
    backdrop-filter: blur(10px);
    box-shadow: 0 10px 30px rgba(86, 63, 42, 0.08);
  }}
  .toolbar {{
    display: grid;
    grid-template-columns: 1.45fr repeat(3, minmax(160px, 1fr)) auto;
    gap: 12px;
    align-items: end;
  }}
  .field {{
    display: grid;
    gap: 6px;
  }}
  .field span {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }}
  input, select, button {{
    width: 100%;
    padding: 13px 14px;
    border-radius: 14px;
    border: 1px solid var(--line);
    background: rgba(255,255,255,0.78);
    color: var(--ink);
    font: inherit;
  }}
  button {{
    width: auto;
    border-color: transparent;
    background: var(--accent);
    color: white;
    cursor: pointer;
    font-weight: 700;
  }}
  .source-pills {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 14px;
  }}
  .pill {{
    padding: 10px 14px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: rgba(255,255,255,0.7);
    color: var(--ink);
    cursor: pointer;
  }}
  .pill.active {{
    background: var(--accent);
    border-color: var(--accent);
    color: white;
  }}
  .summary {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin-top: 16px;
  }}
  .summary-card {{
    padding: 16px 18px;
    border-radius: 18px;
    background: var(--panel);
    border: 1px solid rgba(255,255,255,0.64);
    box-shadow: 0 10px 24px rgba(82, 62, 42, 0.08);
  }}
  .summary-card strong {{
    display: block;
    margin-bottom: 6px;
    font-size: 13px;
    color: var(--muted);
  }}
  .summary-card span {{
    display: block;
    font-size: 26px;
    font-weight: 700;
  }}
  .content {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) 300px;
    gap: 18px;
    margin-top: 20px;
    align-items: start;
  }}
  .stories {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
  }}
  .story-card {{
    display: grid;
    gap: 12px;
    min-height: 180px;
    padding: 18px;
    border-radius: 22px;
    background: var(--panel);
    border: 1px solid rgba(255,255,255,0.62);
    box-shadow: 0 14px 34px rgba(82, 62, 42, 0.1);
    transition: transform 160ms ease, box-shadow 160ms ease;
  }}
  .story-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 18px 40px rgba(82, 62, 42, 0.14);
  }}
  .story-top {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }}
  .badge {{
    display: inline-flex;
    align-items: center;
    padding: 6px 10px;
    border-radius: 999px;
    background: var(--accent-soft);
    color: var(--accent);
    font-size: 12px;
    font-weight: 700;
  }}
  .rank {{
    font-size: 12px;
    color: var(--muted);
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }}
  .story-card h3 {{
    margin: 0;
    font: 700 clamp(22px, 2.3vw, 29px)/1.08 var(--display);
    letter-spacing: -0.03em;
  }}
  .story-meta {{
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    font-size: 12px;
    color: var(--muted);
  }}
  .story-link {{
    margin-top: auto;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    width: fit-content;
    padding: 11px 14px;
    border-radius: 12px;
    background: rgba(24,20,17,0.05);
    font-weight: 700;
  }}
  .group-card {{
    grid-column: 1 / -1;
    display: grid;
    gap: 16px;
    padding: 20px;
    border-radius: 22px;
    background: rgba(255,255,255,0.66);
    border: 1px solid rgba(255,255,255,0.62);
    box-shadow: 0 14px 30px rgba(82, 62, 42, 0.1);
  }}
  .sidebar {{
    display: grid;
    gap: 16px;
    position: sticky;
    top: 18px;
  }}
  .sidebar-card {{
    padding: 18px;
    border-radius: 22px;
    background: rgba(255,255,255,0.7);
    border: 1px solid rgba(255,255,255,0.62);
    box-shadow: 0 12px 28px rgba(82, 62, 42, 0.08);
  }}
  .sidebar-card h2 {{
    margin: 0 0 10px;
    font: 700 24px/1.08 var(--display);
  }}
  .sidebar-card p, .sidebar-card li {{
    margin: 0;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.55;
  }}
  .sidebar-card ol {{
    margin: 0;
    padding-left: 18px;
  }}
  .sidebar-card li + li {{ margin-top: 10px; }}
  .source-list {{
    display: grid;
    gap: 10px;
  }}
  .source-row {{
    display: flex;
    justify-content: space-between;
    gap: 10px;
    padding-bottom: 10px;
    border-bottom: 1px dashed rgba(24,20,17,0.14);
  }}
  .source-row:last-child {{
    border-bottom: 0;
    padding-bottom: 0;
  }}
  .source-row span {{
    font-size: 12px;
    color: var(--muted);
  }}
  .empty {{
    grid-column: 1 / -1;
    padding: 34px 22px;
    border-radius: 22px;
    background: rgba(255,255,255,0.72);
    border: 1px dashed rgba(24,20,17,0.18);
    text-align: center;
    color: var(--muted);
  }}
  .footer {{
    margin-top: 18px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
  }}
  @media (max-width: 1180px) {{
    .toolbar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .content {{ grid-template-columns: 1fr; }}
    .sidebar {{ position: static; }}
  }}
  @media (max-width: 920px) {{
    .hero-grid,
    .metrics,
    .summary,
    .stories {{ grid-template-columns: 1fr; }}
  }}
  @media (max-width: 640px) {{
    .page {{ width: min(100% - 20px, 1400px); padding-top: 14px; }}
    .hero, .controls {{ padding: 18px; }}
    .toolbar {{ grid-template-columns: 1fr; }}
    button {{ width: 100%; }}
  }}
</style>
<div class="page">
  <section class="hero">
    <div class="eyebrow">News Track Trends</div>
    <div class="hero-grid">
      <div>
        <h1>All tracked trending pages in one newsroom-style dashboard.</h1>
        <p>We pull live trend links from Indian Express, Hindustan Times, LiveMint, India Today, Times of India ETimes, Times of India Viral News, and NDTV, then lay them out on one page that is easy to scan, search, and host.</p>
      </div>
      <div class="hero-stack">
        <div class="hero-note">
          <strong>Why this page feels different</strong>
          <p>Instead of a plain scraper dump, this layout treats each story like a front-page item while still preserving the original source and rank from each publisher.</p>
        </div>
        <div class="hero-note">
          <strong>What updates automatically</strong>
          <p>The HTML stays self-contained, so GitHub Pages can host it directly while your workflow refreshes the data snapshot every two hours.</p>
        </div>
      </div>
    </div>
    <div class="metrics">
      <div class="metric">
        <span class="metric-label">Tracked Sources</span>
        <span class="metric-value" id="metricSources">0</span>
      </div>
      <div class="metric">
        <span class="metric-label">Stories In Snapshot</span>
        <span class="metric-value" id="metricStories">0</span>
      </div>
      <div class="metric">
        <span class="metric-label">Last Refresh</span>
        <span class="metric-value" id="metricRefresh">-</span>
      </div>
    </div>
  </section>

  <section class="controls">
    <div class="toolbar">
      <label class="field">
        <span>Search</span>
        <input id="search" placeholder="Search topics, people, headlines...">
      </label>
      <label class="field">
        <span>Source</span>
        <select id="sourceSelect"></select>
      </label>
      <label class="field">
        <span>Sort</span>
        <select id="sort">
          <option value="rankAsc">Rank: low to high</option>
          <option value="rankDesc">Rank: high to low</option>
          <option value="alpha">Headline: A to Z</option>
        </select>
      </label>
      <label class="field">
        <span>View</span>
        <select id="viewMode">
          <option value="cards">Mixed front page</option>
          <option value="source">Grouped by source</option>
        </select>
      </label>
      <button id="reload" type="button">Reset</button>
    </div>
    <div class="source-pills" id="sourcePills"></div>
    <div class="summary">
      <div class="summary-card">
        <strong>Visible stories</strong>
        <span id="visibleCount">0</span>
      </div>
      <div class="summary-card">
        <strong>Source focus</strong>
        <span id="activeSource">All</span>
      </div>
      <div class="summary-card">
        <strong>Top ranked match</strong>
        <span id="topRank">-</span>
      </div>
      <div class="summary-card">
        <strong>Search state</strong>
        <span id="searchState">Everything</span>
      </div>
    </div>
  </section>

  <section class="content">
    <main class="stories" id="stories"></main>
    <aside class="sidebar">
      <section class="sidebar-card">
        <h2>Source Snapshot</h2>
        <div class="source-list" id="sourceStats"></div>
      </section>
      <section class="sidebar-card">
        <h2>Reading Mode</h2>
        <ol>
          <li>Use <strong>Mixed front page</strong> to scan everything like a single trend desk.</li>
          <li>Use <strong>Grouped by source</strong> if you want each publisher separated.</li>
          <li>Story rank is preserved from the source page captured at scrape time.</li>
        </ol>
      </section>
      <section class="sidebar-card">
        <h2>Coverage</h2>
        <p id="coverageNote">Checking source availability...</p>
      </section>
    </aside>
  </section>

  <div class="footer">Self-contained dashboard with embedded scraper output, ready for GitHub Pages.</div>
</div>
<script>
const RAW = {data_json};
const SOURCES = [...new Set(RAW.map(d => d.source))];
const state = {{ raw: RAW, filterText: "", sortMode: "rankAsc", source: "All", viewMode: "cards" }};

function asc(a, b) {{
  return a < b ? -1 : a > b ? 1 : 0;
}}

function formatStamp(value) {{
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString("en-IN", {{
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit"
  }});
}}

function applyFilters(rows) {{
  let result = rows.slice();
  if (state.source !== "All") {{
    result = result.filter(d => d.source === state.source);
  }}
  if (state.filterText) {{
    const q = state.filterText.toLowerCase();
    result = result.filter(d => (d.headline || "").toLowerCase().includes(q));
  }}
  switch (state.sortMode) {{
    case "rankAsc": result.sort((a, b) => a.rank - b.rank); break;
    case "rankDesc": result.sort((a, b) => b.rank - a.rank); break;
    case "alpha": result.sort((a, b) => asc(a.headline, b.headline)); break;
  }}
  return result;
}}

function bySource(rows) {{
  return SOURCES.map(source => ({{
    source,
    rows: rows.filter(row => row.source === source)
  }})).filter(group => group.rows.length);
}}

function sourceDomain(link) {{
  try {{
    return new URL(link).hostname.replace(/^www\\./, "");
  }} catch (_err) {{
    return link;
  }}
}}

function cardTemplate(row) {{
  return `
    <article class="story-card">
      <div class="story-top">
        <span class="badge">${{row.source}}</span>
        <span class="rank">Rank #${{row.rank}}</span>
      </div>
      <h3>${{row.headline}}</h3>
      <div class="story-meta">
        <span>${{formatStamp(row.collected_at_iso)}}</span>
        <span>${{sourceDomain(row.link)}}</span>
      </div>
      <a class="story-link" href="${{row.link}}" target="_blank" rel="noopener">Open article ↗</a>
    </article>
  `;
}}

function groupTemplate(group) {{
  return `
    <section class="group-card">
      <div class="story-top">
        <span class="badge">${{group.source}}</span>
        <span class="rank">${{group.rows.length}} visible stories</span>
      </div>
      <div class="stories">${{group.rows.map(cardTemplate).join("")}}</div>
    </section>
  `;
}}

function renderStories(rows) {{
  const root = document.querySelector("#stories");
  if (!rows.length) {{
    root.innerHTML = `<div class="empty">No stories match this filter yet. Try resetting the search or switching source.</div>`;
    return;
  }}
  if (state.viewMode === "source") {{
    root.innerHTML = bySource(rows).map(groupTemplate).join("");
    return;
  }}
  root.innerHTML = rows.map(cardTemplate).join("");
}}

function renderSourceStats(rows) {{
  document.querySelector("#sourceStats").innerHTML = SOURCES.map(source => {{
    const count = rows.filter(row => row.source === source).length;
    return `<div class="source-row"><strong>${{source}}</strong><span>${{count}} visible</span></div>`;
  }}).join("");
}}

function renderSummary(rows) {{
  document.querySelector("#visibleCount").textContent = String(rows.length);
  document.querySelector("#activeSource").textContent = state.source;
  document.querySelector("#topRank").textContent = rows.length ? `#${{rows[0].rank}}` : "-";
  document.querySelector("#searchState").textContent = state.filterText ? `“${{state.filterText}}”` : "Everything";
}}

function renderHeroMetrics() {{
  document.querySelector("#metricSources").textContent = String(SOURCES.length);
  document.querySelector("#metricStories").textContent = String(RAW.length);
  const latest = RAW.reduce((acc, row) => {{
    const stamp = row.collected_at_iso || "";
    return stamp > acc ? stamp : acc;
  }}, "");
  document.querySelector("#metricRefresh").textContent = formatStamp(latest);
}}

function renderCoverage() {{
  const missing = SOURCES.filter(source => !RAW.some(row => row.source === source));
  document.querySelector("#coverageNote").textContent = missing.length
    ? `No stories in the current snapshot for: ${{missing.join(", ")}}. That usually means the publisher blocked access or returned no qualifying links during the latest run.`
    : "All tracked sources returned at least one story in the latest snapshot.";
}}

function renderPills() {{
  const host = document.querySelector("#sourcePills");
  host.innerHTML = "";
  for (const source of ["All", ...SOURCES]) {{
    const button = document.createElement("button");
    button.className = `pill${{state.source === source ? " active" : ""}}`;
    button.type = "button";
    button.textContent = source;
    button.addEventListener("click", () => {{
      state.source = source;
      document.querySelector("#sourceSelect").value = source;
      render();
    }});
    host.appendChild(button);
  }}
}}

function fillSourceSelect() {{
  document.querySelector("#sourceSelect").innerHTML =
    ["All", ...SOURCES].map(source => `<option value="${{source}}">${{source}}</option>`).join("");
}}

function render() {{
  const rows = applyFilters(state.raw);
  renderStories(rows);
  renderSourceStats(rows);
  renderSummary(rows);
  renderPills();
}}

document.querySelector("#search").addEventListener("input", ev => {{
  state.filterText = ev.target.value || "";
  render();
}});
document.querySelector("#sourceSelect").addEventListener("change", ev => {{
  state.source = ev.target.value;
  render();
}});
document.querySelector("#sort").addEventListener("change", ev => {{
  state.sortMode = ev.target.value;
  render();
}});
document.querySelector("#viewMode").addEventListener("change", ev => {{
  state.viewMode = ev.target.value;
  render();
}});
document.querySelector("#reload").addEventListener("click", () => {{
  state.filterText = "";
  state.sortMode = "rankAsc";
  state.source = "All";
  state.viewMode = "cards";
  document.querySelector("#search").value = "";
  document.querySelector("#sourceSelect").value = "All";
  document.querySelector("#sort").value = "rankAsc";
  document.querySelector("#viewMode").value = "cards";
  render();
}});

fillSourceSelect();
renderHeroMetrics();
renderCoverage();
render();
</script>
"""


def scrape_job(page, job, wait_ms, retries, nav_timeout_ms):
    if job.get("mode") == "rss":
        try:
            return normalize_rows(job, fetch_rss_items(job))
        except Exception as exc:
            print(f"[WARN] {job['source']} RSS extraction failed: {exc}", file=sys.stderr)
            return []

    items = []
    last_error = None
    for _ in range(retries + 1):
        try:
            page.goto(job["url"], wait_until="domcontentloaded", timeout=nav_timeout_ms)
            time.sleep(wait_ms / 1000.0)
            click_cookie_banners(page)
            for _ in range(3):
                page.mouse.wheel(0, 1400)
                time.sleep(0.5)
            candidates = extract_anchors(page, selectors=job.get("selectors"))
            items = normalize_rows(job, candidates)
            if items:
                return items
        except Exception as exc:
            last_error = exc
    if last_error:
        print(f"[WARN] {job['source']} extraction failed: {last_error}", file=sys.stderr)
    return items


def launch_browser_and_page(playwright, args, channel_override=None):
    browser_type = {
        "chromium": playwright.chromium,
        "firefox": playwright.firefox,
        "webkit": playwright.webkit,
    }[args.engine]
    launch_kwargs = {
        "headless": args.headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    channel = channel_override
    if args.engine == "chromium" and channel is None and args.channel in ("chrome", "msedge"):
        channel = args.channel
    if args.engine == "chromium" and channel:
        launch_kwargs["channel"] = channel
    browser = browser_type.launch(**launch_kwargs)
    context = browser.new_context(
        viewport={"width": 1440, "height": 960},
        user_agent=UA_DESKTOP_CHROME,
        locale="en-US",
        timezone_id="Asia/Kolkata",
    )
    add_stealth(context)
    page = context.new_page()
    add_stealth(page)
    return browser, context, page


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site",
        choices=["indianexpress", "hindustantimes", "livemint", "indiatoday", "toi_etimes", "toi_viral", "ndtv", "all"],
        default="all",
    )
    parser.add_argument("--out_csv", default="trending_sources.csv")
    parser.add_argument("--out_json", default="trending_sources.json")
    parser.add_argument("--out_html", default="")
    parser.add_argument("--engine", choices=["chromium", "firefox", "webkit"], default="chromium")
    parser.add_argument("--channel", choices=["none", "chrome", "msedge"], default="none")
    parser.add_argument("--wait_ms", type=int, default=2500)
    parser.add_argument("--nav_timeout_ms", type=int, default=60000)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser, context, page = launch_browser_and_page(playwright, args)
        chrome_browser = chrome_context = chrome_page = None
        if args.engine == "chromium" and args.channel == "none":
            try:
                chrome_browser, chrome_context, chrome_page = launch_browser_and_page(
                    playwright,
                    args,
                    channel_override="chrome",
                )
            except Exception as exc:
                print(f"[WARN] Chrome fallback unavailable: {exc}", file=sys.stderr)

        timestamp = now_iso()
        collected = []
        for job in SITE_JOBS:
            if args.site not in (job["site_key"], "all"):
                continue
            active_page = chrome_page if job.get("prefer_channel") == "chrome" and chrome_page is not None else page
            items = scrape_job(
                active_page,
                job=job,
                wait_ms=args.wait_ms,
                retries=args.retries,
                nav_timeout_ms=args.nav_timeout_ms,
            )
            for row in items:
                row["collected_at_iso"] = timestamp
            collected.extend(items)

        for index, row in enumerate(collected, 1):
            print(f"{index:02d}. [{row['source']}] #{row['rank']} {row['headline']} | {row['link']}")

        if args.out_csv:
            with open(args.out_csv, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["source", "rank", "headline", "link", "collected_at_iso"],
                )
                writer.writeheader()
                writer.writerows(collected)
            print(f"Saved CSV: {args.out_csv} ({len(collected)} rows)")

        if args.out_json:
            with open(args.out_json, "w", encoding="utf-8") as handle:
                json.dump(collected, handle, ensure_ascii=False, indent=2)
            print(f"Saved JSON: {args.out_json} ({len(collected)} items)")

        if args.out_html:
            with open(args.out_html, "w", encoding="utf-8") as handle:
                handle.write(render_html(collected))
            print(f"Saved HTML: {args.out_html}")

        context.close()
        browser.close()
        if chrome_context is not None:
            chrome_context.close()
        if chrome_browser is not None:
            chrome_browser.close()


if __name__ == "__main__":
    main()
