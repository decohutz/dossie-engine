# Test fixtures

This directory holds baseline outputs used by regression tests.

**Fixtures are NOT committed** to the repo because they contain real CIM
data under confidentiality agreements. See `.gitignore`.

## Running regression tests locally

Tests gracefully skip when the baseline directory is absent, so `pytest`
passes cleanly on a fresh checkout.

To actually exercise the regression tests, you need:

1. The source PDF in `data/inputs/Projeto_Frank_CIM.pdf` (also confidential,
   not committed)
2. The baseline artifacts in `tests/fixtures/frank_baseline/`:
   - `valuation.json`
   - `dossie.md`
   - `dossie.xlsx`

### Generating a fresh baseline

```bash
python -m src.cli process data/inputs/Projeto_Frank_CIM.pdf \
    -p "Frank Baseline" --valuation --xlsx --no-llm

mkdir -p tests/fixtures/frank_baseline
cp data/outputs/valuation_frank_baseline.json tests/fixtures/frank_baseline/valuation.json
cp data/outputs/dossie_frank_baseline.md      tests/fixtures/frank_baseline/dossie.md
cp data/outputs/dossie_frank_baseline.xlsx    tests/fixtures/frank_baseline/dossie.xlsx
```

### Running the tests

```bash
pytest tests/test_frank_regression.py -v
```