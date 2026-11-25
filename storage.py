"""
SQLite storage, longitudinal diff engine, and MS Fabric Parquet Star-Schema exporter.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "hull_intel.db"
DEFAULT_FABRIC_DIR = BASE_DIR / "exports" / "fabric"
DASHBOARD_DATA_PATH = BASE_DIR / "dashboard" / "data.json"

TRACKED_DIFF_FIELDS = [
    "entry_requirements",
    "ucas_tariff_min",
    "ucas_tariff_max",
    "ielts_overall",
    "ielts_min_component",
    "ib_points",
    "english_alt_tests",
    "intl_entry_summary",
    "fees_home",
    "fee_home_gbp",
    "fees_international",
    "fee_intl_gbp",
    "international_opportunities",
    "modules_yr1",
    "modules_yr2",
    "modules_yr3",
    "assessments",
    "usps",
    "equipment_facilities",
    "authentic_learning",
    "employability_outcomes",
    "accreditations",
    "subject_ranking_claimed",
    "talking_heads_count",
    "talking_heads_summary",
    "other_programmes",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dim_institutions (
                institution_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                is_hull INTEGER NOT NULL DEFAULT 0,
                tef_rating TEXT,
                times_rank INTEGER,
                guardian_rank INTEGER,
                cug_rank INTEGER,
                subject_rank_times INTEGER,
                subject_rank_guardian INTEGER,
                subject_rank_cug INTEGER,
                cms_platform TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bridge_institution_cohorts (
                institution_id TEXT NOT NULL,
                cohort_name TEXT NOT NULL,
                PRIMARY KEY (institution_id, cohort_name),
                FOREIGN KEY (institution_id) REFERENCES dim_institutions(institution_id)
            );

            CREATE TABLE IF NOT EXISTS dim_courses (
                course_key TEXT PRIMARY KEY,
                institution_id TEXT NOT NULL,
                subject_area TEXT NOT NULL,
                course_title TEXT NOT NULL,
                degree_type TEXT,
                ucas_code TEXT,
                url TEXT NOT NULL,
                resolved_url TEXT,
                http_status INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (institution_id) REFERENCES dim_institutions(institution_id)
            );

            CREATE TABLE IF NOT EXISTS fact_course_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                course_key TEXT NOT NULL,
                source_url TEXT NOT NULL,
                is_wayback_baseline INTEGER NOT NULL DEFAULT 0,
                wayback_timestamp TEXT,
                content_hash TEXT NOT NULL,
                word_count INTEGER,
                entry_requirements TEXT,
                ucas_tariff_min INTEGER,
                ucas_tariff_max INTEGER,
                ielts_overall REAL,
                ielts_min_component REAL,
                ib_points INTEGER,
                english_alt_tests TEXT,
                intl_foundation_pathway TEXT,
                intl_entry_summary TEXT,
                fees_home TEXT,
                fee_home_gbp INTEGER,
                fees_international TEXT,
                fee_intl_gbp INTEGER,
                international_opportunities TEXT,
                modules_yr1 TEXT,
                modules_yr2 TEXT,
                modules_yr3 TEXT,
                assessments TEXT,
                usps TEXT,
                equipment_facilities TEXT,
                authentic_learning TEXT,
                employability_outcomes TEXT,
                accreditations TEXT,
                subject_ranking_claimed TEXT,
                talking_heads_count INTEGER DEFAULT 0,
                talking_heads_summary TEXT,
                other_programmes TEXT,
                module_source TEXT DEFAULT 'course_page',
                evidence_json TEXT,
                llm_model TEXT,
                scrape_status TEXT NOT NULL DEFAULT 'ok',
                scrape_notes TEXT,
                FOREIGN KEY (course_key) REFERENCES dim_courses(course_key)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_course_date
                ON fact_course_snapshots (course_key, snapshot_date DESC);

            CREATE INDEX IF NOT EXISTS idx_snapshots_hash
                ON fact_course_snapshots (course_key, content_hash);

            CREATE TABLE IF NOT EXISTS fact_course_changes (
                change_id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_date TEXT NOT NULL,
                course_key TEXT NOT NULL,
                from_snapshot_id INTEGER,
                to_snapshot_id INTEGER,
                comparison_type TEXT NOT NULL DEFAULT 'live_diff',
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                change_type TEXT NOT NULL,
                FOREIGN KEY (course_key) REFERENCES dim_courses(course_key)
            );

            CREATE TABLE IF NOT EXISTS fact_web_seo_metrics (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_date TEXT NOT NULL,
                course_key TEXT NOT NULL,
                url TEXT NOT NULL,
                device TEXT NOT NULL,
                perf_score INTEGER,
                accessibility_score INTEGER,
                best_practices_score INTEGER,
                seo_score INTEGER,
                lcp_ms REAL,
                cls REAL,
                inp_ms REAL,
                title_tag TEXT,
                title_len INTEGER,
                meta_description TEXT,
                meta_desc_len INTEGER,
                h1_count INTEGER,
                h2_count INTEGER,
                word_count INTEGER,
                canonical_url TEXT,
                missing_alt_count INTEGER,
                total_images INTEGER,
                schema_types TEXT,
                has_course_schema INTEGER DEFAULT 0,
                has_faq_schema INTEGER DEFAULT 0,
                has_video_schema INTEGER DEFAULT 0,
                chat_widget TEXT,
                sticky_cta_detected INTEGER DEFAULT 0,
                cms_platform TEXT,
                martech_pixels TEXT,
                psi_status TEXT DEFAULT 'ok',
                FOREIGN KEY (course_key) REFERENCES dim_courses(course_key)
            );

            CREATE INDEX IF NOT EXISTS idx_seo_course_date
                ON fact_web_seo_metrics (course_key, audit_date DESC);

            CREATE TABLE IF NOT EXISTS fact_api_usage (
                usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                called_at TEXT NOT NULL,
                provider TEXT NOT NULL,
                model_or_endpoint TEXT,
                course_key TEXT,
                status TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                estimated_cost_usd REAL DEFAULT 0.0,
                latency_ms INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_api_usage_date
                ON fact_api_usage (called_at DESC);
            """
        )
        # Lightweight schema migration for existing SQLite databases
        cols = {c[1] for c in conn.execute("PRAGMA table_info(fact_course_snapshots)").fetchall()}
        for col_name, col_def in (
            ("module_source", "TEXT DEFAULT 'course_page'"),
            ("fees_home", "TEXT"),
            ("fee_home_gbp", "INTEGER"),
            ("fees_international", "TEXT"),
            ("fee_intl_gbp", "INTEGER"),
            ("ielts_overall", "REAL"),
            ("ielts_min_component", "REAL"),
            ("ib_points", "INTEGER"),
            ("english_alt_tests", "TEXT"),
            ("intl_foundation_pathway", "TEXT"),
            ("intl_entry_summary", "TEXT"),
        ):
            if col_name not in cols:
                conn.execute(f"ALTER TABLE fact_course_snapshots ADD COLUMN {col_name} {col_def}")
        conn.commit()
    finally:
        conn.close()


