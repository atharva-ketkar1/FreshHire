# FreshHire

Job alerts with a freshness signal you can trust. Polls company ATS boards
(Greenhouse, Lever) directly via their public JSON APIs, records the exact
moment *we* first see each posting (`first_seen_at`), and alerts on relevant
AI/ML roles that appeared in the last 24 hours — not whatever date an
aggregator claims.

## How freshness works

- `first_seen_at` is set once, when a job first appears on a board we watch,
  and never updated. It is the only freshness signal used.
- Dedup is by fingerprint: `sha1(company | normalized title | normalized
  location)`. A repost under a new ATS job ID maps to the existing record and
  keeps its original `first_seen_at`, so reposts can never look fresh.
- A company's **first poll is baseline**: every job already on the board is
  recorded but flagged `is_baseline=1` and never alerted. Only jobs that
  appear *after* we start watching count.
- Jobs that vanish from a board get `closed_at` (fills are visible too), and
  alerts fire at most once per job (`alerted_at`).

## Usage

```bash
pip install -r requirements.txt
python cli.py poll               # fetch all boards, record sightings
python cli.py digest --dry-run   # preview fresh relevant jobs
python cli.py digest             # send Slack digest (needs SLACK_WEBHOOK_URL)
python cli.py run                # poll + digest — what the cron runs
python cli.py list-new --hours 48
python cli.py rescore            # re-apply filters.yaml after tuning it
```

## Configuration

- **[config/companies.yaml](config/companies.yaml)** — companies to watch,
  each mapped to `{ats, slug}`. All seeded slugs verified live 2026-07-14.
  Removing a company deactivates it; its history is kept.
- **[config/filters.yaml](config/filters.yaml)** — role relevance rules,
  three stages: title excludes → title includes → conditional titles (generic
  titles like "Software Engineer" need AI context in the title, or ≥2 distinct
  strong phrases like "fine-tuning" / "model inference" in the description —
  one boilerplate "LLM" mention is not enough). Word-boundary matching
  throughout, so "ai" never matches "email". After editing, run
  `python cli.py rescore`.

Bias is deliberately toward signal: better to miss a borderline role than to
spam. If Glean-style product-SWE roles feel noisy, raise `min_desc_matches`
to 3; if it's missing too much, add terms to `include_title`.

## Deploying the cron (GitHub Actions)

[.github/workflows/poll.yml](.github/workflows/poll.yml) runs every 3 hours
and commits `data/freshhire.db` back to the repo to persist state.

1. `git init && git add -A && git commit -m "init"` and push to a **private**
   GitHub repo (the DB with your alert history gets committed to it).
2. Create a Slack incoming webhook (Slack app → Incoming Webhooks).
3. Repo → Settings → Secrets and variables → Actions → add
   `SLACK_WEBHOOK_URL`.
4. Trigger the workflow once manually (Actions → FreshHire poll → Run
   workflow) to seed the baseline, or commit your locally-seeded
   `data/freshhire.db` so the baseline is already done.

## Roadmap

- **Ashby adapter** (`https://api.ashbyhq.com/posting-api/job-board/{slug}`):
  unlocks OpenAI, Cursor, ElevenLabs, Sierra, Modal, Notion, Perplexity,
  Cohere, Groq, Harvey, LangChain and most 2023+ startups.
- **Workday**: needs headless-browser or reverse-engineered CxS API scraping
  (NVIDIA, Salesforce, most Big Tech).
- **Email digest** as a second alert channel.
- Location filtering, per-company relevance overrides.
