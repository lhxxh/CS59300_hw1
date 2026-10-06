"""Official arXiv Atom API, with an explicitly labelled Crossref fallback."""
import html
import re
import threading
import time
import xml.etree.ElementTree as ET

import httpx

USER_AGENT = "ResearchAssistantHW1/1.0 (academic paper discovery)"
ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")
_arxiv_lock = threading.Lock()
_last_arxiv_request = 0.0


class SearchError(Exception):
    pass


def clean_html(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def arxiv_search(query, start=0, count=12, sort="relevance"):
    global _last_arxiv_request
    params = {"start": start, "max_results": count,
              "sortBy": "submittedDate" if sort == "newest" else "relevance",
              "sortOrder": "descending"}
    if ARXIV_ID.fullmatch(query):
        params["id_list"] = query
    else:
        # Every word must match; treat user input as text rather than query syntax.
        terms = re.findall(r'[\w-]+', query, flags=re.UNICODE)
        params["search_query"] = " AND ".join(f'all:"{term}"' for term in terms)
    with _arxiv_lock:
        time.sleep(max(0, 3 - (time.monotonic() - _last_arxiv_request)))
        _last_arxiv_request = time.monotonic()
        response = httpx.get("https://export.arxiv.org/api/query", params=params,
                             headers={"User-Agent": USER_AGENT}, timeout=25,
                             follow_redirects=True)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    ns = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/"}
    papers = []
    for entry in root.findall("a:entry", ns):
        url = entry.findtext("a:id", "", ns).replace("http://", "https://")
        if "/abs/" not in url:
            if "errors" in url:
                raise SearchError("arXiv could not process this search. Try simpler keywords.")
            continue
        identifier = url.split("/abs/", 1)[1]
        papers.append({
            "external_id": "arxiv:" + re.sub(r"v\d+$", "", identifier),
            "title": " ".join(entry.findtext("a:title", "", ns).split()),
            "authors": [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)],
            "year": int(entry.findtext("a:published", "0000", ns)[:4]) or None,
            "abstract": " ".join(entry.findtext("a:summary", "", ns).split()),
            "url": url, "pdf_url": "https://arxiv.org/pdf/" + identifier,
            "source": "arxiv",
        })
    return {"papers": papers, "total": int(root.findtext("o:totalResults", "0", ns)),
            "source": "arxiv", "warning": None, "start": start}


def crossref_search(query, start=0, count=12, sort="relevance", email=""):
    params = {"query.bibliographic": query, "rows": count, "offset": start,
              "filter": "type:journal-article,type:proceedings-article"}
    if sort == "newest":
        params.update(sort="published", order="desc")
    if email:
        params["mailto"] = email
    response = httpx.get("https://api.crossref.org/works", params=params,
                         headers={"User-Agent": USER_AGENT}, timeout=25)
    response.raise_for_status()
    data = response.json()["message"]
    papers = []
    for item in data["items"]:
        if not item.get("title"):
            continue
        date = item.get("published", item.get("issued", {})).get("date-parts", [[]])[0]
        papers.append({
            "external_id": "doi:" + item["DOI"].lower(),
            "title": clean_html(item["title"][0]),
            "authors": [" ".join(filter(None, [a.get("given"), a.get("family")]))
                        or a.get("name", "Unknown author") for a in item.get("author", [])],
            "year": date[0] if date else None,
            "abstract": clean_html(item.get("abstract", "")),
            "url": "https://doi.org/" + item["DOI"], "pdf_url": "", "source": "crossref",
        })
    return {"papers": papers, "total": data["total-results"], "source": "crossref",
            "warning": "Crossref records may omit abstracts. Upload the paper PDF for full-text AI analysis.",
            "start": start}


def search(query, source="auto", start=0, sort="relevance", email=""):
    try:
        if source == "crossref":
            return crossref_search(query, start, sort=sort, email=email)
        try:
            return arxiv_search(query, start, sort=sort)
        except (httpx.HTTPError, ET.ParseError, SearchError, ValueError):
            if source != "auto":
                raise
            result = crossref_search(query, start, sort=sort, email=email)
            result["warning"] = "arXiv is temporarily unavailable; showing live Crossref results. Upload a PDF to analyze full text."
            return result
    except (httpx.HTTPError, ET.ParseError, ValueError, KeyError) as exc:
        raise SearchError("The paper search service is unavailable or rate-limited. Try again or choose another source.") from exc
