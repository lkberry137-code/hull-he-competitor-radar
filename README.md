# University of Hull — HE Competitor & Web/SEO Intelligence Radar

Lightweight local Python intelligence engine benchmarking **University of Hull** against competitor cohorts (`Local`, `Tariff Peers`, `League Table Peers`, `Aspirational / Russell Group`) across all 17 course-audit categories plus technical **Web, Accessibility & SEO** performance — outputting native **Microsoft Fabric Parquet star-schema tables** and a local preview dashboard.

## 1. Quick Setup

1. Copy `.env.example` to `.env` and add your Gemini API key:
   ```env
   GEMINI_API_KEY=your_key_here
   GEMINI_MODEL=gemini-2.5-flash
   ```
   *(Optional)* Set `FABRIC_EXPORT_DIR` to a local **OneLake File Explorer** folder to auto-sync Parquet tables directly into your Microsoft Fabric Lakehouse `Files/` directory.

2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   playwright install chromium
   ```

## 2. Running the Pilot (Business Management)

* **Verify all 7 live course URLs first:**
  ```powershell
  .\run.ps1 --verify-urls
  ```
* **Run the full audit (Harvest + Gemini + Wayback 12m Diff + PageSpeed Mobile/Desktop + Fabric Parquet Export) and launch the preview dashboard:**
  ```powershell
  .\run.ps1 --serve
  ```
* **Fast test run on 2 universities (no PageSpeed/Wayback):**
  ```powershell
  .\run.ps1 --only hull,lincoln --skip-seo --skip-wayback --serve
  ```

## 3. Loading into Microsoft Fabric

Every run writes 6 strongly-typed `.parquet` star-schema files to `exports/fabric/` (or your custom `FABRIC_EXPORT_DIR`):
* `dim_institutions.parquet`
* `bridge_institution_cohorts.parquet`
* `dim_courses.parquet`
* `fact_course_snapshots.parquet`
* `fact_course_changes.parquet`
* `fact_web_seo_metrics.parquet`

In Fabric Lakehouse: sync via **OneLake File Explorer** into `Files/he_competitor_radar/`, right-click each file → **Load to Tables**, and build a Power BI Direct Lake semantic model (joining `bridge_institution_cohorts` for multi-cohort slicing).
