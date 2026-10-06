"""Text-based PDF extraction without large ML/OCR dependencies."""
import hashlib
import io
import re
from datetime import datetime

import httpx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .search import ARXIV_ID, USER_AGENT

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 120
MAX_TEXT = 400_000


class PDFError(Exception):
    pass


def clean_text(text):
    text = (text or "").replace("\x00", "").replace("\r", "")
    text = re.sub(r"(\w)-\n(?=\w)", r"\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def valid_title(value):
    value = clean_text(value)
    return (len(value) >= 8 and len(value) <= 400
            and not re.search(r"^(untitled|microsoft|latex|word|arxiv:|template|main\.|paper\.)", value, re.I)
            and not re.search(r"\.(pdf|docx?|tex)$", value, re.I))


def extract_metadata(reader, pages, filename, title_fragments):
    first = pages[0]["text"]
    head = "\n".join(p["text"] for p in pages[:2])
    meta = reader.metadata or {}
    title = str(meta.get("/Title") or "").strip()
    if not valid_title(title):
        candidates = [(size, text) for size, text in title_fragments if valid_title(text)]
        if candidates:
            largest = max(size for size, _ in candidates)
            title = " ".join(text for size, text in candidates if size >= largest - 0.5)
        else:
            lines = [line.strip() for line in first.splitlines() if valid_title(line.strip())]
            lines = [line for line in lines if not re.search(r"arxiv|preprint|proceedings|copyright|https?://", line, re.I)]
            title = " ".join(lines[:2]) if lines else filename.rsplit(".", 1)[0]
    title = " ".join(title.split())[:400]
    author_meta = str(meta.get("/Author") or "").strip()
    authors = []
    if author_meta and not re.search(r"^(anonymous|unknown|author|user|latex|microsoft)$", author_meta, re.I):
        authors = [name.strip() for name in re.split(r"\s*;\s*|\s+and\s+|\s*,\s*", author_meta) if name.strip()]
    if not authors:
        # Prefer the short name block immediately after the title, before Abstract.
        lines = first.splitlines()
        title_lines = [i for i, line in enumerate(lines[:35])
                       if len(line.strip()) >= 6 and line.strip().lower() in title.lower()]
        start = max(title_lines) + 1 if title_lines else 0
        for raw in lines[start:start + 48]:
            if re.search(r"\babstract\b|\bintroduction\b", raw, re.I):
                break
            if re.search(r"@|https?://|university|institute|department|research|google|openai|laboratory|school|college|arxiv|conference|proceedings", raw, re.I):
                continue
            line = re.sub(r"[\d*∗⋆†‡✉¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", raw).strip()
            for name in re.split(r"\s*,\s*|\s+and\s+|\s{2,}", line):
                words = name.split()
                if (2 <= len(words) <= 6 and all(
                        re.fullmatch(r"[\w.'’\-]+", w) and
                        (w[0].isupper() or w.lower() in {"van", "von", "de", "del", "der", "da", "di", "al", "bin"})
                        for w in words)
                        and not re.search(r"all rights|is all you need|united states", name, re.I)):
                    authors.append(name)
        authors = list(dict.fromkeys(authors))[:30]
    current_year = datetime.now().year
    arxiv = re.search(r"arXiv\s*:\s*(\d{2})\d{2}\.\d{4,5}", head, re.I)
    year = None
    if arxiv:
        short = int(arxiv.group(1))
        year = 1900 + short if short >= 91 else 2000 + short
    if year is None:
        dates = re.findall(r"\b(?:19|20)\d{2}\b", first[:6000])
        year = next((int(y) for y in dates if 1900 <= int(y) <= current_year + 1), None)
    abstract = ""
    match = re.search(r"\bAbstract\b\s*[:.\-–—]?\s*(.*?)(?=\n\s*(?:\d+[. ]+)?Introduction\b|\n\s*(?:Keywords|Index Terms)\b|\n\s*1[. ]\s+[A-Z]|\n\s*[∗*†‡©]|\n\s*(?:Copyright|arXiv\s*:)|\Z)", head, re.I | re.S)
    if match:
        abstract = " ".join(match.group(1).split())[:6000]
    return {"title": title, "authors": authors, "year": year, "abstract": abstract}


def extract_pdf(data, filename="paper.pdf"):
    if len(data) > MAX_BYTES:
        raise PDFError("The PDF is larger than the 20 MB limit.")
    if not data.lstrip().startswith(b"%PDF-"):
        raise PDFError("This file is not a valid PDF.")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise PDFError("This PDF is password protected. Upload an unlocked copy.")
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise PDFError(f"Please upload a PDF containing 1–{MAX_PAGES} pages.")
        pages = []
        fragments = []
        for index, page in enumerate(reader.pages):
            def collect(text, cm, tm, font, size):
                # Rotated marginal watermarks often have the largest font.
                if index == 0 and size >= 12 and abs(cm[1]) < 0.01 and abs(tm[1]) < 0.01:
                    for line in text.strip().splitlines():
                        fragments.append((size, line.strip()))
            text = clean_text(page.extract_text(visitor_text=collect))
            pages.append({"page": index + 1, "text": text})
        total_text = sum(len(page["text"]) for page in pages)
        if total_text < 100:
            raise PDFError("No readable text was found. This may be a scanned PDF; upload a text-based or OCR-processed copy.")
        if total_text > MAX_TEXT:
            raise PDFError("The extracted text exceeds the 400,000-character limit. Upload a shorter paper.")
        return {"pages": pages, "metadata": extract_metadata(reader, pages, filename, fragments),
                "content_hash": hashlib.sha256(data).hexdigest()}
    except PDFError:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, IndexError, NotImplementedError) as exc:
        raise PDFError("The PDF could not be read. Please try a valid, unlocked PDF.") from exc


def download_arxiv_pdf(external_id):
    # Construct a trusted URL from an identifier, never fetch arbitrary user URLs.
    identifier = external_id.removeprefix("arxiv:")
    if not external_id.startswith("arxiv:") or not ARXIV_ID.fullmatch(identifier):
        raise PDFError("Please attach the paper PDF to enable full-text analysis.")
    try:
        with httpx.stream("GET", "https://arxiv.org/pdf/" + identifier,
                          headers={"User-Agent": USER_AGENT}, timeout=45,
                          follow_redirects=False) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise PDFError("The online PDF exceeds 20 MB. Upload a smaller copy.")
        return bytes(data)
    except httpx.HTTPError as exc:
        raise PDFError("The paper PDF could not be downloaded from arXiv. Open the paper link and attach a local PDF instead.") from exc
