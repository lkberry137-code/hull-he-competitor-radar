"""
Hash-gated Gemini API structured extractor for all course-audit categories.
Uses the Gemini REST API directly (zero SDK dependency friction) with strict
JSON output and verbatim quote grounding to prevent hallucination.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
]

EXTRACTION_SYSTEM_PROMPT = """You are a Higher Education competitive intelligence analyst working for the University of Hull.
Your task is to extract structured course-audit data from a UK university undergraduate course webpage.

STRICT RULES:
1. Extract ONLY facts explicitly stated in the provided webpage text or accreditation badge hints.
2. NEVER guess, extrapolate, or use outside knowledge. If a category is not mentioned on the page, return "Not stated on page".
3. Keep each field concise, clear, and comparable across universities (suitable for an executive table and Power BI dashboard).
4. For `ucas_tariff_min` and `ucas_tariff_max`, return integers if UCAS tariff points are stated (e.g. 112), or null if only A-level grades (e.g. AAA) are given without a numeric tariff.
5. For `fee_home_gbp` and `fee_intl_gbp`, return the annual full-time GBP tuition fee as a plain integer (e.g. 9535 or 17500), or null if not stated.
6. In `evidence_quotes`, include short verbatim quotes (max 25 words each) from the page proving the key extracted claims for `entry_requirements`, `fees`, `usps`, `authentic_learning`, `accreditations`, and `employability_outcomes`.

Return a single valid JSON object with EXACTLY these keys:
{
  "entry_requirements": "string (e.g. '112 UCAS points (BBC at A-level); GCSE Maths & English grade 4/C')",
  "ucas_tariff_min": integer or null,
  "ucas_tariff_max": integer or null,
  "fees_home": "string (e.g. '£9,535 per year (UK full-time, 2026/27)' or 'Not stated on page')",
  "fee_home_gbp": integer or null,
  "fees_international": "string (e.g. '£17,500 per year (International full-time, 2026/27)' or 'Not stated on page')",
  "fee_intl_gbp": integer or null,
  "international_opportunities": "string (study abroad, exchange partners, international year)",
  "modules_yr1": "string (comma-separated list of Year 1 core/optional modules, or 'Not stated on page')",
  "modules_yr2": "string (comma-separated list of Year 2 core/optional modules, or 'Not stated on page')",
  "modules_yr3": "string (comma-separated list of Year 3/Final Year modules, or 'Not stated on page')",
  "assessments": "string (e.g. 'Coursework, exams, group presentations, consultancy report, dissertation')",
  "usps": "string (top 3-5 headline differentiators claimed on the page, separated by semicolons)",
  "equipment_facilities": "string (named labs, trading rooms, Bloomberg suites, enterprise hubs, software)",
  "authentic_learning": "string (placements, year in industry, live client briefs, named business partners)",
  "employability_outcomes": "string (graduate stats, career roles, named graduate employers)",
  "accreditations": "string (e.g. 'AACSB, AMBA, CMI, CIMA exemptions' or 'Not stated on page')",
  "subject_ranking_claimed": "string (any subject ranking/NSS/TEF boast explicitly made on this course page)",
  "other_programmes": "string (foundation year, placement year, joint honours, or specialist pathways listed on page)",
  "evidence_quotes": {
    "entry_requirements": "short verbatim quote or ''",
    "fees": "short verbatim quote or ''",
    "usps": "short verbatim quote or ''",
    "authentic_learning": "short verbatim quote or ''",
    "accreditations": "short verbatim quote or ''",
    "employability_outcomes": "short verbatim quote or ''"
  }
}
"""


def empty_extraction(note: str = "LLM extraction skipped") -> dict[str, Any]:
    return {
        "entry_requirements": note,
        "ucas_tariff_min": None,
        "ucas_tariff_max": None,
        "fees_home": note,
        "fee_home_gbp": None,
        "fees_international": note,
        "fee_intl_gbp": None,
        "international_opportunities": note,
        "modules_yr1": note,
        "modules_yr2": note,
        "modules_yr3": note,
        "assessments": note,
        "usps": note,
        "equipment_facilities": note,
        "authentic_learning": note,
        "employability_outcomes": note,
        "accreditations": note,
        "subject_ranking_claimed": note,
        "other_programmes": note,
        "evidence_quotes": {},
        "llm_model": "",
    }


def extract_course_fields_gemini(
    institution_name: str,
    course_title: str,
    clean_text: str,
    accreditation_badges: list[str] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Call Gemini REST API with JSON mode to extract all structured audit fields."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        return empty_extraction("GEMINI_API_KEY not configured")

    chosen_model = (model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)).strip()
    candidate_models = [chosen_model] + [m for m in FALLBACK_MODELS if m != chosen_model]

    # Cap page text at ~120k chars so long course pages (e.g. SHU) never lose bottom module sections
    trimmed_text = clean_text[:120000]
    badges_str = ", ".join(accreditation_badges or []) or "None detected"

    user_prompt = (
        f"Institution: {institution_name}\n"
        f"Course: {course_title}\n"
        f"Accreditation badge image alt-texts detected in DOM: {badges_str}\n\n"
        f"--- WEBPAGE TEXT START ---\n{trimmed_text}\n--- WEBPAGE TEXT END ---"
    )

    payload = {
        "system_instruction": {"parts": [{"text": EXTRACTION_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    last_err = ""
    for m in candidate_models:
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{m}:generateContent?key={api_key}"
        )
        t0 = time.monotonic()
        try:
            resp = requests.post(endpoint, json=payload, timeout=45)
        except Exception as exc:
            last_err = f"{m}: {exc}"
            continue
        latency_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code in (404, 429, 500, 503):
            last_err = f"{m} HTTP {resp.status_code}"
            time.sleep(1.0)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            last_err = f"{m} unexpected response: {str(data)[:200]}"
            continue

        usage_meta = data.get("usageMetadata") or {}
        in_tok = int(usage_meta.get("promptTokenCount") or 0)
        out_tok = int(usage_meta.get("candidatesTokenCount") or 0)
        tot_tok = int(usage_meta.get("totalTokenCount") or (in_tok + out_tok))
        # Flash-tier approximate pricing ($0.15 / 1M input, $0.60 / 1M output)
        est_cost = round((in_tok / 1_000_000) * 0.15 + (out_tok / 1_000_000) * 0.60, 6)

        parsed = json.loads(raw_text)
        parsed["llm_model"] = m
        parsed["_usage"] = {
            "model": m,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": tot_tok,
            "estimated_cost_usd": est_cost,
            "latency_ms": latency_ms,
        }
        return parsed

    raise RuntimeError(f"All Gemini models failed ({last_err})")


UCAS_GROUNDED_PROMPT = """You are a UK Higher Education competitive intelligence analyst.
The university's main marketing course page did not list Year 1, Year 2, and Year 3 modules.
Use Google Search to find the official UCAS course listing or official university programme specification for this exact undergraduate course and extract the Year 1, Year 2, and Year 3 modules and assessment methods.

