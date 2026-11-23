"""
Google PageSpeed Insights v5 auditor for Mobile and Desktop Lighthouse scores
(Performance, Accessibility, Best Practices, SEO) and Core Web Vitals (LCP, CLS, INP).
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def _score_100(categories: dict[str, Any], key: str) -> int | None:
    val = (categories.get(key) or {}).get("score")
    if val is None:
        return None
    return int(round(float(val) * 100))


def _audit_num(audits: dict[str, Any], key: str) -> float | None:
    val = (audits.get(key) or {}).get("numericValue")
    if val is None:
        return None
    return round(float(val), 3)


def run_pagespeed_audit(url: str, strategy: str = "mobile", timeout: int = 90) -> dict[str, Any]:
    """Run Google PageSpeed Insights for a single URL and device strategy ('mobile' or 'desktop')."""
    params: list[tuple[str, str]] = [
        ("url", url),
        ("strategy", strategy),
        ("category", "performance"),
        ("category", "accessibility"),
        ("category", "best-practices"),
        ("category", "seo"),
    ]
    api_key = os.getenv("PAGESPEED_API_KEY", "").strip()
    if api_key:
        params.append(("key", api_key))

    last_err = ""
    for attempt in range(2):
        t0 = time.monotonic()
        try:
            resp = requests.get(PSI_ENDPOINT, params=params, timeout=timeout)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 429:
                return {"psi_status": "rate_limited", "latency_ms": latency_ms}
            if resp.status_code != 200:
                last_err = f"http_{resp.status_code}"
                continue

            data = resp.json()
            lh = data.get("lighthouseResult") or {}
            cats = lh.get("categories") or {}
            audits = lh.get("audits") or {}

            # INP from CrUX field data if present, else fallback to TBT lab proxy
            loading_exp = (data.get("loadingExperience") or {}).get("metrics") or {}
            inp_ms = (loading_exp.get("INTERACTION_TO_NEXT_PAINT") or {}).get("percentile")
            if inp_ms is None:
                inp_ms = _audit_num(audits, "total-blocking-time")

            return {
                "perf_score": _score_100(cats, "performance"),
                "accessibility_score": _score_100(cats, "accessibility"),
                "best_practices_score": _score_100(cats, "best-practices"),
                "seo_score": _score_100(cats, "seo"),
                "lcp_ms": _audit_num(audits, "largest-contentful-paint"),
                "cls": _audit_num(audits, "cumulative-layout-shift"),
                "inp_ms": float(inp_ms) if inp_ms is not None else None,
                "psi_status": "ok",
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            last_err = f"error: {str(exc)[:80]}"
    return {"psi_status": last_err or "error", "latency_ms": 0}
