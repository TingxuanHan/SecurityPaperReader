# Security Paper Reader

GitHub Pages: https://tingxuanhan.github.io/SecurityPaperReader/

## 查看页面

打开：

```text
https://tingxuanhan.github.io/SecurityPaperReader/
```

页面源文件：

```text
docs/index.md
```

## 本地更新

安装依赖：

```bash
python -m pip install -r requirements.txt
```

抓取论文：

```bash
python scripts/fetch_papers.py
```

分析论文：

```bash
python scripts/analyze_papers.py
```

生成页面：

```bash
python scripts/build_page.py
```

不调用 Codex，只生成降级分析：

```bash
python scripts/analyze_papers.py --limit 80 --no-codex
python scripts/build_page.py
```

## GitHub 设置

Pages：

```text
Settings -> Pages -> Deploy from a branch -> main -> /docs
```

Secrets：

```text
OPENAI_API_KEY
SEMANTIC_SCHOLAR_API_KEY
```

Variables：

```text
CODEX_MODEL=gpt-5-mini
```

## 修改查询配置

编辑：

```text
config/sources.json
```

当前默认查询：

```text
arXiv: AI安全, 最近 14 天
CCS: AI安全, 最近 2 年
AAAI: AI安全, 最近 2 年
ICLR: AI安全, 最近 2 年
```

时间范围写法：

```json
{"days_back": 14}
```

```json
{"years_back": 2}
```

```json
{"start": "2024-01-01", "end": "2025-12-31"}
```

arXiv 配置：

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

会议配置：

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

## 自动更新

查看或修改：

```text
.github/workflows/daily-update.yml
```

默认运行时间：

```text
每天 06:30 Asia/Shanghai
```

手动运行：

```text
GitHub -> Actions -> Daily Paper Update -> Run workflow
```