def sync_config_to_db(config: dict[str, Any], db_path: Path = DB_PATH) -> None:
    conn = get_conn(db_path)
    now = utc_now_iso()
    try:
        for inst in config.get("institutions", []):
            conn.execute(
                """
                INSERT INTO dim_institutions (
                    institution_id, name, is_hull, tef_rating,
                    times_rank, guardian_rank, cug_rank,
                    subject_rank_times, subject_rank_guardian, subject_rank_cug,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(institution_id) DO UPDATE SET
                    name = excluded.name,
                    is_hull = excluded.is_hull,
                    tef_rating = excluded.tef_rating,
                    times_rank = excluded.times_rank,
                    guardian_rank = excluded.guardian_rank,
                    cug_rank = excluded.cug_rank,
                    subject_rank_times = excluded.subject_rank_times,
                    subject_rank_guardian = excluded.subject_rank_guardian,
                    subject_rank_cug = excluded.subject_rank_cug,
                    updated_at = excluded.updated_at
                """,
                (
                    inst["institution_id"],
                    inst["name"],
                    1 if inst.get("is_hull") else 0,
                    inst.get("tef_rating"),
                    inst.get("times_rank"),
                    inst.get("guardian_rank"),
                    inst.get("cug_rank"),
                    inst.get("subject_rank_times"),
                    inst.get("subject_rank_guardian"),
                    inst.get("subject_rank_cug"),
                    now,
                ),
            )
            conn.execute(
                "DELETE FROM bridge_institution_cohorts WHERE institution_id = ?",
                (inst["institution_id"],),
            )
            for cohort in inst.get("cohorts", []):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO bridge_institution_cohorts (institution_id, cohort_name)
                    VALUES (?, ?)
                    """,
                    (inst["institution_id"], cohort),
                )

        for course in config.get("courses", []):
            conn.execute(
                """
                INSERT INTO dim_courses (
                    course_key, institution_id, subject_area, course_title,
                    degree_type, ucas_code, url, is_active, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(course_key) DO UPDATE SET
                    institution_id = excluded.institution_id,
                    subject_area = excluded.subject_area,
                    course_title = excluded.course_title,
                    degree_type = excluded.degree_type,
                    ucas_code = excluded.ucas_code,
                    url = excluded.url,
                    updated_at = excluded.updated_at
                """,
                (
                    course["course_key"],
                    course["institution_id"],
                    course["subject_area"],
                    course["course_title"],
                    course.get("degree_type"),
                    course.get("ucas_code"),
                    course["url"],
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def update_course_http_info(
    course_key: str, resolved_url: str, http_status: int, cms_platform: str | None = None, db_path: Path = DB_PATH
) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE dim_courses SET resolved_url = ?, http_status = ?, updated_at = ? WHERE course_key = ?",
            (resolved_url, http_status, utc_now_iso(), course_key),
        )
        if cms_platform:
            conn.execute(
                """
                UPDATE dim_institutions
                SET cms_platform = ?
                WHERE institution_id = (SELECT institution_id FROM dim_courses WHERE course_key = ?)
                """,
                (cms_platform, course_key),
            )
        conn.commit()
    finally:
        conn.close()


def get_cached_extraction_by_hash(
    course_key: str, content_hash: str, db_path: Path = DB_PATH
) -> dict[str, Any] | None:
    """Return a prior LLM-populated snapshot if this exact page hash was already extracted."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM fact_course_snapshots
            WHERE course_key = ? AND content_hash = ? AND llm_model IS NOT NULL AND llm_model != ''
            ORDER BY snapshot_id DESC LIMIT 1
            """,
            (course_key, content_hash),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_snapshot(
    course_key: str, is_wayback: bool = False, db_path: Path = DB_PATH
) -> dict[str, Any] | None:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM fact_course_snapshots
            WHERE course_key = ? AND is_wayback_baseline = ?
            ORDER BY snapshot_id DESC LIMIT 1
            """,
            (course_key, 1 if is_wayback else 0),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_snapshot(snapshot: dict[str, Any], db_path: Path = DB_PATH) -> int:
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO fact_course_snapshots (
                snapshot_date, course_key, source_url, is_wayback_baseline, wayback_timestamp,
                content_hash, word_count,
                entry_requirements, ucas_tariff_min, ucas_tariff_max,
                ielts_overall, ielts_min_component, ib_points,
                english_alt_tests, intl_foundation_pathway, intl_entry_summary,
                fees_home, fee_home_gbp, fees_international, fee_intl_gbp,
                international_opportunities, modules_yr1, modules_yr2, modules_yr3,
                assessments, usps, equipment_facilities, authentic_learning,
                employability_outcomes, accreditations, subject_ranking_claimed,
                talking_heads_count, talking_heads_summary, other_programmes, module_source,
                evidence_json, llm_model, scrape_status, scrape_notes
            ) VALUES (
                :snapshot_date, :course_key, :source_url, :is_wayback_baseline, :wayback_timestamp,
                :content_hash, :word_count,
                :entry_requirements, :ucas_tariff_min, :ucas_tariff_max,
                :ielts_overall, :ielts_min_component, :ib_points,
                :english_alt_tests, :intl_foundation_pathway, :intl_entry_summary,
                :fees_home, :fee_home_gbp, :fees_international, :fee_intl_gbp,
                :international_opportunities, :modules_yr1, :modules_yr2, :modules_yr3,
                :assessments, :usps, :equipment_facilities, :authentic_learning,
                :employability_outcomes, :accreditations, :subject_ranking_claimed,
                :talking_heads_count, :talking_heads_summary, :other_programmes, :module_source,
                :evidence_json, :llm_model, :scrape_status, :scrape_notes
            )
            """,
            snapshot,
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _norm_val(val: Any) -> str:
    if val is None:
        return ""
    return " ".join(str(val).strip().split())


def compute_and_save_diffs(
    course_key: str,
    old_snap: dict[str, Any],
    new_snap: dict[str, Any],
    old_id: int,
    new_id: int,
    comparison_type: str = "live_diff",
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Compare tracked fields between two snapshots and record deltas in fact_course_changes."""
    changes: list[dict[str, Any]] = []
    now = utc_now_iso()

    for field in TRACKED_DIFF_FIELDS:
        old_v = _norm_val(old_snap.get(field))
        new_v = _norm_val(new_snap.get(field))
        if old_v == new_v:
            continue
        if not old_v and new_v:
            ctype = "added"
        elif old_v and not new_v:
            ctype = "removed"
        else:
            ctype = "modified"
        changes.append(
            {
                "detected_date": now,
                "course_key": course_key,
                "from_snapshot_id": old_id,
                "to_snapshot_id": new_id,
                "comparison_type": comparison_type,
                "field_name": field,
                "old_value": old_v,
                "new_value": new_v,
                "change_type": ctype,
            }
        )

    if changes:
        conn = get_conn(db_path)
        try:
            # Avoid duplicating identical wayback_12m diffs on re-runs
            if comparison_type == "wayback_12m":
                conn.execute(
                    "DELETE FROM fact_course_changes WHERE course_key = ? AND comparison_type = 'wayback_12m'",
                    (course_key,),
                )
            conn.executemany(
                """
                INSERT INTO fact_course_changes (
                    detected_date, course_key, from_snapshot_id, to_snapshot_id,
                    comparison_type, field_name, old_value, new_value, change_type
                ) VALUES (
                    :detected_date, :course_key, :from_snapshot_id, :to_snapshot_id,
                    :comparison_type, :field_name, :old_value, :new_value, :change_type
                )
                """,
                changes,
            )
            conn.commit()
        finally:
            conn.close()

    return changes


def log_api_usage(
    provider: str,
    model_or_endpoint: str,
    course_key: str,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    latency_ms: int = 0,
    db_path: Path = DB_PATH,
) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO fact_api_usage (
                called_at, provider, model_or_endpoint, course_key, status,
                input_tokens, output_tokens, total_tokens, estimated_cost_usd, latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                provider,
                model_or_endpoint,
                course_key,
                status,
                int(input_tokens or 0),
                int(output_tokens or 0),
                int(total_tokens or (int(input_tokens or 0) + int(output_tokens or 0))),
                float(estimated_cost_usd or 0.0),
                int(latency_ms or 0),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def backfill_usage_if_empty(db_path: Path = DB_PATH) -> None:
    """Seed fact_api_usage from existing snapshots/SEO rows if this is a fresh upgrade."""
    conn = get_conn(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM fact_api_usage").fetchone()[0]
        if count > 0:
            return
        snaps = conn.execute(
            "SELECT snapshot_date, course_key, llm_model, word_count FROM fact_course_snapshots WHERE llm_model IS NOT NULL AND llm_model != ''"
        ).fetchall()
        for s in snaps:
            est_in = int((s["word_count"] or 2500) * 1.35) + 450
            est_out = 650
            est_cost = (est_in / 1_000_000) * 0.15 + (est_out / 1_000_000) * 0.60
            conn.execute(
                """
                INSERT INTO fact_api_usage (
                    called_at, provider, model_or_endpoint, course_key, status,
                    input_tokens, output_tokens, total_tokens, estimated_cost_usd, latency_ms
                ) VALUES (?, 'gemini', ?, ?, 'ok_backfilled', ?, ?, ?, ?, 0)
                """,
                (s["snapshot_date"], s["llm_model"], s["course_key"], est_in, est_out, est_in + est_out, round(est_cost, 6)),
            )
        seos = conn.execute(
            "SELECT audit_date, course_key, device, psi_status FROM fact_web_seo_metrics WHERE psi_status != 'skipped'"
        ).fetchall()
        for m in seos:
            conn.execute(
                """
                INSERT INTO fact_api_usage (
                    called_at, provider, model_or_endpoint, course_key, status,
                    input_tokens, output_tokens, total_tokens, estimated_cost_usd, latency_ms
                ) VALUES (?, 'pagespeed', ?, ?, ?, 0, 0, 0, 0.0, 0)
                """,
                (m["audit_date"], f"psi-{m['device']}", m["course_key"], m["psi_status"]),
            )
        conn.commit()
    finally:
        conn.close()


def save_web_seo_metric(metric: dict[str, Any], db_path: Path = DB_PATH) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO fact_web_seo_metrics (
                audit_date, course_key, url, device,
                perf_score, accessibility_score, best_practices_score, seo_score,
                lcp_ms, cls, inp_ms,
                title_tag, title_len, meta_description, meta_desc_len,
                h1_count, h2_count, word_count, canonical_url,
                missing_alt_count, total_images, schema_types,
                has_course_schema, has_faq_schema, has_video_schema,
                chat_widget, sticky_cta_detected, cms_platform, martech_pixels, psi_status
            ) VALUES (
                :audit_date, :course_key, :url, :device,
                :perf_score, :accessibility_score, :best_practices_score, :seo_score,
                :lcp_ms, :cls, :inp_ms,
                :title_tag, :title_len, :meta_description, :meta_desc_len,
                :h1_count, :h2_count, :word_count, :canonical_url,
                :missing_alt_count, :total_images, :schema_types,
                :has_course_schema, :has_faq_schema, :has_video_schema,
                :chat_widget, :sticky_cta_detected, :cms_platform, :martech_pixels, :psi_status
            )
            """,
            metric,
        )
        conn.commit()
    finally:
        conn.close()


def export_fabric_star_schema(
    export_dir: Path | None = None, db_path: Path = DB_PATH
) -> dict[str, Any]:
    """
    Export all 6 star-schema tables to Apache Parquet (.parquet) for MS Fabric Lakehouse
    (plus CSV fallback if pyarrow is unavailable).
    """
    custom_env_dir = os.getenv("FABRIC_EXPORT_DIR", "").strip()
    out_dir = Path(custom_env_dir) if (export_dir is None and custom_env_dir) else (export_dir or DEFAULT_FABRIC_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    tables = [
        "dim_institutions",
        "bridge_institution_cohorts",
        "dim_courses",
        "fact_course_snapshots",
        "fact_course_changes",
        "fact_web_seo_metrics",
        "fact_api_usage",
    ]

    try:
        import pandas as pd  # type: ignore
        import pyarrow  # noqa: F401  # type: ignore
        has_parquet = True
    except ImportError:
        has_parquet = False

    conn = get_conn(db_path)
    summary: dict[str, Any] = {
        "exported_at": utc_now_iso(),
        "export_dir": str(out_dir),
        "format": "parquet" if has_parquet else "csv",
        "tables": [],
    }

    try:
        for table in tables:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
            row_count = len(rows)
            if has_parquet:
                import pandas as pd  # type: ignore

                if rows:
                    df = pd.DataFrame(rows)
                else:
                    cols = [
                        c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()
                    ]
                    df = pd.DataFrame(columns=cols)
                parquet_path = out_dir / f"{table}.parquet"
                df.to_parquet(parquet_path, index=False, engine="pyarrow")
                file_path = str(parquet_path)
                size_bytes = parquet_path.stat().st_size
            else:
                cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                csv_path = out_dir / f"{table}.csv"
                with csv_path.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=cols)
                    writer.writeheader()
                    writer.writerows(rows)
                file_path = str(csv_path)
                size_bytes = csv_path.stat().st_size

            summary["tables"].append(
                {
                    "table": table,
                    "rows": row_count,
                    "file": file_path,
                    "size_bytes": size_bytes,
                }
            )
    finally:
        conn.close()

    return summary


def export_dashboard_json(
    fabric_summary: dict[str, Any] | None = None,
    output_path: Path = DASHBOARD_DATA_PATH,
    db_path: Path = DB_PATH,
) -> Path:
    """Build a compact JSON payload for the local HTML preview dashboard."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)
    try:
        institutions = [dict(r) for r in conn.execute("SELECT * FROM dim_institutions").fetchall()]
        cohorts_rows = [dict(r) for r in conn.execute("SELECT * FROM bridge_institution_cohorts").fetchall()]
        cohort_map: dict[str, list[str]] = {}
        for cr in cohorts_rows:
            cohort_map.setdefault(cr["institution_id"], []).append(cr["cohort_name"])
        for inst in institutions:
            inst["cohorts"] = cohort_map.get(inst["institution_id"], [])

        courses = [dict(r) for r in conn.execute("SELECT * FROM dim_courses WHERE is_active = 1").fetchall()]

        # Latest live snapshot per course
        latest_snapshots: dict[str, dict[str, Any]] = {}
        for c in courses:
            row = conn.execute(
                """
                SELECT * FROM fact_course_snapshots
                WHERE course_key = ? AND is_wayback_baseline = 0
                ORDER BY snapshot_id DESC LIMIT 1
                """,
                (c["course_key"],),
            ).fetchone()
            if row:
                snap = dict(row)
                if snap.get("evidence_json"):
                    try:
                        snap["evidence"] = json.loads(snap["evidence_json"])
                    except json.JSONDecodeError:
                        snap["evidence"] = {}
                else:
                    snap["evidence"] = {}
                latest_snapshots[c["course_key"]] = snap

        changes = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fact_course_changes ORDER BY change_id DESC LIMIT 250"
            ).fetchall()
        ]

        # Latest mobile + desktop SEO metrics per course
        latest_seo: dict[str, dict[str, dict[str, Any]]] = {}
        for c in courses:
            ck = c["course_key"]
            latest_seo[ck] = {}
            for dev in ("mobile", "desktop"):
                row = conn.execute(
                    """
                    SELECT * FROM fact_web_seo_metrics
                    WHERE course_key = ? AND device = ?
                    ORDER BY audit_id DESC LIMIT 1
                    """,
                    (ck, dev),
                ).fetchone()
                if row:
                    latest_seo[ck][dev] = dict(row)

        # API Usage summary & recent log
        today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fact_api_usage ORDER BY usage_id DESC LIMIT 200"
            ).fetchall()
        ]
        all_usage = [dict(r) for r in conn.execute("SELECT * FROM fact_api_usage").fetchall()]
        gemini_calls = [u for u in all_usage if u["provider"] == "gemini" and u["status"] != "cache_hit"]
        gemini_cache_hits = [u for u in all_usage if u["provider"] == "gemini" and u["status"] == "cache_hit"]
        psi_calls = [u for u in all_usage if u["provider"] == "pagespeed"]
        psi_today = [u for u in psi_calls if (u["called_at"] or "").startswith(today_prefix)]
        gemini_today = [u for u in gemini_calls if (u["called_at"] or "").startswith(today_prefix)]

        total_in = sum(int(u["input_tokens"] or 0) for u in gemini_calls)
        total_out = sum(int(u["output_tokens"] or 0) for u in gemini_calls)
        total_cost_usd = round(sum(float(u["estimated_cost_usd"] or 0.0) for u in gemini_calls), 5)

        api_usage_summary = {
            "gemini_total_calls": len(gemini_calls),
            "gemini_calls_today": len(gemini_today),
            "gemini_cache_hits": len(gemini_cache_hits),
            "gemini_input_tokens": total_in,
            "gemini_output_tokens": total_out,
            "gemini_total_tokens": total_in + total_out,
            "estimated_cost_usd": total_cost_usd,
            "estimated_cost_gbp": round(total_cost_usd * 0.79, 5),
            "pagespeed_total_calls": len(psi_calls),
            "pagespeed_calls_today": len(psi_today),
            "pagespeed_daily_quota": 25000,
            "recent_calls": usage_rows[:60],
        }

        payload = {
            "generated_at": utc_now_iso(),
            "institutions": institutions,
            "courses": courses,
            "latest_snapshots": latest_snapshots,
            "changes": changes,
            "latest_seo": latest_seo,
            "fabric_export": fabric_summary or {},
            "api_usage": api_usage_summary,
        }
        json_text = json.dumps(payload, indent=2, ensure_ascii=False)
        output_path.write_text(json_text, encoding="utf-8")
        # Also mirror into docs/ for GitHub Pages publishing
        docs_dir = BASE_DIR / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "data.json").write_text(json_text, encoding="utf-8")
        dash_html = BASE_DIR / "dashboard" / "index.html"
        if dash_html.exists():
            (docs_dir / "index.html").write_text(dash_html.read_text(encoding="utf-8"), encoding="utf-8")
        return output_path
    finally:
        conn.close()
