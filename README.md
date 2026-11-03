# CS59300_hw1 — Folio AI Research Assistant

A web application for finding, organizing, and understanding research papers.

[GitHub repository](https://github.com/lhxxh/CS59300_hw1)

## Features

- Search arXiv or Crossref for papers and view titles, authors, publication years, abstracts, and links.
- Save papers to a local SQLite library that persists after refreshes and restarts.
- Upload PDFs and extract their metadata and full text.
- Generate LLM summaries based on the paper's full text.
- Ask questions about a paper and inspect cited pages and source passages.

The interface and AI responses use English. The backend uses Flask, SQLite, and pypdf; the frontend uses HTML, CSS, and JavaScript.

## Setup and Run

Use Python 3.12 or 3.13. The following commands are for macOS/Linux.

### 1. Install dependencies

```bash
git clone https://github.com/lhxxh/CS59300_hw1.git
cd CS59300_hw1
python3.13 -m venv .venv-app
source .venv-app/bin/activate
pip install -r requirements.lock
cp .env.example .env
```

If using Python 3.12, replace `python3.13` with `python3.12`.

### 2. Configure an LLM

Edit `.env` with your provider's credentials. For Azure Responses:

```dotenv
LLM_API_STYLE=azure_responses
LLM_API_KEY=your-azure-key
LLM_BASE_URL=https://YOUR-RESOURCE.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview
LLM_MODEL=your-deployment-name
```

Use your actual Azure deployment name. Other provider examples are included in `.env.example`. Search, saving, and PDF upload work without an LLM key; summaries and questions require a configured model. Keep `.env` local; it is excluded from Git.

### 3. Start the application

With the virtual environment activated:

```bash
python app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop with `Ctrl+C`, and restart after changing `.env`.

In the website, search and save a paper or upload a PDF, then open **Summary** or **Ask AI**. Data is stored in `data/library.sqlite3` and `data/uploads/` and remains available when the application restarts.

## Project Structure

| Path | Purpose |
| --- | --- |
| `app.py` | Application entry point |
| `research_assistant/` | Flask API, database, external paper search, PDF processing, and LLM integration |
| `templates/`, `static/` | Web interface and browser interactions |
| `requirements.txt`, `requirements.lock` | Dependencies and exact package versions |
| `.env.example` | Configuration template without credentials |
| `tests/` | Workflow tests |

## Automated Tests and Coverage (HW2)

Tests run offline using temporary SQLite databases; external HTTP and model responses are mocked. They do not require `.env` or API keys. With the application dependencies installed as above:

```bash
python -m pip install 'coverage>=7,<8'
python -m unittest discover -s tests -v
python -m coverage run --branch --source=research_assistant -m unittest discover -s tests -v
python -m coverage report -m
```

The original HW1 checks are in `tests/test_workflow.py`; HW2 additions are in `tests/test_hw2_api.py`. Baseline, first-round, and final outputs are in `evidence/`. Coverage is the coverage.py combined statement-and-branch percentage for the Python backend only; it does not include browser JavaScript or prove correctness. HTTP/model mocking means the suite does not verify live providers or real model quality. The standalone PDF report documents the evaluation and iterations.
