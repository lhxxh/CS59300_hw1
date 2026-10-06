# CS59300_hw1 — Folio AI Research Assistant

A complete, single-user research workspace for CS 59300 Homework 1. Search papers, keep a persistent local library, upload PDFs, generate full-text summaries, and ask questions with page citations.

**本机快速启动：** 依赖已安装在 `.venv-app`（Python 3.13）里。运行 `./run.sh`，访问 http://127.0.0.1:8000 。在项目的 `.env` 中填写模型 API key 后重启，才能使用总结和问答。原来的 Python 3.14 `.venv` 保留不动。

## Features

| Homework requirement | Implementation |
| --- | --- |
| Graphical interface, frontend and backend | Responsive HTML/CSS/JavaScript UI and Flask JSON API |
| External paper search | Official arXiv API; explicit Crossref fallback; title, authors, year, abstract and paper link |
| Persistent local library | SQLite stores paper metadata, extracted pages, summaries, analysis jobs and conversations |
| PDF upload and processing | pypdf extracts page text, title, authors, year and abstract; review/edit detected metadata |
| LLM summaries | Real Azure Responses, OpenAI-compatible or Ollama API; summaries use PDF text rather than metadata |
| Questions about a paper | BM25-style passage retrieval, conversation context, page citations and inspectable source excerpts |

The application does not generate placeholder AI answers. If a key is missing, an upstream API fails, or a PDF has no readable text, it shows an actionable error.

## Setup and run

Use Python **3.12 or 3.13**. Python 3.13 was used to verify this project. No Node build, vector database, torch, or local embedding model is required. Node is only needed for the optional JavaScript syntax check.

### Option A: automatic launcher

```bash
./run.sh
```

The launcher creates `.venv-app` if necessary, installs the exact dependency versions from `requirements.lock`, creates `.env` from `.env.example` if missing, and starts the application. It prefers `uv` when installed, and otherwise uses Python's `venv` and `pip`.

Open **http://127.0.0.1:8000**. Stop the server with Ctrl+C. Run the same command again to restart; your library is retained.

### Option B: manual setup with uv

```bash
uv venv --python python3.13 .venv-app
uv pip install --link-mode=copy --python .venv-app/bin/python -r requirements.lock
cp .env.example .env  # only if .env does not exist yet
.venv-app/bin/python app.py
```

Without uv:

```bash
python3.13 -m venv .venv-app
.venv-app/bin/python -m pip install -r requirements.lock
cp .env.example .env  # only if .env does not exist yet
.venv-app/bin/python app.py
```

On Windows, use `.venv-app\Scripts\python.exe` in place of `.venv-app/bin/python`. The Bash launcher is intended for macOS/Linux. For local Windows use, install the other entries in `requirements.lock` without Gunicorn, which is Unix-only; the Flask entry point still works.

## Connect a model

Edit the local `.env` file, then **restart the server**. The UI's “Connect your AI” panel shows configuration status; actual connectivity is verified by a successful analysis. Keys stay on the server and are never returned by `/api/config`.

### OpenAI

```dotenv
LLM_API_STYLE=openai
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1-mini
```

Use a Chat Completions model that is available to your API account. Your ChatGPT/Codex subscription does not configure this application's API credentials.

### DeepSeek or another OpenAI-compatible service

```dotenv
LLM_API_STYLE=openai
LLM_API_KEY=your-provider-key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
```

For this OpenAI-compatible style, the base URL should end at the API prefix; the application appends `/chat/completions`. Set the model name and prefix according to your provider. No provider SDK is required.

### Azure OpenAI Responses

```dotenv
LLM_API_STYLE=azure_responses
LLM_API_KEY=your-azure-key
LLM_BASE_URL=https://YOUR-RESOURCE.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview
LLM_MODEL=your-deployment-name
LLM_MAX_OUTPUT_TOKENS=12000
```

Use the **complete Responses endpoint** supplied by Azure, including its query string. `LLM_MODEL` is the actual deployment name, which can differ from the underlying model ID. This adapter sends an `api-key` header and parses `output_text` items from the Responses result; it does not append a Chat Completions route. It sets `store=false`, while the local SQLite library still retains your summary and conversation.

