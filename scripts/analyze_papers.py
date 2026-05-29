#!/usr/bin/env python3
"""Analyze fetched papers with Codex, or produce a deterministic fallback."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAPERS = ROOT / "data" / "papers.json"
PROMPT_VERSION = "security-paper-v1"


def load_papers(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_papers(path: Path, papers: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_prompt(paper: dict) -> str:
    authors = ", ".join(paper.get("authors", []))
    return f"""
你是安全研究论文阅读助手。请基于给定标题、作者和摘要分析论文，输出严格 JSON，不要 Markdown。

JSON 字段必须为：
- summary: 中文，2-4 句概括论文在做什么
- contributions: 中文字符串数组，列出 2-5 个创新点或贡献
- rating: 1 到 5 的整数，表示对安全研究读者的推荐星级
- recommendation: 中文，1-3 句说明推荐理由
- tags: 英文小写字符串数组，3-6 个主题标签

论文信息：
Title: {paper.get("title", "")}
Authors: {authors}
Source: {paper.get("source", "")}
Published: {paper.get("published", "")}
URL: {paper.get("url", "")}
Abstract:
{paper.get("abstract", "")}
""".strip()


def extract_json(text: str) -> dict | None:
    text = text.strip()
    candidates = [text]
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if match:
        candidates.insert(0, match.group(1))
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        candidates.append(brace.group(0))

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def normalize_analysis(payload: dict, method: str) -> dict:
    rating = payload.get("rating", 3)
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        rating = 3
    rating = max(1, min(5, rating))

    return {
        "summary": str(payload.get("summary", "")).strip(),
        "contributions": [str(x).strip() for x in payload.get("contributions", []) if str(x).strip()][:5],
        "rating": rating,
        "recommendation": str(payload.get("recommendation", "")).strip(),
        "tags": [str(x).strip().lower() for x in payload.get("tags", []) if str(x).strip()][:6],
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "prompt_version": PROMPT_VERSION,
    }


def analyze_with_codex(paper: dict) -> dict | None:
    codex = shutil.which("codex")
    if not codex:
        return None

    model = os.environ.get("CODEX_MODEL")
    with tempfile.NamedTemporaryFile("r+", encoding="utf-8", suffix=".txt") as output:
        cmd = [
            codex,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--output-last-message",
            output.name,
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(build_prompt(paper))

        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        except (subprocess.SubprocessError, OSError):
            return None

        output.seek(0)
        final_message = output.read()

    payload = extract_json(final_message) or extract_json(result.stdout)
    if not payload:
        return None
    return normalize_analysis(payload, "codex")


def fallback_analysis(paper: dict) -> dict:
    abstract = paper.get("abstract", "")
    first_sentence = re.split(r"(?<=[.!?])\s+", abstract.strip())[0] if abstract else ""
    categories = paper.get("categories", [])
    payload = {
        "summary": first_sentence or "暂无自动摘要；请配置 Codex 后重新运行分析任务。",
        "contributions": [
            "基于论文摘要提取，完整创新点需要大模型分析确认。",
            "该论文属于安全相关方向，建议结合正文进一步评估方法和实验。",
        ],
        "rating": 3,
        "recommendation": "当前为规则降级结果；配置 Codex 后会生成更准确的推荐理由。",
        "tags": [c.lower().replace(".", "-") for c in categories[:4]] or ["security"],
    }
    return normalize_analysis(payload, "fallback")


def needs_analysis(paper: dict, force: bool) -> bool:
    analysis = paper.get("analysis") or {}
    return force or analysis.get("prompt_version") != PROMPT_VERSION or not analysis.get("summary")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--papers", type=Path, default=DEFAULT_PAPERS)
    parser.add_argument("--limit", type=int, default=int(os.environ.get("ANALYZE_LIMIT", "8")))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-codex", action="store_true", help="Use deterministic fallback analysis without invoking Codex.")
    args = parser.parse_args()

    papers = load_papers(args.papers)
    analyzed = 0
    for paper in papers:
        if analyzed >= args.limit:
            break
        if not needs_analysis(paper, args.force):
            continue
        paper["analysis"] = fallback_analysis(paper) if args.no_codex else analyze_with_codex(paper) or fallback_analysis(paper)
        analyzed += 1

    save_papers(args.papers, papers)
    print(f"Analyzed {analyzed} papers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
