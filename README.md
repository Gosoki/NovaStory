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

Each participant: consent (language picked once, ja/zh/en) → "before you
start" intro page → background questionnaire (novice status recorded, nobody
screened out) → 3 rounds (**Williams design**: all 6 orderings of C/D/E × 3
topic rotations = 18 sequences) with an in-app questionnaire (incl. per-round
satisfaction) + per-shot intent annotation after each round → whole-study final
survey → completion code. A resume token in the URL (`?t=`) survives
refresh/reconnect without duplicating data.
**All research documentation lives in [`docs/paper/`](docs/paper/README.md)**
(design: `02`; measures: `03`; analysis plan: `04`; open decisions: `06`).

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
| `participants` | demographics, screening battery (incl. baseline covariates), latin-square seq, attention check, resume `token`, `final_survey_json`, completion code, status |
| `trials` | per round: condition, intent, `guidance_json` (E Q&A), `revision_requests` (D), `script_versions` (ai/user_edit), `n_ai_rounds`/`n_hand_edits`/`hand_edit_chars`, `t_pregen`/`t_postgen`, generation params (model/temperature/base_url); unique on (participant_id, round_idx) + INSERT OR REPLACE so resumes/double-clicks never duplicate a round |
| `events` | millisecond-timestamped interaction log (round_start … trial_submit, session_resumed) with per-round ordering (`seq_in_round`), session-segment `attempt` ids and post-submit `trial_id` backfill; `llm_done` carries per-call token usage |
| `questionnaires` | ownership / agency / TLX items, intent-violation, imagine-match, satisfaction, per-shot annotations; unique on (participant_id, round_idx) |

Language: participants run in **Japanese** (`ja`, default); **ja/zh/en are all
end-to-end** (UI + prompts + topic scenarios + shot parsing), and the researcher
can switch for testing (topics carry `{ja, zh, en}`, LLM output follows the
participant's language). Researcher mode (sidebar, password-locked): table
browser + CSV export + session reset for local testing.

## Offline pipeline (analysis)

`scripts/` and `analysis/` hold the non-Streamlit pipeline. The **v3 pipeline is
complete and self-tested**: `analysis/{textstats,v3,stats,power_sim,embed,
figures,norming,pilot_check}.py` — `make analysis` runs the whole chain, `make
pilot` prints the four go/no-go readings, `make help` lists everything. Frozen
pre-registration constants live in `analysis/prereg.py` (single source of truth).
Legacy `analysis/metrics.py` (v2 HLZ era) is superseded by `v3.py` and gets
deleted after data collection; ghost-run was removed 2026-07-02. The same
functions are also exposed in the researcher web panel (no CLI needed).

## Tests

```bash
.venv/bin/python scripts/dev_smoke_e2e.py   # full participant flow, stubbed LLM, temp DB
```

## i18n

`i18n/locales/{ja,zh,en}.json` mirror the same key tree (missing keys fall back
to `ja`, the study language). Participants pick ja/zh/en once on the consent
page; topics live in `data/topics.json` (first 3 entries are used).
