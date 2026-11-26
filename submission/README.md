# SQL Repair Agent - CPU reproduction

This export implements a deterministic, feedback-driven SQL repair agent. It uses the course's fixed public hints, SQLite feedback and SQLGlot edits. The deployed agent has no live model, no model weights, no API calls, no task-specific answer table, and no benchmark database access outside official tools.

The official package must be downloaded separately. Do not replace any official source or data files with files from this export.

## Linux / macOS

Use Python 3.11 or 3.12. Set `OFFICIAL` to the extracted course package and `SOURCE` to this export:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r "$SOURCE/requirements.txt"
PYTHONUTF8=1 PYTHONPATH="$OFFICIAL" python -m mp3_sql.check_data
python "$SOURCE/reproduce.py" --official "$OFFICIAL" --split test
```

`reproduce.py` calls the unmodified `mp3_sql.predict` and `mp3_sql.score` CLIs, using four workers. It sets `PYTHONPATH` and UTF-8 mode for the child processes. All paths are passed as argument lists. Prediction and score files are written under this export's `outputs/` directory.

Equivalent explicit official commands:

```bash
export PYTHONUTF8=1
export PYTHONPATH="$SOURCE:$OFFICIAL"
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4
python -m mp3_sql.predict --split test --workers 4 \
  --method "$SOURCE/submission.py" --checkpoint "$SOURCE/config.json" \
  --output "$SOURCE/outputs/test.json"
python -m mp3_sql.score --split test --workers 4 \
  --predictions "$SOURCE/outputs/test.json" --output "$SOURCE/outputs/test-score.json"
```

After SQLGlot is provisioned, prediction/scoring work offline. No optional model dependencies are required.

## Controlled ablations

Use validation for design choices. Run each command from the activated environment:

```bash
python "$SOURCE/reproduce.py" --official "$OFFICIAL" --split validation \
  --config "$SOURCE/ablations/draft_only.json" --output-dir "$SOURCE/experiments/draft_only"
python "$SOURCE/reproduce.py" --official "$OFFICIAL" --split validation \
  --config "$SOURCE/ablations/hints_only.json" --output-dir "$SOURCE/experiments/hints_only"
python "$SOURCE/reproduce.py" --official "$OFFICIAL" --split validation \
  --config "$SOURCE/ablations/no_entity_names.json" --output-dir "$SOURCE/experiments/no_entity_names"
python "$SOURCE/reproduce.py" --official "$OFFICIAL" --split validation \
  --config "$SOURCE/config.json" --output-dir "$SOURCE/experiments/full"
```

The draft-only configuration executes/checks the exact draft and then submits it; it is a diagnostic baseline. The hints-only ablation retains hint checking and selection but disables AST repairs. The no-entity-names ablation disables name-to-entity grounding in both candidate generation and post-check refinement while retaining all other repairs. These use the same implementation, inputs and official tools as the full system.

## Files

- `submission.py`: public `create_agent(checkpoint=None)` entry point and feedback controller.
- `repairs.py`: generic SQL AST edits and schema graph search.
- `config.json`: final CPU configuration.
- `report.pdf`: method, experiments, costs, failure analysis, safeguards and assistance disclosure.
- `outputs/test.json`: complete official prediction trajectories.
- `outputs/test-score.json`: complete, unchanged official score; upload this file to the course form.
- `outputs/test-runtime.json`: sampled Windows process-tree resource measurements.
- `experiments/`: validation ablation predictions and unchanged official scores.

No model is declared because none runs during evaluation. The fixed hint cache is supplied by the official `repair_hint` tool. It is not bundled or modified here.

Exploratory local LoRA training and unchanged-model inference were evaluated during development but did not improve the selected controller. They are described in the report, with training configuration/provenance and logs under `experiments/development/`. No experimental weights, training dataset or base model is part of this deployed export.

## Attribution and assistance

Benchmark: Spider-derived course package, distributed under CC BY-SA 4.0; see the official package's `NOTICE`. This export does not redistribute the benchmark databases. SQLGlot remains under its upstream license and is installed separately.

OpenAI Codex assisted implementation, debugging, experiment design and report preparation. It is not called by the submitted agent. The student should review and understand this implementation and the report before submitting the individual assignment.
