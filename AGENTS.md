# Repository Instructions

For work in this repository:

- Use `config/sources.json` as the source of truth for databases, topics, venues, and date ranges.
- Generate GitHub Pages content into `docs/index.md`.
- Treat `data/papers.json` as generated paper data.
- Prefer official or stable APIs:
  - arXiv API for arXiv.
  - DBLP API for conference metadata.
  - OpenReview API for ICLR.
  - Semantic Scholar only when `SEMANTIC_SCHOLAR_API_KEY` or `S2_API_KEY` is configured.
- Do not use Google Scholar scraping.
- When a fetch fails, check the HTTP status, response body, API key environment variables, generated query URL, and current source configuration.
- When changing paper sources, update `config/sources.json` first, then run:

```bash
python scripts/fetch_papers.py
python scripts/analyze_papers.py --limit 80 --no-codex
python scripts/build_page.py
```

- Keep `README.md` as direct operation steps. Do not add long explanations there.
- Before pushing, run `git status --short` and avoid reverting user changes.
