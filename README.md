# Security Paper Reader

Security Paper Reader 是一个自动更新的安全论文阅读看板。它会按配置抓取论文，调用 Codex 生成中文分析结果，然后把结果写入 GitHub Pages 使用的 Markdown 页面。

当前默认主题是 **AI 安全**：

- arXiv：最近 14 天。
- CCS：最近 2 年。
- AAAI：最近 2 年。
- ICLR：最近 2 年。

页面文件在 [`docs/index.md`](docs/index.md)。GitHub Pages 可以直接使用 `main` 分支的 `/docs` 目录发布。

## 仓库结构

```text
config/sources.json              # 数据库、主题、时间范围配置
data/papers.json                 # 抓取和分析后的论文数据
docs/index.md                    # GitHub Pages Markdown 页面
scripts/fetch_papers.py          # 抓取论文
scripts/analyze_papers.py        # 调用 Codex 或降级规则生成分析
scripts/build_page.py            # 从 data/papers.json 生成 docs/index.md
.github/workflows/daily-update.yml # 每日自动更新工作流
```

## 工作流程

1. `scripts/fetch_papers.py` 读取 `config/sources.json`，从 arXiv 和 Semantic Scholar 抓取论文。
2. `scripts/analyze_papers.py` 对新增论文生成摘要总结、创新点、推荐星级、推荐理由和标签。
3. `scripts/build_page.py` 把论文数据渲染成 `docs/index.md`。
4. GitHub Actions 每天北京时间 06:30 自动运行并提交更新。

如果环境里有 Codex CLI 和 `OPENAI_API_KEY`，分析脚本会优先调用 Codex。否则会写入规则降级结果，保证页面仍能更新。

## 本地运行

```bash
python -m pip install -r requirements.txt
python scripts/fetch_papers.py
python scripts/analyze_papers.py
python scripts/build_page.py
```

只想测试页面生成，不调用 Codex：

```bash
python scripts/analyze_papers.py --limit 80 --no-codex
python scripts/build_page.py
```

当前页面 Markdown 会生成到：

```text
docs/index.md
```

## GitHub 配置

在仓库设置中配置：

- Pages：选择 `Deploy from a branch`，分支为 `main`，目录为 `/docs`。
- Secrets：添加 `OPENAI_API_KEY`，用于 GitHub Actions 中调用 Codex。
- Secrets：可选添加 `SEMANTIC_SCHOLAR_API_KEY`，用于提高 CCS/AAAI/ICLR 查询稳定性。
- Variables：可选添加 `CODEX_MODEL`，默认使用 `gpt-5-mini`。

## 配置数据库、主题和时间范围

核心配置文件是 [`config/sources.json`](config/sources.json)。

`topics` 定义主题名称、检索 query 和关键词过滤；`sources` 定义每个数据库如何查询，包括数据库类型、主题、venue、时间范围和每次最多返回数量。

时间范围支持：

- `{"days_back": 14}`：从运行时间往前 14 天。
- `{"years_back": 2}`：从运行时间往前 2 年。
- `{"start": "2024-01-01", "end": "2025-12-31"}`：固定日期范围。

arXiv 示例：

```json
{
  "name": "arXiv AI安全 最近两周",
  "type": "arxiv",
  "database": "arxiv",
  "enabled": true,
  "topic": "AI安全",
  "date_range": {
    "days_back": 14
  },
  "max_results": 20,
  "sort_by": "submittedDate",
  "sort_order": "descending"
}
```

会议示例，当前通过 Semantic Scholar 按 venue 查询：

```json
{
  "name": "ICLR AI安全 最近两年",
  "type": "semantic_scholar",
  "database": "ICLR",
  "enabled": true,
  "topic": "AI安全",
  "venue": [
    "ICLR",
    "International Conference on Learning Representations"
  ],
  "date_range": {
    "years_back": 2
  },
  "max_results": 20
}
```

## 当前状态

本地已经抓到 arXiv 最近两周 AI 安全相关论文，并写入 [`data/papers.json`](data/papers.json)。CCS、AAAI、ICLR 当前依赖 Semantic Scholar，若遇到 429 限流，建议配置 `SEMANTIC_SCHOLAR_API_KEY` 后在 GitHub Actions 中运行。
