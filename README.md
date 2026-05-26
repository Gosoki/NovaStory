# NovaStory · Storyboard Co-Creation Experiment

A Streamlit demo for studying how different human–AI collaboration modes shape
short-video storyboard creativity. Built around a wizard-style A → B → C/D flow.

- **Group A** — pure manual writing + one-line creative hook
- **Group B** — low automation: hook + custom prompt → AI script (regeneratable)
- **Group C** (Flow 1) — one-click full-auto generation
- **Group D** (Flow 2) — mid-outline intervention: AI outline → user edits → final script

UI is available in 中文 / English / 日本語 (i18n via `i18n/locales/*.json`).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Sidebar configuration

- **Subject ID** & **Flow** (ABC vs ABD)
- **OpenAI API Key**, **Base URL** (defaults to `https://api.openai.com/v1`, override for OpenRouter / local LLMs), **Model** name
- **Topic** — pick a preset from `data/topics.json` or edit live; "Save as new preset" appends to the file
- **Researcher mode** — switches the main area to a CSV viewer with filters and a download button

Topic fields lock the instant Group A is started, so the same Subject ID is guaranteed
consistent across A/B/C/D.

## Data

Submissions append to `data/experiment_results.csv` with columns:

`User_ID, Topic, Group, Total_Time_Seconds, Initial_Input, Interventions, Final_Output, Timestamp`

- `Topic` — JSON snapshot of the topic at A-submit time
- `Interventions` — JSON `{ai_outline, user_edited}`, only for Group D

Self-rating is intentionally **not** collected here; collect it via Google Forms separately.

## i18n

Add a language by dropping a new `i18n/locales/<code>.json` mirroring `zh.json`'s key
tree and appending the code to `AVAILABLE_LANGS` / `LANG_LABELS` in
[i18n/translator.py](i18n/translator.py).
