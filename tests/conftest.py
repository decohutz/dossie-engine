"""
Shared pytest fixtures.

Design note: the Frank pipeline is session-scoped so the full PDF-to-artifacts
run (~30s in --no-llm mode) happens ONCE regardless of how many tests consume
it. Individual tests read their own artifact and assert against it — the
pipeline run is shared infrastructure, not per-test setup.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Project roots (tests/conftest.py lives one level under repo root)
REPO_ROOT = Path(__file__).resolve().parent.parent
FRANK_PDF = REPO_ROOT / "data" / "inputs" / "Projeto_Frank_CIM.pdf"
BASELINE_DIR = REPO_ROOT / "tests" / "fixtures" / "frank_baseline"


@pytest.fixture(scope="session")
def frank_pipeline_outputs(tmp_path_factory) -> dict:
    """Run the Frank pipeline once per session in --no-llm mode.

    Returns a dict with paths to the generated artifacts:
        {
            "md":        Path("...dossie_frank_regression.md"),
            "json":      Path("...valuation_frank_regression.json"),
            "xlsx":      Path("...dossie_frank_regression.xlsx"),
            "pptx":      Path("...dossie_frank_regression.pptx"),
            "out_dir":   Path(...),       # tmpdir the artifacts live in
        }

    Why --no-llm: deterministic. The financial extraction path (the only thing
    our refactor touches) doesn't depend on the LLM anyway — it uses the rules-
    based pdfplumber text parser. This lets these tests run in CI without Ollama.
    """
    if not FRANK_PDF.exists():
        pytest.skip(f"Fixture PDF not found at {FRANK_PDF}")

    # Isolate outputs in a temp dir so repeated runs don't collide.
    out_dir = tmp_path_factory.mktemp("frank_pipeline")

    # The CLI hardcodes `data/outputs/` — easiest path is to call run_pipeline
    # + the exporters directly, mirroring cli.py's flow.
    from src.pipeline.orchestrator import run_pipeline
    from src.pipeline.assembler import to_markdown
    from src.valuation.scenarios import run_full_valuation
    from src.exporters.xlsx_exporter import export_xlsx
    from src.exporters.pptx_exporter import export_pptx

    dossier = run_pipeline(
        pdf_path=str(FRANK_PDF),
        project_name="Frank Regression",
        use_llm=False,
        enrich=False,
        verbose=False,
    )

    # Markdown
    md_path = out_dir / "dossie.md"
    md_path.write_text(to_markdown(dossier), encoding="utf-8")

    # Valuation
    val_result = run_full_valuation(dossier, verbose=False)
    json_path = out_dir / "valuation.json"
    json_path.write_text(
        json.dumps(val_result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # Excel
    xlsx_path = out_dir / "dossie.xlsx"
    export_xlsx(dossier, str(xlsx_path), valuation_data=val_result, verbose=False)

    # PPT
    pptx_path = out_dir / "dossie.pptx"
    export_pptx(dossier, str(pptx_path), valuation_data=val_result, verbose=False)

    return {
        "md": md_path,
        "json": json_path,
        "xlsx": xlsx_path,
        "pptx": pptx_path,
        "out_dir": out_dir,
    }


@pytest.fixture(scope="session")
def frank_baseline_dir() -> Path:
    """Return the path to the frank baseline fixtures directory.

    These files are the golden output committed at the point we locked in the
    current refactor. Any future code change that causes a financial, valuation,
    or exporter diff against them is a regression.
    """
    if not BASELINE_DIR.exists():
        pytest.skip(f"Baseline fixtures not found at {BASELINE_DIR}")
    return BASELINE_DIR
