"""
Playwright-based course page harvester, deterministic DOM/SEO/MarTech/Talking-Heads
scanner, and Wayback Machine 12-month baseline fetcher.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"

WAF_BLOCK_PATTERNS = [
    r"access\s+blocked",
    r"unusual\s+activity\s+on\s+your\s+web\s+page\s+connection",
    r"just\s+a\s+moment\.\.\.",
    r"verify\s+you\s+are\s+human",
    r"attention\s+required!\s*\|\s*cloudflare",
    r"sorry,\s+you\s+have\s+been\s+blocked",
    r"request\s+blocked\s+by\s+security",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 HullHERadar/1.0"
)

VIDEO_HOST_PATTERNS = [
    ("YouTube", r"(youtube\.com|youtu\.be|youtube-nocookie\.com)"),
    ("Vimeo", r"vimeo\.com"),
    ("Panopto", r"panopto\."),
    ("Wistia", r"wistia\.(com|net)"),
    ("Brightcove", r"brightcove\."),
    ("Kaltura", r"kaltura\."),
    ("Stream/Mediasite", r"(mediasite|microsoftstream)"),
]

ACCREDITATION_KEYWORDS = [
    "aacsb", "amba", "equis", "cmi", "cim", "cima", "cipd", "acca",
    "icaew", "chartered management institute", "chartered institute",
    "small business charter", "sbc", "efmd", "prme", "tef",
]

CHAT_WIDGET_SIGNATURES = {
    "Unibuddy": r"unibuddy",
    "GeckoChat": r"gecko(engage|chat|form)",
    "Intercom": r"intercom(cdn)?\.io|widget\.intercom",
    "LiveChat": r"livechatinc\.com",
    "Drift": r"js\.driftt\.com",
    "Zendesk Chat": r"static\.zdassets\.com|zopim",
    "Tawk.to": r"embed\.tawk\.to",
    "WhatsApp": r"wa\.me/|api\.whatsapp\.com",
    "The Ambassador Platform": r"theambassadorplatform",
}

MARTECH_SIGNATURES = {
    "GA4 / gtag": r"googletagmanager\.com/gtag/js|G-[A-Z0-9]{6,}",
    "Google Tag Manager": r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]+",
    "Meta Pixel": r"connect\.facebook\.net|fbq\(",
    "TikTok Pixel": r"analytics\.tiktok\.com|ttq\.",
    "LinkedIn Insight": r"snap\.licdn\.com|_linkedin_partner_id",
    "Microsoft Clarity": r"clarity\.ms/tag",
    "Hotjar": r"static\.hotjar\.com",
    "Microsoft Ads (UET)": r"bat\.bing\.com",
    "Snapchat Pixel": r"sc-static\.net/scevent",
    "Cookiebot / OneTrust": r"(cookiebot\.com|onetrust\.com|cookielaw\.org)",
}

CMS_SIGNATURES = {
    "Contensis": r"contensis|zengenti",
    "Squiz Matrix / Funnelback": r"squiz|funnelback",
    "Drupal": r"drupal|sites/default/files",
    "Sitecore": r"sitecore|-/media/",
    "WordPress": r"wp-content/|wp-includes/",
    "Terminalfour (T4)": r"terminalfour|/media/t4/",
    "Next.js": r"__NEXT_DATA__|/_next/static/",
    "Umbraco": r"umbraco",
}


def compute_content_hash(clean_text: str) -> str:
    normalised = re.sub(r"\s+", " ", clean_text.strip().lower())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _detect_patterns(blob: str, signatures: dict[str, str]) -> list[str]:
    found: list[str] = []
    for label, pattern in signatures.items():
        if re.search(pattern, blob, re.IGNORECASE):
            found.append(label)
    return found


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def parse_html_signals(html: str, url: str) -> dict[str, Any]:
    """Extract clean text, Talking Heads, accreditations badges, SEO, Schema, UX & MarTech."""
    soup = _make_soup(html)

    # 1. Title, Meta, Canonical, Headings
    title_tag = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta_desc_el = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    meta_desc = (meta_desc_el.get("content") or "").strip() if meta_desc_el else ""
    canonical_el = soup.find("link", attrs={"rel": re.compile(r"canonical", re.I)})
    canonical_url = (canonical_el.get("href") or "").strip() if canonical_el else ""
    h1_count = len(soup.find_all("h1"))
    h2_count = len(soup.find_all("h2"))

    # 2. Images & Alt Text + Accreditation Badge Hints
    imgs = soup.find_all("img")
    total_images = len(imgs)
    missing_alt_count = 0
    accreditation_badges: list[str] = []
    for img in imgs:
        alt = (img.get("alt") or "").strip()
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not alt:
            missing_alt_count += 1
        combined = f"{alt} {src}".lower()
        for kw in ACCREDITATION_KEYWORDS:
            if kw in combined:
                label = alt if alt else src.split("/")[-1]
                if label and label not in accreditation_badges:
                    accreditation_badges.append(label[:120])
                break

    # 3. JSON-LD Schema.org types
    schema_types: list[str] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                t = item.get("@type")
                if isinstance(t, str) and t not in schema_types:
                    schema_types.append(t)
                elif isinstance(t, list):
                    for sub_t in t:
                        if isinstance(sub_t, str) and sub_t not in schema_types:
                            schema_types.append(sub_t)
                for v in item.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(item, list):
                stack.extend(item)

    schema_lower = {s.lower() for s in schema_types}

    # 4. Talking Heads / Video Detection
    videos: list[dict[str, str]] = []
    seen_video_srcs: set[str] = set()
    for iframe in soup.find_all("iframe"):
        src = (iframe.get("src") or iframe.get("data-src") or "").strip()
        if not src or src in seen_video_srcs:
            continue
        for host_name, pattern in VIDEO_HOST_PATTERNS:
            if re.search(pattern, src, re.IGNORECASE):
                seen_video_srcs.add(src)
                title = (iframe.get("title") or iframe.get("aria-label") or "").strip()
                videos.append({"provider": host_name, "title": title or "Embedded video", "src": src[:200]})
                break

    for vid in soup.find_all("video"):
        src = (vid.get("src") or "").strip()
        if not src:
            source_el = vid.find("source")
            src = (source_el.get("src") or "").strip() if source_el else "html5-video"
        if src not in seen_video_srcs:
            seen_video_srcs.add(src)
            title = (vid.get("title") or vid.get("aria-label") or "HTML5 Video").strip()
            videos.append({"provider": "HTML5 Video", "title": title, "src": src[:200]})

    # Also catch YouTube/Vimeo lightbox links (common on UK uni pages)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href in seen_video_srcs:
            continue
        if re.search(r"(youtube\.com/watch|youtu\.be/|vimeo\.com/\d+)", href, re.I):
            seen_video_srcs.add(href)
            label = a.get_text(" ", strip=True) or (a.get("aria-label") or "Video link")
            videos.append({"provider": "Video Link", "title": label[:100], "src": href[:200]})

    if videos:
        talking_heads_summary = "; ".join(
            f"{v['provider']}: {v['title']}" for v in videos[:6]
        )
    else:
        talking_heads_summary = "No embedded videos detected on page"

    # 5. Chat Widgets, CMS, MarTech Pixels, Sticky CTA heuristic
    chat_widgets = _detect_patterns(html, CHAT_WIDGET_SIGNATURES)
    martech_pixels = _detect_patterns(html, MARTECH_SIGNATURES)
    cms_hits = _detect_patterns(html, CMS_SIGNATURES)
    cms_platform = cms_hits[0] if cms_hits else "Unknown"

    sticky_cta = 1 if re.search(
        r"(sticky|fixed)[^\"']{0,40}(cta|apply|bar|nav|action)", html, re.IGNORECASE
    ) else 0

    # 6. Clean Main Text for LLM Extraction & Content Hashing
    # Remove noisy boilerplate tags before extracting text
    text_soup = _make_soup(html)
    for tag in text_soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    main_el = text_soup.find("main") or text_soup.find(attrs={"role": "main"}) or text_soup.body or text_soup
    raw_lines = [line.strip() for line in main_el.get_text("\n").splitlines() if line.strip()]
    clean_text = "\n".join(raw_lines)
    word_count = len(clean_text.split())
    content_hash = compute_content_hash(clean_text)

    # Also detect any embedded UCAS Explore course URL on the page for fallback
    detected_ucas_url = ""
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if re.search(r"(ucas\.com/explore/courses/|digital\.ucas\.com/coursedisplay/courses/)", h, re.I):
            detected_ucas_url = h
            break

    return {
        "clean_text": clean_text,
        "content_hash": content_hash,
        "word_count": word_count,
        "talking_heads_count": len(videos),
        "talking_heads_summary": talking_heads_summary,
        "accreditation_badges": accreditation_badges,
        "detected_ucas_url": detected_ucas_url,
        "seo": {
            "title_tag": title_tag[:300],
            "title_len": len(title_tag),
            "meta_description": meta_desc[:500],
            "meta_desc_len": len(meta_desc),
            "h1_count": h1_count,
            "h2_count": h2_count,
            "word_count": word_count,
            "canonical_url": canonical_url[:400],
            "missing_alt_count": missing_alt_count,
            "total_images": total_images,
            "schema_types": ", ".join(schema_types),
            "has_course_schema": 1 if ("course" in schema_lower or "courseinstance" in schema_lower) else 0,
            "has_faq_schema": 1 if "faqpage" in schema_lower else 0,
            "has_video_schema": 1 if "videoobject" in schema_lower else 0,
            "chat_widget": ", ".join(chat_widgets) if chat_widgets else "None detected",
            "sticky_cta_detected": sticky_cta,
            "cms_platform": cms_platform,
            "martech_pixels": ", ".join(martech_pixels) if martech_pixels else "None detected",
        },
    }


def is_waf_or_block_page(clean_text: str, title_tag: str = "", word_count: int = 0) -> bool:
    """Detect WAF / cyber-security / anti-bot block pages so they never overwrite good data."""
    if word_count > 600:
        return False
    sample = f"{title_tag}\n{clean_text[:2000]}"
    return any(re.search(pat, sample, re.IGNORECASE) for pat in WAF_BLOCK_PATTERNS)


def _cache_file_for_url(url: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(url.strip().lower().encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _load_fresh_cache(url: str, ttl_hours: float) -> dict[str, Any] | None:
    if ttl_hours <= 0:
        return None
    cf = _cache_file_for_url(url)
    if not cf.exists():
        return None
    age_hours = (time.time() - cf.stat().st_mtime) / 3600.0
    if age_hours > ttl_hours:
        return None
    try:
        data = json.loads(cf.read_text(encoding="utf-8"))
        if data.get("waf_blocked"):
            return None
        data["engine"] = f"local_cache ({age_hours:.1f}h old)"
        return data
    except Exception:
        return None


def _save_cache(url: str, parsed: dict[str, Any]) -> None:
    if parsed.get("waf_blocked") or parsed.get("word_count", 0) < 150:
        return
    try:
        _cache_file_for_url(url).write_text(
            json.dumps(parsed, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def harvest_page_playwright(
    url: str,
    timeout_ms: int = 35000,
    cache_ttl_hours: float = 24.0,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Render the course page in headless Chromium/Edge, dismiss cookie banners,
    expand module/year tabs & accordions, and return full HTML + metadata.
    Uses a 24-hour local cache by default so dev/re-runs never hammer competitor servers.
    """
    if not force_refresh:
        cached = _load_fresh_cache(url, cache_ttl_hours)
        if cached:
            return cached
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return harvest_page_requests(url)

    with sync_playwright() as p:
        browser = None
        for launch_kwargs in (
            {"headless": True, "channel": "msedge"},
            {"headless": True, "channel": "chrome"},
            {"headless": True},
        ):
            try:
                browser = p.chromium.launch(**launch_kwargs)
                break
            except Exception:
                continue
        if browser is None:
            return harvest_page_requests(url)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
        )
        page = context.new_page()
        http_status = 0
        resolved_url = url
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if resp:
                http_status = resp.status
                resolved_url = resp.url
            page.wait_for_timeout(2000)

            # Best-effort dismiss cookie banners
            for sel in [
                "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
                "#CybotCookiebotDialogBodyButtonDecline",
                "button:has-text('Accept all')",
                "button:has-text('Accept All')",
                "button:has-text('Accept cookies')",
                "#onetrust-accept-btn-handler",
                "#ccc-notify-accept",
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=500):
                        btn.click(timeout=1000)
                        page.wait_for_timeout(300)
                        break
                except Exception:
                    pass

            # Scroll through the page to trigger lazy-loaded module widgets / XHR calls
            page.evaluate(
                """
                async () => {
                    const step = 700;
                    for (let y = 0; y < document.body.scrollHeight; y += step) {
                        window.scrollTo(0, y);
                        await new Promise(r => setTimeout(r, 120));
                    }
                    window.scrollTo(0, 0);
                }
                """
            )
            page.wait_for_timeout(1200)

            # Open all <details> and expand accordion buttons
            page.evaluate(
                """
                () => {
                    document.querySelectorAll('details').forEach(d => d.open = true);
                }
                """
            )

            # Multi-state tab & accordion capture: click Year 1/2/3 / Modules controls
            # ONE BY ONE and capture each revealed panel so mutually exclusive tabs are never lost.
            extra_tab_texts: list[str] = []
            seen_tab_snippets: set[str] = set()
            tab_pattern = re.compile(
                r"(year\s*[1-4]|final\s*year|stage\s*[1-4]|level\s*[4-6]|module|what\s+you('ll|’ll|\s+will)\s+study|course\s+(content|structure)|core\s+modules|optional\s+modules|fees|tuition|funding|international\s+students?)",
                re.IGNORECASE,
            )
            controls = page.locator("button, [role='tab'], a[href^='#'], summary")
            ctrl_count = min(controls.count(), 180)
            clicked = 0
            for idx in range(ctrl_count):
                if clicked >= 30:
                    break
                el = controls.nth(idx)
                try:
                    if not el.is_visible(timeout=200):
                        continue
                    label = (el.inner_text(timeout=300) or "").strip()
                    if not label or len(label) > 85 or not tab_pattern.search(label):
                        continue
                    el.scroll_into_view_if_needed(timeout=1000)
                    el.click(timeout=1200)
                    clicked += 1
                    page.wait_for_timeout(450)

                    # Grab active tabpanel or controlled region text if present
                    panel_text = page.evaluate(
                        """
                        (btn) => {
                            const ctrlId = btn.getAttribute('aria-controls');
                            if (ctrlId) {
                                const target = document.getElementById(ctrlId);
                                if (target && target.innerText) return target.innerText.trim();
                            }
                            const activePanels = Array.from(document.querySelectorAll('[role="tabpanel"]:not([hidden]), .tab-pane.active, .is-active'));
                            const visible = activePanels
                                .filter(p => p.offsetParent !== null && p.innerText && p.innerText.trim().length > 40)
                                .map(p => p.innerText.trim());
                            if (visible.length) return visible.join('\\n---\\n');
                            const parent = btn.closest('section, article, details, div');
                            return parent && parent.innerText ? parent.innerText.trim().slice(0, 4000) : '';
                        }
                        """,
                        el.element_handle(),
                    )
                    if panel_text and len(panel_text) > 30:
                        sig = hashlib.md5(panel_text[:300].encode("utf-8", errors="ignore")).hexdigest()
                        if sig not in seen_tab_snippets:
                            seen_tab_snippets.add(sig)
                            extra_tab_texts.append(f"=== TAB/SECTION: {label} ===\n{panel_text[:6000]}")
                except Exception:
                    continue

            # Generic combobox / <select> Year-filter cycler (e.g. Hull's Contensis/React Module options)
            year_opt_re = re.compile(
                r"^(year\s*[1-4]|first\s*year|second\s*year|third\s*year|fourth\s*year|final\s*year|stage\s*[1-4]|level\s*[4-6]|foundation\s*year)$",
                re.IGNORECASE,
            )
            combos = page.locator("button[role='combobox']")
            combo_count = min(combos.count(), 12)
            for c_idx in range(combo_count):
                cb = combos.nth(c_idx)
                try:
                    if not cb.is_visible(timeout=200):
                        continue
                    cb.scroll_into_view_if_needed(timeout=800)
                    cb.click(timeout=1000)
                    page.wait_for_timeout(350)
                    opt_texts = page.evaluate(
                        """
                        () => Array.from(document.querySelectorAll('[role="option"]'))
                            .filter(o => o.offsetParent !== null)
                            .map(o => (o.innerText || '').trim())
                            .filter(Boolean)
                        """
                    )
                    year_opts = [t for t in opt_texts if year_opt_re.search(t)]
                    if not year_opts:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(150)
                        continue

                    # Found a Year filter combobox! Cycle through every year option
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(150)
                    for y_opt in year_opts:
                        try:
                            cb.click(timeout=1000)
                            page.wait_for_timeout(300)
                            opt_el = page.locator("[role='option']").filter(has_text=re.compile(rf"^{re.escape(y_opt)}$", re.I)).first
                            opt_el.click(timeout=1000)
                            page.wait_for_timeout(500)
                            # Capture all visible module cards/headings after filtering to this year
                            mod_text = page.evaluate(
                                """
                                (btn) => {
                                    let el = btn;
                                    let best = '';
                                    for (let i = 0; i < 8 && el && el.parentElement; i++) {
                                        el = el.parentElement;
                                        const txt = (el.innerText || '').trim();
                                        if (/credits|compulsory|optional/i.test(txt) && txt.length > 200) {
                                            best = txt;
                                            if (txt.length > 500) break;
                                        }
                                    }
                                    return best.slice(0, 8000);
                                }
                                """,
                                cb.element_handle(),
                            )
                            if mod_text:
                                extra_tab_texts.append(f"=== MODULE FILTER: {y_opt} ===\n{mod_text}")
                        except Exception:
                            try:
                                page.keyboard.press("Escape")
                            except Exception:
                                pass
                    break
                except Exception:
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass

            page.wait_for_timeout(800)
            html = page.content()

            # 1-hop module subpage / query-param detector (e.g. ?modules=true or /modules)
            mod_sub_url = page.evaluate(
                r"""
                (baseHost) => {
                    const links = Array.from(document.querySelectorAll('a[href]'));
                    for (const a of links) {
                        const h = a.href || '';
                        const t = (a.innerText || '').trim().toLowerCase();
                        try {
                            const u = new URL(h);
                            if (u.hostname !== baseHost) continue;
                            if (
                                /[?&]modules=true/i.test(u.search) ||
                                (/\/modules\/?$/i.test(u.pathname) && u.pathname.length > 10) ||
                                (t.includes('module') && (u.search.includes('module') || u.pathname.includes('module')))
                            ) {
                                return h;
                            }
                        } catch (e) {}
                    }
                    return '';
                }
                """,
                resolved_url.split("/")[2] if "://" in resolved_url else "",
            )
            if mod_sub_url and mod_sub_url != resolved_url:
                try:
                    page.goto(mod_sub_url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(2000)
                    page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
                    sub_html = page.content()
                    sub_parsed = parse_html_signals(sub_html, mod_sub_url)
                    if sub_parsed["word_count"] > 50:
                        extra_tab_texts.append(
                            f"=== LINKED MODULES PAGE ({mod_sub_url}) ===\n{sub_parsed['clean_text'][:25000]}"
                        )
                except Exception:
                    pass
        finally:
            browser.close()

    parsed = parse_html_signals(html, resolved_url)
    if extra_tab_texts:
        extra_block = "\n\n--- EXPANDED TABS & MODULES ---\n" + "\n\n".join(extra_tab_texts)
        parsed["clean_text"] = parsed["clean_text"] + extra_block
        parsed["word_count"] = len(parsed["clean_text"].split())
        parsed["content_hash"] = compute_content_hash(parsed["clean_text"])
    parsed["http_status"] = http_status
    parsed["resolved_url"] = resolved_url
    parsed["engine"] = "playwright"
    parsed["waf_blocked"] = is_waf_or_block_page(
        parsed["clean_text"],
        parsed["seo"].get("title_tag", ""),
        parsed["word_count"],
    )
    if not parsed["waf_blocked"]:
        _save_cache(url, parsed)
    return parsed


def harvest_page_requests(url: str, timeout: int = 25) -> dict[str, Any]:
    """Lightweight HTTP fallback when Playwright isn't needed or installed."""
    resp = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True
    )
    parsed = parse_html_signals(resp.text, resp.url)
    parsed["http_status"] = resp.status_code
    parsed["resolved_url"] = resp.url
    parsed["engine"] = "requests"
    return parsed


