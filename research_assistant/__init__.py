"""Flask API and background full-text analysis jobs."""
import hmac
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from . import db
from .llm import LLM, LLMError, answer, summarize
from .pdfs import MAX_BYTES, PDFError, download_arxiv_pdf, extract_pdf
from .search import ARXIV_ID, SearchError, search

ROOT = Path(__file__).resolve().parent.parent


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def create_app(config=None):
    load_dotenv(ROOT / ".env")
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    app.config.update(
        DATA_DIR=os.getenv("DATA_DIR", str(ROOT / "data")),
        MAX_CONTENT_LENGTH=MAX_BYTES + 1024 * 1024,
        LLM_API_STYLE=os.getenv("LLM_API_STYLE", "openai"),
        LLM_API_KEY=os.getenv("LLM_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""),
        LLM_BASE_URL=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        LLM_MODEL=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
        LLM_MAX_OUTPUT_TOKENS=os.getenv("LLM_MAX_OUTPUT_TOKENS", "12000"),
        LLM_REASONING_EFFORT=os.getenv("LLM_REASONING_EFFORT", ""),
        CROSSREF_EMAIL=os.getenv("CROSSREF_EMAIL", ""),
        APP_USERNAME=os.getenv("APP_USERNAME", ""), APP_PASSWORD=os.getenv("APP_PASSWORD", ""),
    )
    if config:
        app.config.update(config)
    data_dir = Path(app.config["DATA_DIR"]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir = data_dir / "uploads"
    upload_dir.mkdir(exist_ok=True)
    database = str(data_dir / "library.sqlite3")
    db.initialize(database)
    llm = LLM(app.config)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="paper-analysis")
    job_lock = threading.Lock()
    app.extensions.update(llm=llm, executor=executor, database=database)

    def get_paper(identifier, detail=True):
        with db.connect(database) as conn:
            row = conn.execute("SELECT * FROM papers WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise APIError("This paper was not found in your library.", 404)
        return db.serialize_paper(row, detail=detail)

    def active_job(conn, identifier):
        return conn.execute("SELECT id FROM jobs WHERE paper_id=? AND status IN ('queued', 'running')",
                            (identifier,)).fetchone()

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise APIError("Expected a JSON object.")
        return value

    def validate_metadata(value):
        title = value.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 400:
            raise APIError("Enter a paper title between 1 and 400 characters.")
        authors = value.get("authors", [])
        if not isinstance(authors, list) or len(authors) > 100 or any(not isinstance(a, str) or len(a) > 200 for a in authors):
            raise APIError("Authors must be a list of names (up to 100).")
        year = value.get("year")
        if year in (None, ""):
            year = None
        elif not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2100:
            raise APIError("Publication year must be between 1900 and 2100, or left blank.")
        abstract = value.get("abstract", "")
        if not isinstance(abstract, str) or len(abstract) > 20000:
            raise APIError("The abstract must contain at most 20,000 characters.")
        return {"title": title.strip(), "authors": [a.strip() for a in authors if a.strip()],
                "year": year, "abstract": abstract.strip()}

    @app.before_request
    def access_control():
        if request.path == "/api/health":
            return None
        username, password = app.config["APP_USERNAME"], app.config["APP_PASSWORD"]
        if username or password:
            auth = request.authorization
            if not (username and password and auth and
                    hmac.compare_digest(auth.username or "", username) and
                    hmac.compare_digest(auth.password or "", password)):
                return jsonify(error="Sign in to access this research library."), 401, {"WWW-Authenticate": 'Basic realm="Research library"'}
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                raise APIError("Requests must come from this application's origin.", 403)
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                raise APIError("Cross-site requests are not allowed.", 403)

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(APIError)
    def api_error(exc):
        return jsonify(error=exc.message), exc.status

    @app.errorhandler(PDFError)
    def pdf_error(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(LLMError)
    def llm_error(exc):
        return jsonify(error=str(exc)), 503

    @app.errorhandler(SearchError)
    def search_error(exc):
        return jsonify(error=str(exc)), 502

    @app.errorhandler(HTTPException)
    def http_error(exc):
        message = "File too large. The PDF limit is 20 MB." if exc.code == 413 else exc.description
        return jsonify(error=message), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        app.logger.exception("Unhandled application error")
        return jsonify(error="An unexpected server error occurred. Check the server log and try again."), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok")

    @app.get("/api/config")
    def public_config():
        return jsonify(llm_configured=llm.configured, model=llm.model,
                       provider=llm.style, max_upload_mb=20)

    @app.get("/api/search")
    def search_papers():
        query = request.args.get("q", "").strip()
        source = request.args.get("source", "auto")
        sort = request.args.get("sort", "relevance")
        try:
            start = int(request.args.get("start", "0"))
        except ValueError as exc:
            raise APIError("Invalid result offset.") from exc
        if not 2 <= len(query) <= 250 or not re.search(r"\w", query):
            raise APIError("Enter 2–250 characters of keywords, a title, or an arXiv ID.")
        if source not in ("auto", "arxiv", "crossref") or sort not in ("relevance", "newest") or not 0 <= start <= 9900:
            raise APIError("Invalid search source, sort order, or result offset.")
        key = json.dumps([query.lower(), source, sort, start])
        with db.connect(database) as conn:
            cached = conn.execute("SELECT payload FROM search_cache WHERE cache_key=? AND expires_at>?", (key, time.time())).fetchone()
        result = json.loads(cached[0]) if cached else search(query, source, start, sort, app.config["CROSSREF_EMAIL"])
        if not cached:
            with db.connect(database) as conn:
                conn.execute("DELETE FROM search_cache WHERE expires_at<?", (time.time(),))
                conn.execute("INSERT OR REPLACE INTO search_cache VALUES (?, ?, ?)", (key, json.dumps(result), time.time() + 600))
        with db.connect(database) as conn:
            saved = {row["external_id"]: row["id"] for row in conn.execute("SELECT id, external_id FROM papers WHERE external_id IS NOT NULL")}
        for paper in result["papers"]:
            paper["saved_id"] = saved.get(paper["external_id"])
        return jsonify(result)

    @app.get("/api/papers")
    def list_papers():
        query = request.args.get("q", "").strip().lower()
        with db.connect(database) as conn:
            papers = [db.serialize_paper(row) for row in conn.execute("SELECT * FROM papers ORDER BY created_at DESC")]
        if query:
            papers = [p for p in papers if query in (p["title"] + " " + " ".join(p["authors"]) + " " + p["abstract"]).lower()]
        return jsonify(papers=papers)

    @app.post("/api/papers")
    def save_paper():
        value = body()
        metadata = validate_metadata(value)
        external = value.get("external_id", "")
        source = value.get("source")
        if source == "arxiv" and isinstance(external, str) and external.startswith("arxiv:") and ARXIV_ID.fullmatch(external[6:]):
            identifier = re.sub(r"v\d+$", "", external[6:])
            external, url, pdf_url = "arxiv:" + identifier, "https://arxiv.org/abs/" + identifier, "https://arxiv.org/pdf/" + identifier
        elif source == "crossref" and isinstance(external, str) and re.fullmatch(r"doi:10\.\d{4,9}/\S{1,250}", external):
            external = external.lower()
            url, pdf_url = "https://doi.org/" + external[4:], ""
        else:
            raise APIError("Save a valid paper from the search results.")
        identifier = uuid.uuid4().hex
        with db.connect(database) as conn:
            conn.execute("""INSERT OR IGNORE INTO papers
                         (id, external_id, title, authors, year, abstract, url, pdf_url, source, created_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (identifier, external, metadata["title"], json.dumps(metadata["authors"]), metadata["year"],
                          metadata["abstract"], url, pdf_url, source, db.now()))
            row = conn.execute("SELECT * FROM papers WHERE external_id=?", (external,)).fetchone()
        return jsonify(paper=db.serialize_paper(row), duplicate=row["id"] != identifier), 200 if row["id"] != identifier else 201

    @app.get("/api/papers/<identifier>")
    def paper_detail(identifier):
        paper = get_paper(identifier)
        with db.connect(database) as conn:
            messages = []
            for row in conn.execute("SELECT * FROM messages WHERE paper_id=? ORDER BY id", (identifier,)):
                message = dict(row)
                message["sources"] = json.loads(message["sources"])
                messages.append(message)
            job = conn.execute("SELECT id, kind, status, progress FROM jobs WHERE paper_id=? AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1", (identifier,)).fetchone()
        return jsonify(paper=paper, messages=messages, active_job=dict(job) if job else None)

    @app.patch("/api/papers/<identifier>")
    def edit_paper(identifier):
        metadata = validate_metadata(body())
        get_paper(identifier)
        with db.connect(database) as conn:
            conn.execute("UPDATE papers SET title=?, authors=?, year=?, abstract=? WHERE id=?",
                         (metadata["title"], json.dumps(metadata["authors"]), metadata["year"], metadata["abstract"], identifier))
        return jsonify(paper=get_paper(identifier, False))

    @app.delete("/api/papers/<identifier>")
    def delete_paper(identifier):
        with job_lock, db.connect(database) as conn:
            row = conn.execute("SELECT filename FROM papers WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise APIError("Paper not found.", 404)
            if active_job(conn, identifier):
                raise APIError("Wait for the current analysis to finish before removing this paper.", 409)
            conn.execute("DELETE FROM papers WHERE id=?", (identifier,))
        if row["filename"]:
            (upload_dir / row["filename"]).unlink(missing_ok=True)
        return jsonify(deleted=True)

    @app.post("/api/upload")
    def upload():
        file = request.files.get("file")
        if not file or not file.filename or not file.filename.lower().endswith(".pdf"):
            raise APIError("Choose a PDF file to upload.")
        data = file.read(MAX_BYTES + 1)
        extracted = extract_pdf(data, secure_filename(file.filename) or "paper.pdf")
        target = request.form.get("paper_id", "").strip()
        with job_lock, db.connect(database) as conn:
            if target:
                existing = conn.execute("SELECT * FROM papers WHERE id=?", (target,)).fetchone()
                if not existing:
                    raise APIError("Paper not found.", 404)
                if active_job(conn, target):
                    raise APIError("Wait for the analysis to finish before replacing the PDF.", 409)
            else:
                duplicate = conn.execute("SELECT * FROM papers WHERE content_hash=?", (extracted["content_hash"],)).fetchone()
                if duplicate:
                    return jsonify(paper=db.serialize_paper(duplicate), duplicate=True,
                                   message="This PDF is already in your library."), 200
            identifier = target or uuid.uuid4().hex
            filename = uuid.uuid4().hex + ".pdf"
            (upload_dir / filename).write_bytes(data)
            if target:
                conn.execute("""UPDATE papers SET filename=?, pages=?, content_hash=?,
                             summary=NULL, summary_model=NULL, summary_language=NULL WHERE id=?""",
                             (filename, json.dumps(extracted["pages"]), extracted["content_hash"], identifier))
                conn.execute("DELETE FROM messages WHERE paper_id=?", (identifier,))
            else:
                meta = extracted["metadata"]
                conn.execute("""INSERT INTO papers
                             (id, title, authors, year, abstract, source, filename, pages, content_hash, created_at)
                             VALUES (?, ?, ?, ?, ?, 'upload', ?, ?, ?, ?)""",
                             (identifier, meta["title"], json.dumps(meta["authors"]), meta["year"], meta["abstract"],
                              filename, json.dumps(extracted["pages"]), extracted["content_hash"], db.now()))
        if target and existing["filename"]:
            (upload_dir / existing["filename"]).unlink(missing_ok=True)
        return jsonify(paper=get_paper(identifier, False), duplicate=False,
                       message="PDF extracted. Review the detected metadata; missing fields can be filled in."), 201

    @app.get("/api/papers/<identifier>/pdf")
    def original_pdf(identifier):
        get_paper(identifier, False)
        with db.connect(database) as conn:
            row = conn.execute("SELECT filename FROM papers WHERE id=?", (identifier,)).fetchone()
        if not row["filename"] or not (upload_dir / row["filename"]).is_file():
            raise APIError("No local PDF is available. Attach a PDF first.", 404)
        return send_file(upload_dir / row["filename"], mimetype="application/pdf", download_name="paper.pdf")

    def update_job(job_id, **values):
        with db.connect(database) as conn:
            conn.execute("UPDATE jobs SET " + ", ".join(f"{name}=?" for name in values) + " WHERE id=?",
                         (*values.values(), job_id))

    def run_analysis(job_id, identifier, kind, question, language):
        try:
            update_job(job_id, status="running", progress="Preparing the paper…")
            paper = get_paper(identifier)
            if not paper["has_content"]:
                update_job(job_id, progress="Downloading and extracting the full PDF…")
                data = download_arxiv_pdf(paper["external_id"] or "")
                extracted = extract_pdf(data)
                filename = uuid.uuid4().hex + ".pdf"
                (upload_dir / filename).write_bytes(data)
                with db.connect(database) as conn:
                    conn.execute("UPDATE papers SET pages=?, filename=?, content_hash=? WHERE id=?",
                                 (json.dumps(extracted["pages"]), filename, extracted["content_hash"], identifier))
                paper = get_paper(identifier)
            if kind == "summary":
                result = {"summary": summarize(llm, paper, lambda message: update_job(job_id, progress=message), language),
                          "model": llm.model, "language": language}
                with db.connect(database) as conn:
                    conn.execute("UPDATE papers SET summary=?, summary_model=?, summary_language=? WHERE id=?",
                                 (result["summary"], llm.model, language, identifier))
            else:
                update_job(job_id, progress="Finding relevant passages and answering…")
                with db.connect(database) as conn:
                    history = [dict(row) for row in conn.execute("SELECT question, answer FROM messages WHERE paper_id=? AND language=? ORDER BY id DESC LIMIT 3", (identifier, language))][::-1]
                result = answer(llm, paper, question, history, language)
                result.update(question=question, model=llm.model, language=language, created_at=db.now())
                with db.connect(database) as conn:
                    cursor = conn.execute("INSERT INTO messages (paper_id, question, answer, sources, model, created_at, language) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                          (identifier, question, result["answer"], json.dumps(result["sources"]), llm.model, result["created_at"], language))
                    result["id"] = cursor.lastrowid
            update_job(job_id, status="completed", progress="Complete", result=json.dumps(result))
        except (PDFError, LLMError, APIError) as exc:
            update_job(job_id, status="failed", progress="Could not complete analysis", error=str(getattr(exc, "message", exc)))
        except Exception:
            app.logger.exception("Paper analysis failed")
            update_job(job_id, status="failed", progress="Could not complete analysis", error="An unexpected analysis error occurred. Check the server log and retry.")

    def start_analysis(identifier, kind):
        llm.check()
        value = body()
        language = value.get("language", "en")
        if language not in ("en", "zh"):
            raise APIError("Choose English or Chinese for the response language.")
        paper = get_paper(identifier)
        question = value.get("question", "")
        if kind == "question" and (not isinstance(question, str) or not question.strip() or len(question) > 2000):
            raise APIError("Enter a question between 1 and 2,000 characters.")
        if kind == "summary" and paper["summary"] and paper["summary_model"] == llm.model and paper["summary_language"] == language and not value.get("refresh"):
            return jsonify(cached=True, result={"summary": paper["summary"], "model": llm.model, "language": language})
        if not paper["has_content"] and paper["source"] != "arxiv":
            raise APIError("Attach the full paper PDF before asking the AI to analyze it.", 422)
        job_id = uuid.uuid4().hex
        with job_lock, db.connect(database) as conn:
            if active_job(conn, identifier):
                raise APIError("An analysis is already running for this paper.", 409)
            conn.execute("INSERT INTO jobs (id, paper_id, kind, status, progress, created_at) VALUES (?, ?, ?, 'queued', 'Waiting to start…', ?)",
                         (job_id, identifier, kind, db.now()))
        executor.submit(run_analysis, job_id, identifier, kind, question.strip(), language)
        return jsonify(job_id=job_id, cached=False), 202

    @app.post("/api/papers/<identifier>/summary")
    def summary(identifier):
        return start_analysis(identifier, "summary")

    @app.post("/api/papers/<identifier>/questions")
    def question(identifier):
        return start_analysis(identifier, "question")

    @app.get("/api/jobs/<job_id>")
    def job(job_id):
        with db.connect(database) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise APIError("Analysis job not found.", 404)
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return jsonify(result)

    return app