STRICT RULES:
1. Rely ONLY on official UCAS or official university pages returned by search.
2. If you cannot verify a year's modules from search results, return "Not stated on page".
3. Return ONLY a valid JSON object (no markdown fences, no commentary) with EXACTLY these keys:
{
  "modules_yr1": "comma-separated list of Year 1 modules (Core/Optional if stated)",
  "modules_yr2": "comma-separated list of Year 2 modules",
  "modules_yr3": "comma-separated list of Year 3 / Final Year modules",
  "assessments": "concise summary of assessment methods if stated, else 'Not stated on page'",
  "source_label": "short label e.g. 'UCAS (NND2)' or 'University Programme Spec'"
}
"""


def extract_ucas_modules_grounded_gemini(
    institution_name: str,
    course_title: str,
    ucas_code: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    """
    Sanctioned fallback: uses Gemini with Google Search Grounding to look up
    official UCAS / programme spec Year 1-3 modules when the course page omits them.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        return {"modules_yr1": "Not stated on page", "modules_yr2": "Not stated on page", "modules_yr3": "Not stated on page", "assessments": "Not stated on page"}

    chosen_model = (model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)).strip()
    candidate_models = [chosen_model] + [m for m in FALLBACK_MODELS if m != chosen_model]

    query_text = (
        f"Find the official UCAS course listing (or official university programme specification) "
        f"for {institution_name} undergraduate {course_title} (UCAS code: {ucas_code or 'unknown'}). "
        f"Extract the Year 1, Year 2, and Year 3 modules and assessment methods as JSON."
    )
    payload = {
        "system_instruction": {"parts": [{"text": UCAS_GROUNDED_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": query_text}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},
    }

    for m in candidate_models:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        t0 = time.monotonic()
        try:
            resp = requests.post(endpoint, json=payload, timeout=50)
        except Exception:
            continue
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code in (404, 429, 500, 503):
            time.sleep(1.0)
            continue
        if resp.status_code != 200:
            continue

        data = resp.json()
        try:
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Strip optional markdown code fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```", 2)[1]
                if raw_text.lower().startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()
            parsed = json.loads(raw_text)
        except Exception:
            continue

        # Capture verified grounding source URL if present
        chunks = (data["candidates"][0].get("groundingMetadata") or {}).get("groundingChunks") or []
        source_urls = [
            (ch.get("web") or {}).get("uri", "")
            for ch in chunks
            if (ch.get("web") or {}).get("uri")
        ]
        parsed["source_url"] = source_urls[0] if source_urls else ""

        usage_meta = data.get("usageMetadata") or {}
        in_tok = int(usage_meta.get("promptTokenCount") or 0)
        out_tok = int(usage_meta.get("candidatesTokenCount") or 0)
        est_cost = round((in_tok / 1_000_000) * 0.15 + (out_tok / 1_000_000) * 0.60, 6)
        parsed["_usage"] = {
            "model": m,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "estimated_cost_usd": est_cost,
            "latency_ms": latency_ms,
        }
        return parsed

    return {"modules_yr1": "Not stated on page", "modules_yr2": "Not stated on page", "modules_yr3": "Not stated on page", "assessments": "Not stated on page"}