For reasoning deployments that support it, optionally set `LLM_REASONING_EFFORT=low` to reduce generation time. An incomplete response is reported as an error rather than stored as a complete summary.

You can make a tiny real connection check, which consumes a small amount of provider API usage:

```bash
.venv-app/bin/python scripts/verify_model.py
```

### Ollama

Install and start Ollama separately, pull a model that fits your computer, and configure its native chat endpoint:

```bash
ollama pull qwen3:8b
```

```dotenv
LLM_API_STYLE=ollama
LLM_API_KEY=
LLM_BASE_URL=http://127.0.0.1:11434
LLM_MODEL=qwen3:8b
```

In Docker, use `http://host.docker.internal:11434` to reach Ollama running on the macOS/Windows host. The selected model must support the requested context size; generation speed depends on your hardware.

The website interface, summaries, and AI answers use **English**. Earlier conversations in other languages remain in SQLite but are not displayed in the English conversation view. Search and PDF ingestion work without a model connection. Summarizing or asking a question sends the relevant paper text to the configured model service.

## Workflow

1. **Discover:** enter keywords, a title, or an arXiv ID such as `1706.03762`. Choose relevance/newest and arXiv/Crossref. Searches are cached for ten minutes, and requests to arXiv are spaced by at least three seconds within this application process.
2. **Save:** click Save paper. Saving the same external record twice reuses the existing library item.
3. **Upload:** choose or drop a PDF. Review the automatically detected metadata. You can also attach a PDF to an existing saved paper from its upload icon.
4. **Summarize:** select a saved paper and open Summary. arXiv PDFs are downloaded lazily when analysis begins; Crossref records require an attached PDF. All readable pages are included, with chunked notes and consolidation for long papers. Summaries are saved and cached per model/language.
5. **Ask:** open Ask AI, type a question, and click Ask AI. Relevant overlapping passages are retrieved using lexical scoring; recent questions/answers provide conversational context. Click a page citation to inspect the source, or expand source passages.

Replacing a PDF invalidates that paper's existing summary and conversation so that old analysis is not presented as evidence for the new document. The original metadata remains editable.

## Architecture

```text
Browser: HTML + CSS + JavaScript
    │ same-origin JSON API / multipart PDF upload
    ▼
Flask backend ───────────► arXiv / Crossref
    │
    ├── SQLite: papers, extracted pages, messages, jobs, search cache
    ├── pypdf: PDF text and heuristic metadata extraction
    └── background executor
            ├── full-text / chunked summarization
            └── lexical passage retrieval → configured LLM API
```

| File | Responsibility |
| --- | --- |
| `app.py` | Local server and Gunicorn WSGI entry point |
| `research_assistant/__init__.py` | API routes, validation, uploads and analysis job coordination |
| `research_assistant/db.py` | SQLite schema, transactions and paper serialization |
| `research_assistant/search.py` | Official external search APIs and fallback handling |
| `research_assistant/pdfs.py` | PDF text, metadata extraction and trusted arXiv downloads |
| `research_assistant/llm.py` | Model HTTP requests, summarization and question retrieval |
| `templates/index.html`, `static/` | UI layout, interactions and safe Markdown rendering |
| `tests/test_workflow.py` | Focused offline workflow checks |
| `scripts/verify_live.py` | Optional real API/PDF verification and demo PDF download |
| `docs/DEMO.md` | Demo recording sequence and submission checklist |
| `docs/REFLECTION_NOTES.md` | Factual development notes and reflection prompts |

By default, data lives in `data/library.sqlite3` and `data/uploads/`. Set `DATA_DIR` to change the destination. Back up the entire data directory while the server is stopped, including the PDFs. The project ignores `.env`, both virtual environments, runtime data, caches and temporary downloads in Git.

## Validation

Run the focused checks without contacting external services or spending API credits:

```bash
.venv-app/bin/python -m unittest discover -s tests -v
```

These verify persistence across application instances, duplicate saves/uploads, PDF metadata/text extraction, footnote handling, metadata edits, source retrieval, full-paper coverage, model HTTP request shape, cached summaries, conversation persistence, failed model authentication, origin validation, and deletion. Upstream services are mocked at the HTTP boundary in offline tests. This does **not** verify live model quality or account credentials.

