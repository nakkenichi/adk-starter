# Agent Evaluation

Live quality evaluation with `agents-cli eval` (not part of the offline unit suite).

| File | Purpose |
|---|---|
| `datasets/basic-dataset.json` | Eval cases (the agents-cli default dataset path). |
| `eval_config.yaml` | Metrics to grade (`final_response_quality`, `safety`). |

## Run locally

```bash
gcloud auth application-default login
make eval                     # = agents-cli eval run (local agent + Vertex AI grading)
```

`agents-cli` boots the agent locally, sends every eval case, stores traces under
`artifacts/traces/`, then grades them and writes results to `artifacts/grade_results/`.
Configure the project in `.env` (`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=global`).

## In CI

`.github/workflows/eval.yml` runs nightly (and on demand via *Run workflow*) once the GCP
repository variables exist, and uploads `artifacts/` as the `eval-results` artifact.

## Customize

1. Replace the cases in `datasets/basic-dataset.json` with real user journeys of your domain.
   Add `reference.response` to a case when there is a known-good answer.
2. Add metrics to `eval_config.yaml` (built-in names or `custom_metrics`); see
   `agents-cli eval grade --help` and `agents-cli eval metric --help`.
3. Compare runs with `agents-cli eval compare` before merging prompt or model changes.
