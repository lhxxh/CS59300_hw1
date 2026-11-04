# CS 59300 — Folio AI Research Assistant

This repository contains my HW1 web application and the automated testing work for HW2. The application source remains the subject of the tests; the HW2 test suite and report are stored alongside it.

- [HW1: Application](#hw1-application)
- [HW2: Test Generation and Evaluation](#hw2-test-generation-and-evaluation)
- [Repository Layout](#repository-layout)

## HW1: Application

Folio is a web application for finding, organizing, and understanding research papers. It can:

- Search arXiv or Crossref and display titles, authors, publication years, abstracts, and links.
- Save papers in a local SQLite library that persists across restarts.
- Upload a PDF and extract its metadata and full text.
- Generate a summary or answer questions using an LLM, with cited pages and passages.

The backend uses Flask, SQLite, and pypdf; the frontend uses HTML, CSS, and JavaScript.

### Install and run the application

Use Python 3.12 or 3.13. The following commands are for macOS/Linux:

```bash
git clone https://github.com/lhxxh/CS59300_hw1.git
cd CS59300_hw1
python3.13 -m venv .venv-app
source .venv-app/bin/activate
python -m pip install -r requirements.lock
cp .env.example .env
```

If using Python 3.12, replace `python3.13` with `python3.12`. To use summaries and questions, configure an LLM in `.env`. For Azure Responses, for example:

```dotenv
LLM_API_STYLE=azure_responses
LLM_API_KEY=your-azure-key
LLM_BASE_URL=https://YOUR-RESOURCE.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview
LLM_MODEL=your-deployment-name
```

Use your actual deployment name; `.env.example` contains other provider examples. Search, saving, and PDF upload do not require an LLM key. Keep `.env` local; it is excluded from Git.

With the virtual environment activated, start the application:

```bash
python app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop with `Ctrl+C`, and restart after changing `.env`. The local library and uploaded files are stored under `data/`.

## HW2: Test Generation and Evaluation

For HW2, Codex generated an initial test suite and improved it in two rounds. The evaluation retains the 11 HW1 workflow tests and adds 11 HW2 API test methods. On Python 3.13.12, the initial suite passed 15 tests with 73% combined backend statement-and-branch coverage; the final suite passed 22 with 82%. No application bugs were detected by these runs. See the [HW2 report](report/hw2-report.pdf) for the approach, results, limitations, and reflection.

### Run tests and reproduce coverage

First install the application dependencies using the HW1 setup above and activate the environment. Then install coverage.py and run:

```bash
python -m pip install 'coverage>=7,<8'
python -m unittest discover -s tests -v
python -m coverage run --branch --source=research_assistant -m unittest discover -s tests -v
python -m coverage report -m
```

The tests use temporary SQLite databases and mock external HTTP/model responses. They do not require API keys or live services. Coverage measures the Python backend, not browser JavaScript; mocking also leaves real providers and model output quality unverified. Exact timing and coverage may vary by environment.

The original workflow checks are in `tests/test_workflow.py`; the HW2 additions are in `tests/test_hw2_api.py`. The `evidence/` directory contains the recorded initial, first-round, and final test and coverage outputs, along with the ARIS evidence check. The report source is in `report/hw2-report.tex`.

## Repository Layout

| Path | Assignment | Purpose |
| --- | --- | --- |
| `app.py`, `research_assistant/` | HW1 | Flask application and backend modules |
| `templates/`, `static/` | HW1 | Web interface and browser interactions |
| `requirements.lock`, `.env.example` | HW1 | Application dependencies and configuration template |
| `tests/test_workflow.py` | HW1 | Existing workflow checks, retained for HW2 evaluation |
| `tests/test_hw2_api.py` | HW2 | Agent-generated and iteratively improved API tests |
| `evidence/` | HW2 | Test runs, coverage reports, and evidence check |
| `report/` | HW2 | Report PDF and editable LaTeX source |
