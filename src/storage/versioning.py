"""
Dossier versioning.
Saves snapshots with timestamps and tracks changes between versions.
"""
from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path


VERSIONS_DIR = Path("data/versions")
OUTPUTS_DIR = Path("data/outputs")


def _ensure_dirs():
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def _project_dir(project_name: str) -> Path:
    """Get or create a project-specific versions directory."""
    safe_name = project_name.lower().replace(" ", "_").replace("/", "_")
    d = VERSIONS_DIR / safe_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_versions(project_name: str) -> list[dict]:
    """List all saved versions for a project, newest first."""
    proj_dir = _project_dir(project_name)
    versions = []

    for f in sorted(proj_dir.glob("v*.json"), reverse=True):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            meta = data.get("metadata", {})
            versions.append({
                "file": f.name,
                "path": str(f),
                "version": meta.get("version", f.stem),
                "created_at": meta.get("created_at", ""),
                "target_company": meta.get("target_company", ""),
                "project_name": meta.get("project_name", ""),
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return versions


def get_next_version_number(project_name: str) -> str:
    """Get the next version number (v001, v002, ...)."""
    versions = list_versions(project_name)
    if not versions:
        return "v001"

    # Find highest existing version number
    max_num = 0
    for v in versions:
        ver = v["version"]
        if ver.startswith("v") and ver[1:].isdigit():
            max_num = max(max_num, int(ver[1:]))

    return f"v{max_num + 1:03d}"


def save_version(project_name: str, dossier_dict: dict) -> str:
    """Save a versioned snapshot of the dossier.

    Returns the path to the saved file.
    """
    _ensure_dirs()
    proj_dir = _project_dir(project_name)

    version = dossier_dict.get("metadata", {}).get("version", "v001")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{version}_{timestamp}.json"
    filepath = proj_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(dossier_dict, f, ensure_ascii=False, indent=2, default=str)

    return str(filepath)


def load_version(project_name: str, version: str | None = None) -> dict | None:
    """Load a specific version, or the latest if version is None."""
    versions = list_versions(project_name)
    if not versions:
        return None

    if version is None:
        # Load latest
        path = versions[0]["path"]
    else:
        # Find specific version
        match = [v for v in versions if v["version"] == version or version in v["file"]]
        if not match:
            return None
        path = match[0]["path"]

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_diff(old: dict, new: dict) -> list[str]:
    """Compute a simple diff between two dossier versions.

    Returns a list of human-readable change descriptions.
    """
    changes = []

    old_meta = old.get("metadata", {})
    new_meta = new.get("metadata", {})
    if old_meta.get("version") != new_meta.get("version"):
        changes.append(f"Version: {old_meta.get('version')} -> {new_meta.get('version')}")

    # Compare summary counts
    old_summary = _count_summary(old)
    new_summary = _count_summary(new)

    for key in new_summary:
        old_val = old_summary.get(key, 0)
        new_val = new_summary[key]
        if old_val != new_val:
            diff = new_val - old_val
            sign = "+" if diff > 0 else ""
            changes.append(f"{key}: {old_val} -> {new_val} ({sign}{diff})")

    # Compare gaps
    old_gaps = len(old.get("gaps", []))
    new_gaps = len(new.get("gaps", []))
    if old_gaps != new_gaps:
        diff = new_gaps - old_gaps
        sign = "+" if diff > 0 else ""
        changes.append(f"Gaps: {old_gaps} -> {new_gaps} ({sign}{diff})")

    if not changes:
        changes.append("No changes detected")

    return changes


def _count_summary(data: dict) -> dict[str, int]:
    """Count key elements in a dossier dict."""
    company = data.get("company", {})
    financials = data.get("financials", {})
    market = data.get("market", {})

    fin_count = sum(
        1 for key in financials
        if key.startswith(("dre_", "balance_")) and financials.get(key) is not None
        and isinstance(financials[key], dict) and financials[key].get("lines")
    )

    return {
        "executives": len(company.get("executives", [])),
        "shareholders": len(company.get("shareholders", [])),
        "timeline_events": len(company.get("timeline", [])),
        "products": len(company.get("products", [])),
        "financial_statements": fin_count,
        "competitors": len(market.get("competitors", [])),
        "market_sizes": len(market.get("market_sizes", [])),
    }