#!/usr/bin/env python3
"""Fetch recent security papers from configured sources."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "sources.json"
DEFAULT_OUTPUT = ROOT / "data" / "papers.json"
SEMANTIC_SCHOLAR_FIELDS = ",".join(
    [
        "paperId",
        "externalIds",
        "url",
        "title",
        "abstract",
        "venue",
        "year",
        "publicationDate",
        "authors",
        "openAccessPdf",
        "fieldsOfStudy",
        "s2FieldsOfStudy",
    ]
)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def topic_map(config: dict) -> dict[str, dict]:
    return {topic["name"]: topic for topic in config.get("topics", [])}


def resolve_topic(source: dict, topics: dict[str, dict]) -> dict:
    topic_name = source.get("topic")
    if topic_name and topic_name in topics:
        return topics[topic_name]
    return {
        "name": topic_name or source.get("name", "default"),
        "query": source.get("query", ""),
        "keywords": source.get("keywords", []),
    }


def resolve_date_range(source: dict) -> tuple[datetime | None, datetime | None]:
    date_range = source.get("date_range") or {}
    end = datetime.now(timezone.utc)
    if date_range.get("start"):
        start = parse_date(str(date_range["start"]))
    elif date_range.get("days_back"):
        start = end - timedelta(days=int(date_range["days_back"]))
    elif date_range.get("years_back"):
        start = end - timedelta(days=365 * int(date_range["years_back"]))
    else:
        start = None

    if date_range.get("end"):
        end = parse_date(str(date_range["end"]))
    return start, end


def parse_date(value: str) -> datetime:
    if len(value) == 4 and value.isdigit():
        return datetime(int(value), 1, 1, tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_optional_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def in_date_range(value: str | None, start: datetime | None, end: datetime | None) -> bool:
    if value and len(value) == 4 and value.isdigit():
        year = int(value)
        if start and year < start.year:
            return False
        if end and year > end.year:
            return False
        return True
    parsed = parse_optional_date(value)
    if not parsed:
        return True
    if start and parsed < start:
        return False
    if end and parsed > end:
        return False
    return True


def year_range(start: datetime | None, end: datetime | None) -> str | None:
    if not start and not end:
        return None
    start_year = start.year if start else ""
    end_year = end.year if end else ""
    return f"{start_year}-{end_year}"


def keyword_match(paper: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = " ".join(
        [
            paper.get("title", ""),
            paper.get("abstract", ""),
            " ".join(paper.get("categories", [])),
        ]
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def arxiv_topic_query(topic: dict) -> str:
    keywords = topic.get("keywords") or []
    if not keywords:
        return topic.get("query", "all:security")
    return " OR ".join(f'all:"{keyword}"' for keyword in keywords)


def arxiv_date_query(start: datetime | None, end: datetime | None) -> str:
    if not start and not end:
        return ""
    start_text = (start or datetime(1970, 1, 1, tzinfo=timezone.utc)).strftime("%Y%m%d%H%M")
    end_text = (end or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M")
    return f"submittedDate:[{start_text} TO {end_text}]"


def request_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": "SecurityPaperReader/1.0 (+https://github.com/TingxuanHan/SecurityPaperReader)"}
    if "api.semanticscholar.org" in url:
        api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY")
        if api_key:
            headers["x-api-key"] = api_key
    return headers


def get_with_retries(url: str, attempts: int = 3) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                timeout=30,
                headers=request_headers(url),
            )
            if response.status_code in {429, 500, 502, 503, 504} and attempt < attempts:
                retry_after = response.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else attempt * 10
                time.sleep(min(delay, 30))
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 10)
    assert last_error is not None
    raise last_error


def fetch_arxiv(source: dict, topic: dict) -> list[dict]:
    start, end = resolve_date_range(source)
    query = source.get("query") or arxiv_topic_query(topic)
    date_query = arxiv_date_query(start, end)
    if date_query:
        query = f"({query}) AND {date_query}"
    params = {
        "search_query": query,
        "start": 0,
        "max_results": int(source.get("max_results", 20)),
        "sortBy": source.get("sort_by", "submittedDate"),
        "sortOrder": source.get("sort_order", "descending"),
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    response = get_with_retries(url)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(response.text)
    papers: list[dict] = []

    for entry in root.findall("atom:entry", ns):
        links = entry.findall("atom:link", ns)
        html_url = ""
        pdf_url = ""
        for link in links:
            href = link.attrib.get("href", "")
            if link.attrib.get("type") == "application/pdf":
                pdf_url = href
            elif link.attrib.get("rel") == "alternate":
                html_url = href

        arxiv_id = clean_text(entry.findtext("atom:id", namespaces=ns)).rsplit("/", 1)[-1]
        categories = [c.attrib.get("term", "") for c in entry.findall("atom:category", ns)]
        paper = {
            "id": f"arxiv:{arxiv_id}",
            "source": source["name"],
            "database": source.get("database", "arxiv"),
            "topic": topic.get("name", source.get("topic", "")),
            "title": clean_text(entry.findtext("atom:title", namespaces=ns)),
            "authors": [
                clean_text(author.findtext("atom:name", namespaces=ns))
                for author in entry.findall("atom:author", ns)
            ],
            "abstract": clean_text(entry.findtext("atom:summary", namespaces=ns)),
            "published": clean_text(entry.findtext("atom:published", namespaces=ns)),
            "updated": clean_text(entry.findtext("atom:updated", namespaces=ns)),
            "url": html_url or clean_text(entry.findtext("atom:id", namespaces=ns)),
            "pdf_url": pdf_url,
            "categories": [c for c in categories if c],
            "analysis": {},
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if in_date_range(paper["published"], start, end) and keyword_match(paper, topic.get("keywords", [])):
            papers.append(paper)

    return papers


def fetch_semantic_scholar(source: dict, topic: dict) -> list[dict]:
    start, end = resolve_date_range(source)
    venues = source.get("venue") or source.get("venues") or []
    if isinstance(venues, str):
        venues = [venues]
    if not venues:
        venues = [source.get("database", source["name"])]

    papers_by_id: dict[str, dict] = {}
    query = source.get("query") or topic.get("query") or "security"
    limit = int(source.get("max_results", 20))

    for venue in venues:
        params = {
            "query": query,
            "limit": limit,
            "fields": SEMANTIC_SCHOLAR_FIELDS,
            "venue": venue,
        }
        years = year_range(start, end)
        if years:
            params["year"] = years
        url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
        response = get_with_retries(url)
        for item in response.json().get("data", []):
            paper = semantic_scholar_paper(source, topic, item)
            if not in_date_range(paper.get("published"), start, end):
                continue
            if not keyword_match(paper, topic.get("keywords", [])):
                continue
            papers_by_id[paper["id"]] = paper
        time.sleep(1)

    return list(papers_by_id.values())[:limit]


def semantic_scholar_paper(source: dict, topic: dict, item: dict) -> dict:
    external_ids = item.get("externalIds") or {}
    paper_id = item.get("paperId") or external_ids.get("DOI") or item.get("url")
    open_access_pdf = item.get("openAccessPdf") or {}
    published = item.get("publicationDate") or (str(item["year"]) if item.get("year") else "")
    categories = item.get("fieldsOfStudy") or []
    categories.extend(field.get("category", "") for field in item.get("s2FieldsOfStudy") or [])
    categories = sorted({clean_text(category) for category in categories if clean_text(category)})
    return {
        "id": f"semantic_scholar:{paper_id}",
        "source": source["name"],
        "database": source.get("database", "semantic_scholar"),
        "topic": topic.get("name", source.get("topic", "")),
        "venue": item.get("venue") or source.get("database", ""),
        "title": clean_text(item.get("title")),
        "authors": [clean_text(author.get("name")) for author in item.get("authors", [])],
        "abstract": clean_text(item.get("abstract")),
        "published": published,
        "updated": "",
        "url": item.get("url") or "",
        "pdf_url": open_access_pdf.get("url") or "",
        "categories": categories,
        "analysis": {},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def merge_papers(existing: list[dict], incoming: list[dict], limit: int) -> list[dict]:
    by_id = {paper["id"]: paper for paper in existing}
    for paper in incoming:
        if paper["id"] in by_id:
            previous = by_id[paper["id"]]
            paper["analysis"] = previous.get("analysis", {})
            paper["fetched_at"] = previous.get("fetched_at", paper["fetched_at"])
        by_id[paper["id"]] = paper

    def sort_key(paper: dict) -> str:
        return paper.get("published") or paper.get("updated") or ""

    return sorted(by_id.values(), key=sort_key, reverse=True)[:limit]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    config = load_json(args.config, {})
    existing = load_json(args.output, [])
    incoming: list[dict] = []
    topics = topic_map(config)

    for source in config.get("sources", []):
        if not source.get("enabled", True):
            continue
        topic = resolve_topic(source, topics)
        try:
            if source.get("type") == "arxiv":
                incoming.extend(fetch_arxiv(source, topic))
                time.sleep(3)
            elif source.get("type") == "semantic_scholar":
                incoming.extend(fetch_semantic_scholar(source, topic))
            else:
                print(f"Unsupported source type: {source.get('type')}", file=sys.stderr)
        except requests.RequestException as exc:
            print(f"Failed to fetch {source.get('name', source.get('type'))}: {exc}", file=sys.stderr)

    limit = int(config.get("max_papers_per_run", 20))
    save_json(args.output, merge_papers(existing, incoming, limit))
    print(f"Saved {len(incoming)} fetched papers to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
