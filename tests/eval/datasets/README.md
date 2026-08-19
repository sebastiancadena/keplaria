# Evaluation Datasets

This directory contains evaluation datasets for testing agent behavior.

## Start here: the domain suite

Everything below this section is the scaffold's generic guidance. The eval
that actually gates anything in this project is the **domain suite**, and it
does not run the way the scaffold describes:

```bash
bash tests/eval/run_domain_evals.sh     # seed -> generate -> grade -> save evidence
```

- **Dataset:** `domain-dataset.json` — 24 cases, each one a canonical supplier
  event. `basic-dataset.json` is the untouched scaffold sample.
- **`agents-cli eval generate` cannot collect this graph's stream.** Its SSE
  parser requires every event to carry `author` and `content`, and Workflow
  function nodes legitimately emit state-only events with neither. Only the
  *generate* stage is replaced, by `../generate_traces.py`; grading stays in
  `agents-cli eval grade`.
- **Grading is deterministic**, not judged: `../domain_metrics.py` scores each
  case 1.0/0.0 against the post-run Firestore case document. Every expectation
  is a binary policy outcome with an exact answer, so a judged rubric would
  only add noise.
- **Prerequisites**, both local: the Firestore emulator listening on 8451 (it
  dies silently — the runner checks first) and `../yente_stub.py`, which the
  runner starts for you. Real Gemini calls are made; no ERP call ever is.
- **Adding a case** means four edits, and unit tests in
  `tests/unit/test_domain_metrics.py` fail if you miss one: the dataset here,
  a wipe/seed slot in `../seed.py`, a branch in `../domain_metrics.py`, and a
  model-exposure expectation in that same file naming which agents may and may
  not run on it.
- **Evidence** lands in `spikes/domain_evals/evidence.json`, with every graded
  run kept in `history/` — the failing ones too, not just the flattering
  latest. A score you did not re-run is not evidence.

## Running Evaluations

### Default Dataset

```bash
# Generate traces using the default dataset
agents-cli eval generate
agents-cli eval grade
```

### Custom Dataset

```bash
# Generate traces for a custom dataset
agents-cli eval generate --dataset tests/eval/datasets/custom-dataset.json --output custom_traces/
agents-cli eval grade --metrics general_quality --traces custom_traces/
```

### Deployed Agent

By default, `eval generate` starts a local HTTP server to run your agent in, dispatches each case in parallel and then tears the server down. Pass `--url <base_url> --app-name <name>` to target an already-running or deployed agent instead.

```bash
agents-cli eval generate --url https://my-agent.run.app --app-name app
```

## Dataset Format

Each dataset file follows the Gemini Enterprise Agent Platform Evaluation
dataset format. An eval case may use **either** of two shapes — both are
valid input to `agents-cli eval generate`:

**Shape A — single-prompt case:**

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "prompt": {
        "role": "user",
        "parts": [{"text": "User message"}]
      }
    }
  ]
}
```

**Shape B — continued-conversation case (the "N+1" pattern):**
The case carries prior turns in `agent_data` and the last turn ends with a
user message; `eval generate` appends the next agent response.

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "agent_data": {
        "turns": [
          {
            "turn_index": 0,
            "events": [
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "First user message"}]}},
              {"author": "agent", "content": {"role": "model", "parts": [{"text": "First agent reply"}]}},
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "Follow-up user message"}]}}
            ]
          }
        ]
      }
    }
  ]
}
```

## Key Fields

- `eval_cases`: Array of evaluation cases.
- `eval_case_id`: Unique identifier for the evaluation case (optional).
- `prompt`: A single user message — Shape A.
- `agent_data.turns`: Prior conversation turns ending with a user message — Shape B.

## Creating Custom Datasets

You can create custom datasets in two ways:

1. **By Hand**: Copy `basic-dataset.json` as a template and manually add evaluation cases.
2. **Synthesize**: Use the synthetic dataset generation command to generate conversation scenarios:

   ```bash
   agents-cli eval dataset synthesize --count 10
   ```

## Discovering Metrics

You can discover available out-of-the-box evaluation metrics by running:

```bash
agents-cli eval metric list
```

## Beyond Generate and Grade

Once you have a baseline, the eval surface has a few more commands worth knowing about:

- `agents-cli eval compare BASE CAND` — diff two grade-results files (regression check).
- `agents-cli eval analyze RESULTS` — cluster failure modes from a grade-results file.
- `agents-cli eval optimize` — auto-tune your agent's prompts using eval data.

See the [Evaluation Guide](https://google.github.io/agents-cli/guide/evaluation/) for the full surface and metric reference.
