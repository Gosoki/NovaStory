# NovaStory v3 · Guided Co-Creation Experiment

Streamlit platform for a **within-subjects online experiment** comparing
*ask-before-generate* and *fix-after-generate* AI workflows for novice
storyboard creators (intent fidelity, psychological ownership, revision effort).

- **Condition C** — one-shot: intent → full script → submit (no loop)
- **Condition D** — generate-then-repair: AI generates first; the script is an
  always-editable textarea plus a free-form "tell the AI what to change" box;
  unlimited revision loop (proxy for the default chatbot workflow)
- **Condition E** — guide-then-generate: the AI first asks 5-7 option-style
  questions (3 fixed expert dimensions + 2-4 AI-chosen), then generates; the
  user can keep requesting guided follow-up rounds or edit directly

Each participant: consent → background questionnaire (novice status recorded,
nobody screened out) → 3 rounds (3×3 Latin square over conditions × topics)
with an in-app questionnaire + per-shot intent annotation after each round →
completion code. Interaction spec: `paper/7`; engineering: `paper/5`;
runtime flow: `paper/6`; claim verification: `paper/9`.

> v1 (A/B/C/D wizard) retired 2026-06-12 (`3a7737b`); v2 (ModeMirror E,
> outline-editing D) retired 2026-06-13 — both live in git history.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml  # fill api_configs
streamlit run app.py
```

`secrets.toml` keys: `[[api_configs]]` blocks (first one is auto-applied for
participants) and optional `researcher_password` (fallback: env
`NOVASTORY_RESEARCHER_PW`, then dev default `nova`).

## Data

Everything is written to SQLite (`data/novastory.db`, WAL mode, gitignored):

| table | contents |
|---|---|
| `participants` | demographics, screening battery, latin-square seq, attention check, completion code |
| `trials` | per round: condition, intent, AI/edited outlines, ModeMirror dissent + adjudication, final script, generation params (model/temperature/base_url), phase durations (LLM wait separated) |
| `events` | timestamped interaction log (round_start … questionnaire_submit) |
| `questionnaires` | ownership / agency / TLX items, intent-violation, per-shot annotations |

Researcher mode (sidebar, password-locked): table browser + CSV export +
session reset for local testing.

## Offline pipeline (analysis)

`scripts/` and `analysis/` hold the non-Streamlit pipeline (machine baselines,
ghost-run counterfactuals, HLZ/diversity metrics, stats, power simulation,
LLM-judge). See the Makefile and `analysis/requirements-analysis.txt`.

## Tests

```bash
.venv/bin/python scripts/dev_smoke_e2e.py   # full participant flow, stubbed LLM, temp DB
```

## i18n

`i18n/locales/{zh,en,ja}.json` mirror the same key tree (zh is the fallback
source of truth). Deploy a study in ONE language; topics live in
`data/topics.json` (first 3 entries are used).
