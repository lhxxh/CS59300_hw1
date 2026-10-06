"""Optional live external API/PDF check; does not need or print an LLM API key."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import httpx

from research_assistant.pdfs import download_arxiv_pdf, extract_pdf
from research_assistant.search import arxiv_search, crossref_search


def main():
    started = time.monotonic()
    arxiv = arxiv_search('1706.03762',count=1)
    print('arXiv:',arxiv['papers'][0]['title'], '|',arxiv['papers'][0]['year'],flush=True)
    crossref = crossref_search('attention is all you need',count=2)
    print('Crossref:',len(crossref['papers']),'live records',flush=True)
    data = download_arxiv_pdf('arxiv:1706.03762')
    extracted = extract_pdf(data,'attention-is-all-you-need.pdf')
    output = Path(__file__).resolve().parent.parent / 'tmp' / 'pdfs'
    output.mkdir(parents=True,exist_ok=True)
    pdf = output / 'attention-is-all-you-need.pdf'; pdf.write_bytes(data)
    print('PDF:',len(extracted['pages']),'pages;',sum(len(p['text']) for p in extracted['pages']),'text characters',flush=True)
    print('Detected metadata:',json.dumps(extracted['metadata'],ensure_ascii=False),flush=True)
    print('Example PDF:',pdf,flush=True)
    print('Live checks completed in',round(time.monotonic()-started,2),'seconds',flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc),file=sys.stderr)
        sys.exit(1)
