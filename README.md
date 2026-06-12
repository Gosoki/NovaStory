# NovaStory v2 · Storyboard Co-Creation Experiment

Streamlit platform for a **within-subjects online experiment** on how the
placement of human intervention in an AI storyboarding pipeline affects output
homogenization and psychological ownership.

- **Condition C** — fully automatic: intent statement → final script
- **Condition D** — outline checkpoint: intent → AI outline → human edit → final
- **Condition E** — D + **ModeMirror**: before finalizing, the AI shows the
  "default version" it would have produced, diagnoses overlap with the user's
  outline, asks a challenging question, offers a counter-proposal, and forces
  an accept / transform / reject adjudication

Each participant: consent → novice screening (6-item battery + DAT) →
3 rounds (3×3 Latin square over conditions × topics) with an in-app
questionnaire + per-shot intent annotation after each round → completion code.
Research design: see `paper/4、研究方案书.md`; engineering spec: `paper/5、开发任务书.md`.

> v1 (the A/B/C/D wizard flow) was retired on 2026-06-12; its code lives in git
> history (`3a7737b`) and its pilot data in `data/archive/`.

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
