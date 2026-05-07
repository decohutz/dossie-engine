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
REGENERA_BASELINE_DIR = REPO_ROOT / "tests" / "fixtures" / "regenera_synthetic_baseline"


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


# ────────────────────────────────────────────────────────────────────
# Regenera synthetic fixtures
# ────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def regenera_synthetic_pipeline_outputs(tmp_path_factory) -> dict:
    """Run the pipeline once per session against the synthetic Regenera
    XLSX fixture and return paths to the generated artifacts.

    The synthetic XLSX reproduces the structural shape of the real
    Projeto Regenera CIM (six entities including a non-operating CSC,
    accent-free 'Receita Liquida' labels, 1h+6p year layout) without
    using the confidential numbers — see tests/_regenera_synthetic.py.

    Manual overrides are passed via run_pipeline kwargs (mimicking the
    --ev-ebitda / --market-size-brl-bn CLI flags from E3.5) so that
    the valuation output is fully populated and IRR/MOIC compute
    deterministically. Without these the comparables-based methods
    silently fall back to None and the regression test would only
    exercise the DCF path.
    """
    from tests._regenera_synthetic import build_regenera_synthetic_xlsx

    out_dir = tmp_path_factory.mktemp("regenera_synthetic_pipeline")
    xlsx_input = out_dir / "_input.xlsx"
    build_regenera_synthetic_xlsx(xlsx_input)

    from src.pipeline.orchestrator import run_pipeline
    from src.pipeline.assembler import to_markdown
    from src.valuation.scenarios import run_full_valuation
    from src.exporters.xlsx_exporter import export_xlsx
    from src.exporters.pptx_exporter import export_pptx

    dossier = run_pipeline(
        inputs=[str(xlsx_input)],
        project_name="Regenera Synthetic Regression",
        use_llm=False,
        enrich=False,
        verbose=False,
        # Manual overrides — pin the multiples and market sizing so
        # the valuation output is deterministic across runs and machines.
        ev_ebitda_override=11.0,
        ev_revenue_override=1.8,
        market_size_brl_bn_override=12.5,
        market_cagr_override=0.08,
    )

    md_path = out_dir / "dossie.md"
    md_path.write_text(to_markdown(dossier), encoding="utf-8")

    val_result = run_full_valuation(dossier, verbose=False)
    json_path = out_dir / "valuation.json"
    json_path.write_text(
        json.dumps(val_result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    xlsx_path = out_dir / "dossie.xlsx"
    export_xlsx(dossier, str(xlsx_path), valuation_data=val_result, verbose=False)

    pptx_path = out_dir / "dossie.pptx"
    export_pptx(dossier, str(pptx_path), valuation_data=val_result, verbose=False)

    return {
        "md": md_path,
        "json": json_path,
        "xlsx": xlsx_path,
        "pptx": pptx_path,
        "out_dir": out_dir,
        "dossier": dossier,
    }


@pytest.fixture(scope="session")
def regenera_baseline_dir() -> Path:
    """Path to the Regenera synthetic baseline fixtures directory.

    These golden files were generated by running the pipeline against
    the synthetic XLSX once at the time E6 was committed. Future code
    changes that produce a diff against them are regressions on the
    Regenera-shape branch (multi-entity, projection-flagged, accent-
    free labels, non-op CSC).
    """
    if not REGENERA_BASELINE_DIR.exists():
        pytest.skip(f"Regenera baseline fixtures not found at {REGENERA_BASELINE_DIR}")
    return REGENERA_BASELINE_DIR
