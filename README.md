# jobcopilot

A job-search assistant I run every day. It collects offers, scores them against my profile with Claude, and prepares applications for me to review. It can fill in a form, but it never submits one. That step stays with me.

## What it does

1. **Collects** offers from 9 sources, including company job boards on Greenhouse, Ashby and Lever. LinkedIn and Indeed public listings are optional extra sources, off by default.
2. **Filters** them with rules: location, level, excluded phrases and minimum salary when one is stated.
3. **Scores** what remains from 0 to 100 with Claude, with the reasons for the fit and the gaps.
4. **Prepares** the best matches: reads the application form, tailors the CV and drafts the open answers in my own writing style.
5. **Fills** the form in a visible browser on my Mac. I check everything and submit it myself.

## The dashboard

- **Dashboard:** key numbers, the funnel from offer to interview, offers per day, score spread, sources, and what needs me now. It warns when the daily run is missing.
- **Offers:** every open offer from any day, best first, with search and filters. Each card shows why it fits, the gaps, and one button for the next step.
- **Applied and Pipeline:** what I sent, when, and which ones are due a follow-up.
- When a form can't be read, such as LinkedIn Easy Apply, it looks for the same role on the company's own job board, and still tailors the CV so I can apply by hand.

## Keeping the output honest

An LLM writing a CV can invent things, so the checks are plain code, not more prompting:

- **Fact guard:** every number and technology name in a generated CV or answer must already exist in my experience bank. If not, the output is flagged.
- **Copy and repeat checks:** answers that lift passages from the posting, or repeat another answer in the same application, are flagged.
- **AI rules:** postings that ask candidates not to use AI are detected and quoted.
- **Personal questions:** diversity and consent questions are never answered by Claude or the agent.
- **No submit:** the agent only fills fields. The one button it can click, to open a form, is checked first, and tests confirm that nothing gets submitted.

## Measuring the AI

`jobcopilot eval run` checks the AI features against a fixed test set and a saved baseline, and the Evals page shows one verdict, the failing cases and what to do next.

- **Match scoring:** 32 authored postings, clear fits and clear misfits, including traps such as years of experience hidden in the body or an on-site-only role. Each score must land on the right side of its line.
- **Agreement with my decisions:** the jobs I applied to should score above the ones I skipped. The test set grows as I use the dashboard.
- **Tailored CV:** the share of CVs that pass the fact guard on the first try, before any retry.
- **Gate:** a change counts as worse only when the 95% interval on the same cases excludes zero. `eval run --gate` then exits with code 1.

## How it is built

- Python, FastAPI and htmx for the dashboard, Typer for the CLI
- Claude for scoring and drafting, with JSON schema checks and a retry on invalid output
- WeasyPrint for the CV PDF
- Playwright for the Mac agent, which polls the dashboard and acts in a visible Chromium window
- Docker Compose for the daily run and the dashboard
- 500+ tests with pytest

## Run it

```bash
cp .env.example .env    # add CLAUDE_CODE_OAUTH_TOKEN
mkdir -p data
cp config.example.yaml data/config.yaml
cp profile.example.yaml data/profile.yaml
cp cv.example.yaml data/cv.yaml
docker compose up -d --build
```

The dashboard is at http://127.0.0.1:8077. It has no login, so keep that port local.

Without Docker:

```bash
uv sync
uv run jobcopilot --home data run
uv run jobcopilot --home data serve
```

## Limits

- The scoring test set is small and the scorer passes all of it, so it catches regressions but cannot show improvements yet. Agreement with my own decisions rests on 14 jobs.
- It needs a Claude Code token.
- It is built for my own search. The profile, CV and config files need editing before it suits anyone else.
