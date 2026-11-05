# jobcopilot

A job-search assistant I run every day. It collects offers, scores them against my profile with Claude, and prepares applications for me to review. It can fill in a form, but it never submits one. That step stays with me.

## What it does

1. **Collects** offers from 9 sources, including company job boards on Greenhouse, Ashby and Lever. LinkedIn public listings are a tenth, optional source, off by default.
2. **Filters** them with rules: location, level, excluded phrases and minimum salary when one is stated.
3. **Scores** what remains from 0 to 100 with Claude, with the reasons for the fit and the gaps.
4. **Prepares** the best matches: reads the application form, tailors the CV and drafts the open answers in my own writing style.
5. **Fills** the form in a visible browser on my Mac. I check everything and submit it myself.

## Keeping the output honest

An LLM writing a CV can invent things, so the checks are plain code, not more prompting:

- **Fact guard:** every number and technology name in a generated CV or answer must already exist in my experience bank. If not, the output is flagged.
- **Copy and repeat checks:** answers that lift passages from the posting, or repeat another answer in the same application, are flagged.
- **AI rules:** postings that ask candidates not to use AI are detected and quoted.
- **Personal questions:** diversity and consent questions are never answered by Claude or the agent.
- **No submit:** the agent only fills fields. The one button it can click, to open a form, is checked first, and tests confirm that nothing gets submitted.

## How it is built

- Python, FastAPI and htmx for the dashboard, Typer for the CLI
- Claude for scoring and drafting, with JSON schema checks and a retry on invalid output
- WeasyPrint for the CV PDF
- Playwright for the Mac agent, which polls the dashboard and acts in a visible Chromium window
- Docker Compose for the daily run and the dashboard
- 440+ tests with pytest

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

- I have not published a measurement of scoring quality yet.
- It needs a Claude Code token.
- It is built for my own search. The profile, CV and config files need editing before it suits anyone else.
