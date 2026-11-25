"""
Main CLI runner for the University of Hull HE Competitor & Web/SEO Intelligence Radar.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import time
import os
from pathlib import Path
from typing import Any

import extractor
import harvester
import seo_auditor
import storage


def load_dotenv_simple(env_path: Path) -> None:
    """Zero-dependency .env loader so the tool runs even without python-dotenv."""
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_snapshot_record(
    course_key: str,
    source_url: str,
    harvest_data: dict[str, Any],
    extracted: dict[str, Any],
    is_wayback: bool = False,
    wayback_ts: str | None = None,
    scrape_status: str = "ok",
    scrape_notes: str = "",
) -> dict[str, Any]:
    return {
        "snapshot_date": storage.utc_now_iso(),
        "course_key": course_key,
        "source_url": source_url,
        "is_wayback_baseline": 1 if is_wayback else 0,
        "wayback_timestamp": wayback_ts,
        "content_hash": harvest_data.get("content_hash", ""),
        "word_count": harvest_data.get("word_count", 0),
        "entry_requirements": extracted.get("entry_requirements"),
        "ucas_tariff_min": extracted.get("ucas_tariff_min"),
        "ucas_tariff_max": extracted.get("ucas_tariff_max"),
        "ielts_overall": extracted.get("ielts_overall"),
        "ielts_min_component": extracted.get("ielts_min_component"),
        "ib_points": extracted.get("ib_points"),
        "english_alt_tests": extracted.get("english_alt_tests"),
        "intl_foundation_pathway": extracted.get("intl_foundation_pathway"),
        "intl_entry_summary": extracted.get("intl_entry_summary"),
        "fees_home": extracted.get("fees_home"),
        "fee_home_gbp": extracted.get("fee_home_gbp"),
        "fees_international": extracted.get("fees_international"),
        "fee_intl_gbp": extracted.get("fee_intl_gbp"),
        "international_opportunities": extracted.get("international_opportunities"),
        "modules_yr1": extracted.get("modules_yr1"),
        "modules_yr2": extracted.get("modules_yr2"),
        "modules_yr3": extracted.get("modules_yr3"),
        "assessments": extracted.get("assessments"),
        "usps": extracted.get("usps"),
        "equipment_facilities": extracted.get("equipment_facilities"),
        "authentic_learning": extracted.get("authentic_learning"),
        "employability_outcomes": extracted.get("employability_outcomes"),
        "accreditations": extracted.get("accreditations"),
        "subject_ranking_claimed": extracted.get("subject_ranking_claimed"),
        "talking_heads_count": harvest_data.get("talking_heads_count", 0),
        "talking_heads_summary": harvest_data.get("talking_heads_summary", ""),
        "other_programmes": extracted.get("other_programmes"),
        "module_source": extracted.get("module_source", "course_page"),
        "evidence_json": json.dumps(extracted.get("evidence_quotes") or {}, ensure_ascii=False),
        "llm_model": extracted.get("llm_model", ""),
        "scrape_status": scrape_status,
        "scrape_notes": scrape_notes,
    }


def extract_with_hash_cache(
    course_key: str,
    inst_name: str,
    course_title: str,
    harvest_data: dict[str, Any],
    skip_llm: bool,
    force_llm: bool,
) -> dict[str, Any]:
    if skip_llm:
        return extractor.empty_extraction("Skipped (--skip-llm)")

    content_hash = harvest_data["content_hash"]
    if not force_llm:
        cached = storage.get_cached_extraction_by_hash(course_key, content_hash)
        if cached:
            print(f"    [LLM cache hit] Content hash unchanged ({content_hash[:10]}) — skipping API call.")
            storage.log_api_usage(
                provider="gemini",
                model_or_endpoint=cached.get("llm_model") or "cache",
                course_key=course_key,
                status="cache_hit",
            )
            return {
                **cached,
                "evidence_quotes": json.loads(cached.get("evidence_json") or "{}"),
            }

    print(f"    [Gemini] Extracting 17 course fields ({harvest_data['word_count']} words)...")
    res = extractor.extract_course_fields_gemini(
        institution_name=inst_name,
        course_title=course_title,
        clean_text=harvest_data["clean_text"],
        accreditation_badges=harvest_data.get("accreditation_badges"),
    )
    usage = res.get("_usage") or {}
    if usage:
        storage.log_api_usage(
            provider="gemini",
            model_or_endpoint=usage.get("model", ""),
            course_key=course_key,
            status="ok",
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            estimated_cost_usd=usage.get("estimated_cost_usd", 0.0),
            latency_ms=usage.get("latency_ms", 0),
        )
    return res


def cmd_verify_urls(config: dict[str, Any]) -> None:
    print("\n=== Verifying Live Course URLs ===")
    for course in config["courses"]:
        res = harvester.verify_url(course["url"])
        flag = "OK  " if res["ok"] else "FAIL"
        print(f"  [{flag}] {course['institution_id']:18} | HTTP {res['status']:3} | {res['title']}")
        if res["resolved_url"] != course["url"]:
            print(f"         ↳ Redirected to: {res['resolved_url']}")


def cmd_serve(port: int = 8765) -> None:
    dash_dir = BASE_DIR / "dashboard"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(dash_dir))
    print(f"\nServing local preview dashboard at http://localhost:{port}")
    print("Press Ctrl+C to stop.")
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        httpd.serve_forever()


def main() -> None:
    load_dotenv_simple(BASE_DIR / ".env")

    parser = argparse.ArgumentParser(
        description="University of Hull HE Competitor & Web/SEO Intelligence Radar"
    )
    parser.add_argument("--verify-urls", action="store_true", help="Check that all configured URLs resolve HTTP 200")
    parser.add_argument("--only", type=str, default="", help="Comma-separated institution_ids to run (e.g. hull,lincoln)")
    parser.add_argument("--skip-llm", action="store_true", help="Skip Gemini extraction (run DOM/SEO/Talking Heads only)")
    parser.add_argument("--force-llm", action="store_true", help="Ignore content-hash cache and re-run Gemini extraction")
    parser.add_argument("--skip-seo", action="store_true", help="Skip Google PageSpeed Insights API calls")
    parser.add_argument("--skip-wayback", action="store_true", help="Skip Wayback Machine 12-month baseline fetch")
    parser.add_argument("--force-refresh", action="store_true", help="Bypass the 24h local HTML cache and re-fetch live pages")
    parser.add_argument("--export-only", action="store_true", help="Regenerate Fabric Parquet exports & dashboard JSON only")
    parser.add_argument("--serve", action="store_true", help="Serve the local HTML dashboard after running")
    parser.add_argument("--port", type=int, default=8765, help="Port for --serve (default 8765)")
    args = parser.parse_args()

    config = load_config()

    if args.verify_urls:
        cmd_verify_urls(config)
        return

    storage.init_db()
    storage.backfill_usage_if_empty()
    storage.sync_config_to_db(config)

    if not args.export_only:
        inst_map = {i["institution_id"]: i for i in config["institutions"]}
        only_set = {x.strip().lower() for x in args.only.split(",") if x.strip()}

        for course in config["courses"]:
            inst_id = course["institution_id"]
            if only_set and inst_id.lower() not in only_set:
                continue

            inst = inst_map[inst_id]
            ck = course["course_key"]
            url = course["url"]
            print(f"\n>>> [{inst['name']}] {course['course_title']}")
            print(f"    URL: {url}")

            # 1. Harvest Live Page (uses 24h local cache unless --force-refresh is passed)
            try:
                harvest_data = harvester.harvest_page_playwright(url, force_refresh=args.force_refresh)
            except Exception as exc:
                print(f"    [ERROR] Failed to harvest {url}: {exc}")
                continue

            http_status = harvest_data.get("http_status", 0)
            resolved_url = harvest_data.get("resolved_url", url)
            seo_dom = harvest_data["seo"]
            engine_label = harvest_data.get("engine", "playwright")
            if harvest_data.get("waf_blocked"):
                print(
                    f"    [WAF BLOCKED] Security/WAF block page detected on {resolved_url} "
                    f"(title='{seo_dom.get('title_tag', '')}'). Preserving previous good snapshot!"
                )
                continue

            storage.update_course_http_info(ck, resolved_url, http_status, seo_dom.get("cms_platform"))
            print(
                f"    [Harvest:{engine_label}] HTTP {http_status} | {harvest_data['word_count']} words | "
                f"Videos: {harvest_data['talking_heads_count']} | CMS: {seo_dom['cms_platform']} | "
                f"Schema: {seo_dom['schema_types'] or 'None'}"
            )

            scrape_status = "ok"
            scrape_notes = ""
            if http_status >= 400 or harvest_data["word_count"] < 150:
                scrape_status = "warning"
                scrape_notes = f"Low word count ({harvest_data['word_count']}) or HTTP {http_status}"
                print(f"    [WARNING] {scrape_notes}")

            # 2. Extract Course Audit Fields (Hash-Gated Gemini)
            prev_live = storage.get_latest_snapshot(ck, is_wayback=False)
            try:
                extracted = extract_with_hash_cache(
                    ck, inst["name"], course["course_title"], harvest_data, args.skip_llm, args.force_llm
                )
            except Exception as exc:
                print(f"    [LLM ERROR] {exc}")
                extracted = extractor.empty_extraction(f"LLM error: {str(exc)[:100]}")

            # 2b. UCAS / Search-Grounded Fallback if course marketing page omitted Year 1/2/3 modules
            m1 = (extracted.get("modules_yr1") or "").strip().lower()
            if not args.skip_llm and (not m1 or m1.startswith("not stated")):
                print("    [UCAS Fallback] Modules missing on course page — querying Gemini Search Grounding (UCAS / Programme Spec)...")
                ucas_mods = extractor.extract_ucas_modules_grounded_gemini(
                    inst["name"], course["course_title"], course.get("ucas_code", "")
                )
                u_usage = ucas_mods.get("_usage") or {}
                if u_usage:
                    storage.log_api_usage(
                        provider="gemini",
                        model_or_endpoint=f"{u_usage.get('model', '')} (ucas-grounded)",
                        course_key=ck,
                        status="ok",
                        input_tokens=u_usage.get("input_tokens", 0),
                        output_tokens=u_usage.get("output_tokens", 0),
                        total_tokens=u_usage.get("total_tokens", 0),
                        estimated_cost_usd=u_usage.get("estimated_cost_usd", 0.0),
                        latency_ms=u_usage.get("latency_ms", 0),
                    )
                if ucas_mods.get("modules_yr1") and not ucas_mods["modules_yr1"].lower().startswith("not stated"):
                    extracted["modules_yr1"] = ucas_mods["modules_yr1"]
                    extracted["modules_yr2"] = ucas_mods.get("modules_yr2") or extracted.get("modules_yr2")
                    extracted["modules_yr3"] = ucas_mods.get("modules_yr3") or extracted.get("modules_yr3")
                    if (extracted.get("assessments") or "").lower().startswith("not stated") and ucas_mods.get("assessments"):
                        extracted["assessments"] = ucas_mods["assessments"]
                    extracted["module_source"] = "ucas_fallback"
                    ev = extracted.get("evidence_quotes") or {}
                    ev["ucas_fallback_url"] = ucas_mods.get("source_url", "")
                    ev["ucas_fallback_label"] = ucas_mods.get("source_label", "UCAS")
                    extracted["evidence_quotes"] = ev
                    print(f"    [UCAS Fallback] Populated & flagged as 'ucas_fallback' ({ev['ucas_fallback_label']})!")
                elif course.get("ucas_fallback_modules"):
                    cfg_fb = course["ucas_fallback_modules"]
                    extracted["modules_yr1"] = cfg_fb.get("modules_yr1") or extracted.get("modules_yr1")
                    extracted["modules_yr2"] = cfg_fb.get("modules_yr2") or extracted.get("modules_yr2")
                    extracted["modules_yr3"] = cfg_fb.get("modules_yr3") or extracted.get("modules_yr3")
                    extracted["module_source"] = "ucas_fallback"
                    ev = extracted.get("evidence_quotes") or {}
                    ev["ucas_fallback_label"] = cfg_fb.get("source_label", "UCAS")
                    extracted["evidence_quotes"] = ev
                    print(f"    [UCAS Fallback] Loaded verified UCAS modules from config.json & flagged as 'ucas_fallback' ({ev['ucas_fallback_label']})!")
                else:
                    print("    [UCAS Fallback] No verified module list returned.")
                    extracted["module_source"] = "not_found"
            else:
                extracted["module_source"] = extracted.get("module_source") or "course_page"

            live_snap = build_snapshot_record(
                ck, resolved_url, harvest_data, extracted,
                is_wayback=False, scrape_status=scrape_status, scrape_notes=scrape_notes,
            )
            live_id = storage.save_snapshot(live_snap)

            # 3. Live Diff vs Previous Run
            if prev_live and prev_live.get("content_hash") != live_snap["content_hash"]:
                diffs = storage.compute_and_save_diffs(
                    ck, prev_live, live_snap, prev_live["snapshot_id"], live_id, comparison_type="live_diff"
                )
                print(f"    [Diff] {len(diffs)} field change(s) detected since previous run.")

            # 4. Optional Wayback Machine 12-Month Baseline (only if not already captured)
            if not args.skip_wayback:
                existing_wb = storage.get_latest_snapshot(ck, is_wayback=True)
                if not existing_wb:
                    print("    [Wayback] Looking up ~12-month-old archive snapshot...")
                    wb_harvest = harvester.fetch_wayback_baseline(resolved_url, months_ago=12)
                    if wb_harvest and wb_harvest["word_count"] >= 150:
                        wb_ts = wb_harvest["wayback_timestamp"]
                        print(f"    [Wayback] Found snapshot from {wb_ts[:4]}-{wb_ts[4:6]}-{wb_ts[6:8]} ({wb_harvest['word_count']} words)")
                        try:
                            wb_extracted = extract_with_hash_cache(
                                ck, inst["name"], course["course_title"], wb_harvest, args.skip_llm, args.force_llm
                            )
                            wb_snap = build_snapshot_record(
                                ck, wb_harvest["archive_url"], wb_harvest, wb_extracted,
                                is_wayback=True, wayback_ts=wb_ts,
                            )
                            wb_id = storage.save_snapshot(wb_snap)
                            wb_diffs = storage.compute_and_save_diffs(
                                ck, wb_snap, live_snap, wb_id, live_id, comparison_type="wayback_12m"
                            )
                            print(f"    [Wayback 12m Diff] {len(wb_diffs)} change(s) vs. 12 months ago.")
                        except Exception as exc:
                            print(f"    [Wayback LLM Error] {exc}")
                    else:
                        print("    [Wayback] No usable 12-month snapshot found.")

            # 5. Web, Accessibility & SEO Benchmarking (Mobile + Desktop)
            for device in ("mobile", "desktop"):
                if args.skip_seo:
                    psi = {
                        "perf_score": None, "accessibility_score": None,
                        "best_practices_score": None, "seo_score": None,
                        "lcp_ms": None, "cls": None, "inp_ms": None,
                        "psi_status": "skipped",
                    }
                else:
                    print(f"    [PageSpeed] Auditing {device}...")
                    psi = seo_auditor.run_pagespeed_audit(resolved_url, strategy=device)
                    storage.log_api_usage(
                        provider="pagespeed",
                        model_or_endpoint=f"psi-{device}",
                        course_key=ck,
                        status=psi.get("psi_status", "ok"),
                        latency_ms=psi.get("latency_ms", 0),
                    )
                    print(
                        f"      ↳ {device}: Perf={psi.get('perf_score')} | "
                        f"A11y={psi.get('accessibility_score')} | "
                        f"SEO={psi.get('seo_score')} | status={psi.get('psi_status')}"
                    )
                    time.sleep(1.0)

                metric_row = {
                    "audit_date": storage.utc_now_iso(),
                    "course_key": ck,
                    "url": resolved_url,
                    "device": device,
                    "perf_score": psi.get("perf_score"),
                    "accessibility_score": psi.get("accessibility_score"),
                    "best_practices_score": psi.get("best_practices_score"),
                    "seo_score": psi.get("seo_score"),
                    "lcp_ms": psi.get("lcp_ms"),
                    "cls": psi.get("cls"),
                    "inp_ms": psi.get("inp_ms"),
                    **seo_dom,
                    "psi_status": psi.get("psi_status", "ok"),
                }
                storage.save_web_seo_metric(metric_row)

            # Polite pause between universities
            time.sleep(1.5)

    # 6. Export MS Fabric Parquet Star Schema + Local Dashboard JSON
    print("\n=== Exporting MS Fabric Star Schema & Local Dashboard ===")
    fabric_summary = storage.export_fabric_star_schema()
    print(f"  Format: {fabric_summary['format'].upper()} → {fabric_summary['export_dir']}")
    for t in fabric_summary["tables"]:
        print(f"    - {t['table']:28} | {t['rows']:4} rows | {t['size_bytes']:,} bytes")

    dash_json = storage.export_dashboard_json(fabric_summary=fabric_summary)
    print(f"  Dashboard data updated: {dash_json}")

    if args.serve:
        cmd_serve(args.port)


if __name__ == "__main__":
    main()