def verify_url(url: str, timeout: int = 15) -> dict[str, Any]:
    """Quick HTTP check for --verify-urls."""
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True
        )
        soup = _make_soup(resp.text[:8000])
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        return {
            "ok": 200 <= resp.status_code < 400,
            "status": resp.status_code,
            "resolved_url": resp.url,
            "title": title[:100],
        }
    except Exception as exc:
        return {"ok": False, "status": 0, "resolved_url": url, "title": str(exc)[:100]}


def fetch_wayback_baseline(url: str, months_ago: int = 12) -> dict[str, Any] | None:
    """
    Query the Internet Archive Wayback CDX API for the closest HTTP 200 snapshot
    from ~12 months ago to power Day-1 'Changes in the last year?' diffs.
    """
    target_dt = datetime.now(timezone.utc) - timedelta(days=30 * months_ago)
    target_ts = target_dt.strftime("%Y%m%d")

    cdx_url = "https://web.archive.org/cdx/search/cdx"
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "from": (target_dt - timedelta(days=90)).strftime("%Y%m%d"),
        "to": (target_dt + timedelta(days=90)).strftime("%Y%m%d"),
        "closest": target_ts,
        "sort": "closest",
        "limit": 1,
    }
    try:
        r = requests.get(cdx_url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
        if r.status_code != 200:
            return None
        rows = r.json()
        if not rows or len(rows) < 2:
            return None
        ts, orig_url, _status = rows[1]
        archive_url = f"https://web.archive.org/web/{ts}id_/{orig_url}"
        page_resp = requests.get(archive_url, headers={"User-Agent": USER_AGENT}, timeout=25)
        if page_resp.status_code != 200 or len(page_resp.text) < 500:
            return None
        parsed = parse_html_signals(page_resp.text, archive_url)
        parsed["wayback_timestamp"] = ts
        parsed["archive_url"] = archive_url
        return parsed
    except Exception:
        return None


def fetch_ucas_modules_page(
    institution_name: str,
    course_title: str,
    ucas_code: str = "",
    direct_ucas_url: str = "",
) -> dict[str, Any] | None:
    """
    Fallback harvester: fetches the university's UCAS Explore course page when
    the university's own marketing page omits Year 1/2/3 module lists.
    """
    from urllib.parse import quote_plus
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return None

    with sync_playwright() as p:
        browser = None
        for kw in ({"headless": True, "channel": "msedge"}, {"headless": True, "channel": "chrome"}, {"headless": True}):
            try:
                browser = p.chromium.launch(**kw)
                break
            except Exception:
                continue
        if browser is None:
            return None

        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1440, "height": 900}, locale="en-GB")
        page = context.new_page()
        target_url = direct_ucas_url.strip()
        try:
            if not target_url:
                # Search UCAS Explore for the course + institution
                q = f"{course_title} {institution_name}".strip()
                search_url = f"https://www.ucas.com/explore/search/courses?query={quote_plus(q)}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3500)
                # Accept cookies if shown
                for sel in ["#onetrust-accept-btn-handler", "button:has-text('Accept all')"]:
                    try:
                        b = page.locator(sel).first
                        if b.is_visible(timeout=500):
                            b.click(timeout=1000)
                            page.wait_for_timeout(300)
                            break
                    except Exception:
                        pass

                # Find matching UCAS course card link
                inst_key = institution_name.lower().replace("university of ", "").replace(" university", "").strip()
                links = page.eval_on_selector_all(
                    "a[href*='/explore/courses/']",
                    "els => els.map(e => ({h: e.href, t: (e.closest('article, li, div')?.innerText || e.innerText || '').toLowerCase()}))"
                )
                for lk in links:
                    if inst_key in lk["t"] and (not ucas_code or ucas_code.lower() in lk["t"] or "business" in lk["t"]):
                        target_url = lk["h"]
                        break
                if not target_url and links:
                    for lk in links:
                        if inst_key in lk["t"]:
                            target_url = lk["h"]
                            break

            if not target_url:
                return None

            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            # Expand all accordions / Year 1-3 module sections on UCAS
            page.evaluate(
                """
                () => {
                    document.querySelectorAll('details').forEach(d => d.open = true);
                    document.querySelectorAll('button[aria-expanded="false"]').forEach(b => {
                        const t = (b.innerText || '').toLowerCase();
                        if (/year|module|course|assess|summary|content/i.test(t)) {
                            try { b.click(); } catch (e) {}
                        }
                    });
                }
                """
            )
            page.wait_for_timeout(1200)
            html = page.content()
            resolved = page.url
        finally:
            browser.close()

    parsed = parse_html_signals(html, resolved)
    parsed["ucas_url"] = resolved
    return parsed

