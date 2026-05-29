#!/usr/bin/env python3
"""Build the GitHub Pages markdown dashboard."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAPERS = ROOT / "data" / "papers.json"
DEFAULT_OUTPUT = ROOT / "docs" / "index.md"


def load_papers(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def md_escape(value: str) -> str:
    return html.escape(value or "", quote=False).replace("|", "\\|")


def stars(rating: int) -> str:
    rating = max(1, min(5, int(rating or 3)))
    return "★" * rating + "☆" * (5 - rating)


def paper_block(paper: dict) -> str:
    analysis = paper.get("analysis") or {}
    authors = ", ".join(paper.get("authors", [])[:8])
    if len(paper.get("authors", [])) > 8:
        authors += ", et al."
    tags = " ".join(f"`{tag}`" for tag in analysis.get("tags", []))
    contributions = analysis.get("contributions", [])
    contribution_md = "\n".join(f"- {md_escape(item)}" for item in contributions) or "- 暂无"
    pdf = paper.get("pdf_url")
    pdf_link = f" · [PDF]({pdf})" if pdf else ""

    source_parts = [
        md_escape(paper.get("database", "")),
        md_escape(paper.get("venue", "")),
        md_escape(paper.get("topic", "")),
    ]
    source_text = " · ".join(part for part in source_parts if part)
    if not source_text:
        source_text = md_escape(paper.get("source", ""))

    return f"""
### [{md_escape(paper.get("title", ""))}]({paper.get("url", "")})

**作者**：{md_escape(authors)}

**来源**：{source_text} · **发布日期**：{md_escape(paper.get("published", "")[:10])} · **推荐**：{stars(analysis.get("rating", 3))}{pdf_link}

**摘要总结**：{md_escape(analysis.get("summary", "暂无"))}

**创新点**

{contribution_md}

**推荐理由**：{md_escape(analysis.get("recommendation", "暂无"))}

{tags}
""".strip()


def build_page(papers: list[dict]) -> str:
    updated = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M Asia/Shanghai")
    blocks = "\n\n---\n\n".join(paper_block(paper) for paper in papers)
    if not blocks:
        blocks = "暂无论文。"

    return f"""# Security Paper Reader

自动抓取并总结安全相关论文。更新时间：{updated}

## 今日论文

{blocks}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--papers", type=Path, default=DEFAULT_PAPERS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    papers = load_papers(args.papers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_page(papers), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
