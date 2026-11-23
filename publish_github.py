"""
Publishes the University of Hull HE Competitor Radar (code, docs/ GitHub Pages
dashboard, and MS Fabric star-schema exports) to GitHub using `gh` CLI auth.
Strictly excludes .env, cache/, hull_intel.db, and tunnel_url.txt.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
REPO_NAME = "hull-he-competitor-radar"
GH_CANDIDATES = [
    r"C:\Program Files\GitHub CLI\gh.exe",
    shutil.which("gh") or "",
]

PUBLISH_FILES = [
    "README.md",
    ".gitignore",
    ".env.example",
    "requirements.txt",
    "config.json",
    "run_audit.py",
    "harvester.py",
    "extractor.py",
    "seo_auditor.py",
    "storage.py",
    "tunnel.py",
    "publish_github.py",
    "run.ps1",
    "share.ps1",
    "publish.ps1",
    "dashboard/index.html",
    "docs/index.html",
    "docs/data.json",
]


def find_gh() -> str:
    for c in GH_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise RuntimeError("GitHub CLI (gh.exe) not found.")


def get_gh_token_and_user(gh_exe: str) -> tuple[str, str]:
    token = subprocess.check_output([gh_exe, "auth", "token"], text=True).strip()
    r = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    r.raise_for_status()
    return token, r.json()["login"]


def ensure_repo(token: str, owner: str, repo: str) -> None:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    check = requests.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=15)
    if check.status_code == 200:
        print(f"Repository https://github.com/{owner}/{repo} already exists.")
        return
    print(f"Creating public repository https://github.com/{owner}/{repo} ...")
    resp = requests.post(
        "https://api.github.com/user/repos",
        headers=headers,
        json={
            "name": repo,
            "description": "University of Hull — HE Competitor, Web/SEO & MS Fabric Intelligence Radar",
            "private": False,
            "auto_init": True,
        },
        timeout=20,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create repo: HTTP {resp.status_code} {resp.text[:300]}")


def push_commit_via_api(token: str, owner: str, repo: str, files: list[Path], message: str) -> None:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{owner}/{repo}"

    # Get current main branch HEAD
    ref_r = requests.get(f"{api}/git/ref/heads/main", headers=headers, timeout=15)
    ref_r.raise_for_status()
    parent_sha = ref_r.json()["object"]["sha"]
    commit_r = requests.get(f"{api}/git/commits/{parent_sha}", headers=headers, timeout=15)
    commit_r.raise_for_status()
    base_tree_sha = commit_r.json()["tree"]["sha"]

    tree_items = []
    for fpath in files:
        rel = fpath.relative_to(BASE_DIR).as_posix()
        # Hard safety check: never publish secrets
        if rel in (".env", "tunnel_url.txt") or rel.startswith("cache/"):
            continue
        raw_bytes = fpath.read_bytes()
        blob_r = requests.post(
            f"{api}/git/blobs",
            headers=headers,
            json={"content": base64.b64encode(raw_bytes).decode("ascii"), "encoding": "base64"},
            timeout=30,
        )
        blob_r.raise_for_status()
        tree_items.append(
            {"path": rel, "mode": "100644", "type": "blob", "sha": blob_r.json()["sha"]}
        )
        print(f"  + Staged {rel} ({len(raw_bytes):,} bytes)")

    tree_r = requests.post(
        f"{api}/git/trees",
        headers=headers,
        json={"base_tree": base_tree_sha, "tree": tree_items},
        timeout=30,
    )
    tree_r.raise_for_status()

    new_commit_r = requests.post(
        f"{api}/git/commits",
        headers=headers,
        json={"message": message, "tree": tree_r.json()["sha"], "parents": [parent_sha]},
        timeout=20,
    )
    new_commit_r.raise_for_status()

    upd_r = requests.patch(
        f"{api}/git/refs/heads/main",
        headers=headers,
        json={"sha": new_commit_r.json()["sha"]},
        timeout=20,
    )
    upd_r.raise_for_status()
    print(f"Committed {len(tree_items)} files to {owner}/{repo}@main.")


def enable_github_pages(token: str, owner: str, repo: str) -> str:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{owner}/{repo}/pages"
    r = requests.get(api, headers=headers, timeout=15)
    if r.status_code == 200:
        return r.json().get("html_url", f"https://{owner}.github.io/{repo}/")
    resp = requests.post(
        api,
        headers=headers,
        json={"source": {"branch": "main", "path": "/docs"}},
        timeout=20,
    )
    if resp.status_code in (200, 201):
        return resp.json().get("html_url", f"https://{owner}.github.io/{repo}/")
    print(f"Note: GitHub Pages API returned HTTP {resp.status_code}: {resp.text[:200]}")
    return f"https://{owner}.github.io/{repo}/"


def main() -> None:
    gh_exe = find_gh()
    token, owner = get_gh_token_and_user(gh_exe)

    # Ensure docs/ is synced with latest dashboard/
    docs_dir = BASE_DIR / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("index.html", "data.json"):
        src = BASE_DIR / "dashboard" / fname
        if src.exists():
            (docs_dir / fname).write_bytes(src.read_bytes())

    ensure_repo(token, owner, REPO_NAME)

    files_to_push: list[Path] = []
    for rel in PUBLISH_FILES:
        p = BASE_DIR / rel
        if p.exists():
            files_to_push.append(p)
    # Include MS Fabric exports
    fabric_dir = BASE_DIR / "exports" / "fabric"
    if fabric_dir.exists():
        for fp in sorted(fabric_dir.glob("*")):
            if fp.is_file():
                files_to_push.append(fp)

    msg = sys.argv[1] if len(sys.argv) > 1 else "Publish University of Hull HE Competitor & Web/SEO Radar"
    push_commit_via_api(token, owner, REPO_NAME, files_to_push, msg)
    pages_url = enable_github_pages(token, owner, REPO_NAME)

    print("\n" + "=" * 68)
    print(f"GitHub Repo : https://github.com/{owner}/{REPO_NAME}")
    print(f"Live Pages  : {pages_url}")
    print("=" * 68)


if __name__ == "__main__":
    main()