Eleven offline checks passed, including preservation of earlier conversations during the language-field migration. The browser workflow was also verified with a real Azure Responses deployment named `gpt-5.4-nano`: an English summary of all fifteen pages of *Attention Is All You Need*, a question distinguishing translation and parsing datasets, and clickable source-page verification. Papers, summary, and conversation were verified again after a server restart. This is validation on one example paper, not a guarantee that every model answer or PDF extraction is correct.

The recorded English HW1 demonstration is saved locally in `output/video/folio-hw1-demo-en.mp4`, with English step captions, an SRT file, and a requirement coverage manifest. See `docs/DEMO.md` for timestamps. Runtime output is ignored by Git; submit the video separately on Brightspace.

Optional live search/PDF check:

```bash
.venv-app/bin/python scripts/verify_live.py
```

It calls arXiv and Crossref and downloads the public *Attention Is All You Need* PDF to `tmp/pdfs/attention-is-all-you-need.pdf`. It makes no LLM calls. That real PDF was used to check the detected title, all eight authors, the 2017 publication year, abstract boundaries, and fifteen extracted pages.

## Deployment

### Docker with persistent data

```bash
docker compose up --build -d
```

The app is available at http://127.0.0.1:8000. A named volume preserves SQLite and PDFs across container restarts/rebuilds. `docker compose down` retains the volume; adding `--volumes` removes the library. This configuration uses one Gunicorn worker and four request threads; the background executor is owned by that process.

For an HTTPS hosting platform or VPS, build `Dockerfile`, expose internal port 8000, configure environment variables in the platform, and mount a **persistent volume at `/app/data`**. Run only one replica/worker for this SQLite single-user application. Configure `APP_USERNAME` and `APP_PASSWORD` before exposing the library to the internet, and terminate HTTPS at the platform/reverse proxy. The health-check endpoint is `/api/health`.

You can also run Gunicorn directly on macOS/Linux:

```bash
.venv-app/bin/gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 4 --timeout 300 app:app
```

The Docker deployment configuration is supplied; an external hosting account and public URL are not created by this repository.

## Troubleshooting and limits

- **Slow environment install:** the initial environment on this machine used Python 3.14 and only contained pip. Python 3.13 plus uv installed this project's 18 packages in under a second after permissions were granted. This is an observation for these dependencies, not proof of the original slowdown's cause. Avoid unnecessary torch/transformers/vector-store dependencies. `--link-mode=copy` avoids cross-filesystem cloning warnings.
- **uv panic under Codex:** macOS sandbox restrictions caused a `system-configuration` error during uv startup here. Running the approved setup command outside the sandbox worked. The usual terminal command does not run inside that Codex sandbox.
- **Cannot start localhost:** the Codex command sandbox may block binding a port; grant permission for the server command. The application listens only on localhost by default.
- **Missing metadata:** extraction is heuristic because PDFs have no uniform title/author/abstract structure. Unknown years/authors are shown explicitly, and the review dialog allows correction.
- **Scanned PDF:** OCR is not included. Upload a PDF with a selectable text layer or an OCR-processed copy. Limits: 20 MB, 120 pages, 400,000 extracted characters.
- **Search/download fails:** APIs can be unavailable or rate-limited. Automatic mode labels Crossref fallback. Attach a locally downloaded PDF if arXiv retrieval fails; the app never substitutes an abstract for the full paper.
- **Question misses evidence:** lexical retrieval is deliberately lightweight and can miss semantic matches. Rephrase with paper terminology; Chinese question keywords are expanded for common dataset/method/limitation questions. The model is asked to admit when supplied passages are insufficient.
- **Equations/tables:** PDF extraction can lose mathematical notation and column order. Check cited pages in the original PDF. Page labels refer to PDF page positions, starting at 1.
- **AI fails:** check `.env`, restart, and verify the provider's model access, quota and network. Errors do not save a fabricated summary. An interrupted job is marked failed on restart and can be retried.

## API references

- [arXiv API User's Manual](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/user-manual.md)
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [DeepSeek model names and API base URL](https://api-docs.deepseek.com/quick_start/pricing/)
- [Ollama chat API](https://docs.ollama.com/api/chat)
- [Azure OpenAI Responses](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses)

See `docs/DEMO.md` for the remaining GitHub, demo video, and reflection submission materials.
