#!/usr/bin/env python3
"""Fetch recent security papers from configured sources."""

from __future__ import annotations

import argparse
import json
import os
import random
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


def text_value(value) -> str:
    if isinstance(value, dict):
        return clean_text(value.get("value"))
    return clean_text(value)


def list_value(value) -> list:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


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


def years_in_range(start: datetime | None, end: datetime | None) -> list[int]:
    now = datetime.now(timezone.utc)
    start_year = start.year if start else now.year
    end_year = end.year if end else now.year
    return list(range(start_year, end_year + 1))


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


def normalize_authors(value) -> list[str]:
    authors = value
    if isinstance(authors, dict):
        authors = authors.get("author")
    if authors is None:
        return []
    if isinstance(authors, str):
        return [clean_text(authors)]
    if isinstance(authors, dict):
        return [clean_text(authors.get("text") or authors.get("#text") or authors.get("name"))]
    if isinstance(authors, list):
        names = []
        for author in authors:
            if isinstance(author, dict):
                names.append(clean_text(author.get("text") or author.get("#text") or author.get("name")))
            else:
                names.append(clean_text(str(author)))
        return [name for name in names if name]
    return []


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


def has_semantic_scholar_key() -> bool:
    return bool(os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY"))


def get_with_retries(url: str, attempts: int = 3) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                timeout=30,
                headers=request_headers(url),
            )
            if response.status_code == 429 and "api.semanticscholar.org" in url and not has_semantic_scholar_key():
                raise requests.HTTPError(
                    f"429 Too Many Requests from Semantic Scholar: {response.text[:300]}",
                    response=response,
                )
            if response.status_code in {429, 500, 502, 503, 504} and attempt < attempts:
                retry_after = response.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else attempt * 10
                time.sleep(min(delay, 30))
                continue
            if response.status_code >= 400:
                raise requests.HTTPError(
                    f"{response.status_code} error for {url}: {response.text[:300]}",
                    response=response,
                )
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

    params = {
        "query": query,
        "limit": limit,
        "fields": SEMANTIC_SCHOLAR_FIELDS,
        "venue": ",".join(venues),
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


def fetch_dblp(source: dict, topic: dict) -> list[dict]:
    start, end = resolve_date_range(source)
    stream = source.get("stream") or source.get("venue")
    if isinstance(stream, list):
        stream = stream[0]
    if not stream:
        stream = source.get("database", source["name"])

    papers_by_id: dict[str, dict] = {}
    limit = int(source.get("max_results", 50))
    keywords = topic.get("keywords", [])

    for year in years_in_range(start, end):
        params = {
            "q": f"stream:{stream}:{year}",
            "format": "json",
            "h": str(max(limit * 5, 100)),
        }
        url = "https://dblp.org/search/publ/api?" + urllib.parse.urlencode(params)
        try:
            response = get_with_retries(url)
        except requests.RequestException as exc:
            print(f"Failed to fetch DBLP {stream} {year}: {exc}", file=sys.stderr)
            continue
        hits = response.json().get("result", {}).get("hits", {}).get("hit", [])
        if isinstance(hits, dict):
            hits = [hits]
        for hit in hits:
            paper = dblp_paper(source, topic, hit)
            if not paper["title"] or paper["id"].endswith(f":{stream}/{year}"):
                continue
            if not in_date_range(paper.get("published"), start, end):
                continue
            if not keyword_match(paper, keywords):
                continue
            papers_by_id[paper["id"]] = paper
        time.sleep(1)

    return list(papers_by_id.values())[:limit]


def dblp_paper(source: dict, topic: dict, hit: dict) -> dict:
    info = hit.get("info", {})
    key = info.get("key") or hit.get("@id") or info.get("url")
    ee = info.get("ee") or ""
    if isinstance(ee, list):
        ee = ee[0] if ee else ""
    venue = info.get("venue") or source.get("database", "")
    if isinstance(venue, list):
        venue = " / ".join(clean_text(item) for item in venue)
    year = clean_text(str(info.get("year") or ""))
    return {
        "id": f"dblp:{key}",
        "source": source["name"],
        "database": source.get("database", "DBLP"),
        "topic": topic.get("name", source.get("topic", "")),
        "venue": clean_text(venue),
        "title": clean_text(info.get("title")),
        "authors": normalize_authors(info.get("authors")),
        "abstract": "",
        "published": year,
        "updated": "",
        "url": info.get("url") or "",
        "pdf_url": ee if str(ee).lower().endswith(".pdf") else "",
        "categories": ["dblp", source.get("database", "")],
        "analysis": {},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_openreview(source: dict, topic: dict) -> list[dict]:
    start, end = resolve_date_range(source)
    venue_template = source.get("venue_id") or source.get("venue")
    if isinstance(venue_template, list):
        venue_template = venue_template[0]
    if not venue_template:
        raise requests.RequestException(f"Missing OpenReview venue for {source['name']}")

    papers_by_id: dict[str, dict] = {}
    limit = int(source.get("max_results", 50))
    for year in years_in_range(start, end):
        venue_id = str(venue_template).replace("{year}", str(year))
        params = {
            "content.venueid": venue_id,
            "limit": str(max(limit * 5, 100)),
        }
        url = "https://api2.openreview.net/notes?" + urllib.parse.urlencode(params)
        try:
            response = get_with_retries(url)
        except requests.RequestException as exc:
            print(f"Failed to fetch OpenReview {venue_id}: {exc}", file=sys.stderr)
            continue
        for note in response.json().get("notes", []):
            paper = openreview_paper(source, topic, note, year)
            if not in_date_range(paper.get("published"), start, end):
                continue
            if not keyword_match(paper, topic.get("keywords", [])):
                continue
            papers_by_id[paper["id"]] = paper
        time.sleep(1)

    return list(papers_by_id.values())[:limit]


def openreview_paper(source: dict, topic: dict, note: dict, year: int) -> dict:
    content = note.get("content", {})
    note_id = note.get("id", "")
    title = text_value(content.get("title"))
    authors = [clean_text(author) for author in list_value(content.get("authors"))]
    abstract = text_value(content.get("abstract"))
    keywords = [clean_text(keyword) for keyword in list_value(content.get("keywords"))]
    return {
        "id": f"openreview:{note_id}",
        "source": source["name"],
        "database": source.get("database", "OpenReview"),
        "topic": topic.get("name", source.get("topic", "")),
        "venue": text_value(content.get("venue")) or source.get("database", ""),
        "title": title,
        "authors": [author for author in authors if author],
        "abstract": abstract,
        "published": str(year),
        "updated": "",
        "url": f"https://openreview.net/forum?id={note_id}",
        "pdf_url": f"https://openreview.net/pdf?id={note_id}" if note_id else "",
        "categories": [keyword for keyword in keywords if keyword],
        "analysis": {},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def safe_filename(value: str, max_length: int = 160) -> str:
    value = re.sub(r"[^A-Za-z0-9._ -]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    return (value or "paper")[:max_length]


def download_papers(papers: list[dict], paper_dir: Path, delay_max: int) -> None:
    paper_dir.mkdir(parents=True, exist_ok=True)
    for paper in papers:
        pdf_url = paper.get("pdf_url")
        if not pdf_url:
            continue
        year = (paper.get("published") or "unknown")[:4]
        filename = safe_filename(f"{paper.get('database', 'paper')}-{year}-{paper.get('title', '')}") + ".pdf"
        output = paper_dir / filename
        paper["local_pdf"] = str(output.relative_to(ROOT))
        if output.exists():
            continue
        delay = random.randint(0, max(0, delay_max))
        if delay:
            time.sleep(delay)
        response = get_with_retries(pdf_url)
        content_type = response.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not pdf_url.lower().endswith(".pdf"):
            print(f"Skip non-PDF response for {paper.get('title')}: {content_type}", file=sys.stderr)
            continue
        output.write_bytes(response.content)


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
    parser.add_argument("--download-pdfs", action="store_true")
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
            elif source.get("type") == "dblp":
                incoming.extend(fetch_dblp(source, topic))
            elif source.get("type") == "openreview":
                incoming.extend(fetch_openreview(source, topic))
            elif source.get("type") == "semantic_scholar":
                incoming.extend(fetch_semantic_scholar(source, topic))
            else:
                print(f"Unsupported source type: {source.get('type')}", file=sys.stderr)
        except requests.RequestException as exc:
            print(f"Failed to fetch {source.get('name', source.get('type'))}: {exc}", file=sys.stderr)

    if args.download_pdfs or config.get("download_pdfs"):
        paper_dir = ROOT / config.get("paper_dir", "paper")
        delay_max = int(config.get("download_delay_max_seconds", 100))
        download_papers(incoming, paper_dir, delay_max)

    limit = int(config.get("max_papers_per_run", 20))
    save_json(args.output, merge_papers(existing, incoming, limit))
    print(f"Saved {len(incoming)} fetched papers to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
