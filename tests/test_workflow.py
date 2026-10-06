"""Focused HW1 smoke checks. External services are mocked only at the HTTP boundary."""
import io
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from research_assistant import create_app
from research_assistant import db
from research_assistant.llm import LLM, LLMError, chunks, retrieve, summarize
from research_assistant.pdfs import extract_pdf
from research_assistant.search import arxiv_search, search


def paper_pdf(footnotes=False):
    writer = PdfWriter()
    writer.add_metadata({"/Title":"Reliable Research Assistants", "/Author":"" if footnotes else "Alice Chen; Bob Smith"})
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
    texts = [
        ['Reliable Research Assistants','Alice Chen, Bob Smith','2024','Abstract',
         'We study reliable paper analysis. Our approach grounds answers in extracted PDF passages.',
         'The assistant cites page numbers and preserves a local research library.',
         '1 Introduction','We propose a retrieval-based method for answering research questions.'],
        ['2 Experiments','The Aurora dataset contains 2,400 annotated research questions.',
         'Our baseline is a metadata-only assistant. Full-text grounding improves accuracy.',
         '3 Limitations','Scanned documents and complex equations need better extraction.',
         'END_OF_PAPER_EVIDENCE'],
    ]
    if footnotes:
        texts[0][1] = 'Alice Chen*, Bob Smith*'
        texts[0].insert(7, '*Equal contribution. This is not part of the abstract.')
    for lines in texts:
        page = writer.add_blank_page(width=612,height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        commands = ['BT /F1 12 Tf 50 740 Td 22 TL']
        for line in lines:
            line = line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            commands.append(f'({line}) Tj T*')
        commands.append('ET')
        stream = DecodedStreamObject(); stream.set_data('\n'.join(commands).encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    buffer = io.BytesIO(); writer.write(buffer); return buffer.getvalue()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING":True,"DATA_DIR":self.temp.name,"LLM_API_KEY":"",
                       "LLM_API_STYLE":"openai","LLM_BASE_URL":"https://example.invalid/v1",
                       "LLM_MODEL":"test-model","APP_USERNAME":"","APP_PASSWORD":""}
        self.app = create_app(self.config); self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions['executor'].shutdown(wait=True)
        self.temp.cleanup()

    def upload(self):
        response = self.client.post('/api/upload',data={'file':(io.BytesIO(paper_pdf()),'paper.pdf')})
        self.assertEqual(response.status_code,201,response.json)
        return response.json['paper']['id']

    def await_job(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.client.get('/api/jobs/' + job_id).json
            if job['status'] in ('completed','failed'): return job
            time.sleep(.02)
        self.fail('Analysis did not finish')

    def test_save_deduplicates_and_survives_restart(self):
        payload = dict(external_id='arxiv:1706.03762',title='Attention Is All You Need',
                       authors=['Ashish Vaswani'],year=2017,abstract='A Transformer paper.',source='arxiv')
        saved = self.client.post('/api/papers',json=payload)
        self.assertEqual(saved.status_code,201)
        again = self.client.post('/api/papers',json=payload)
        self.assertTrue(again.json['duplicate'])
        second = create_app(self.config)
        try:
            library = second.test_client().get('/api/papers').json['papers']
            self.assertEqual(len(library),1)
            self.assertEqual(library[0]['id'],saved.json['paper']['id'])
        finally: second.extensions['executor'].shutdown(wait=True)

    def test_legacy_conversation_language_migration_preserves_answers(self):
        from pathlib import Path
        path = Path(self.temp.name) / 'legacy.sqlite3'
        with db.connect(path) as conn:
            conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, question TEXT, answer TEXT)")
            conn.execute("INSERT INTO messages VALUES (1, 'What datasets?', 'WMT 2014 [p. 7].')")
            conn.execute("INSERT INTO messages VALUES (2, '数据集？', '使用 WMT 2014 [p. 7]。')")
        db.initialize(path)
        db.initialize(path)
        with db.connect(path) as conn:
            rows = [dict(row) for row in conn.execute('SELECT * FROM messages ORDER BY id')]
        self.assertEqual([row['language'] for row in rows], ['en', 'zh'])
        self.assertEqual(rows[1]['answer'], '使用 WMT 2014 [p. 7]。')

    def test_pdf_upload_metadata_text_edit_and_attachment(self):
        identifier = self.upload()
        detail = self.client.get('/api/papers/' + identifier).json['paper']
        self.assertEqual(detail['title'],'Reliable Research Assistants')
        self.assertEqual(detail['authors'],['Alice Chen','Bob Smith'])
        self.assertEqual(detail['year'],2024)
        self.assertIn('We study reliable',detail['abstract'])
        self.assertEqual(detail['page_count'],2)
        self.assertIn('Aurora dataset',detail['pages'][1]['text'])
        duplicate = self.client.post('/api/upload',data={'file':(io.BytesIO(paper_pdf()),'renamed.pdf')})
        self.assertTrue(duplicate.json['duplicate'])
        updated = self.client.patch('/api/papers/' + identifier,json=dict(title='Reviewed title',authors=['Alice Chen'],year=2025,abstract='Reviewed abstract'))
        self.assertEqual(updated.json['paper']['title'],'Reviewed title')
        pdf = self.client.get(f'/api/papers/{identifier}/pdf')
        self.assertEqual(pdf.mimetype,'application/pdf'); pdf.close()

    def test_invalid_pdf_and_missing_key_have_clear_errors(self):
        response = self.client.post('/api/upload',data={'file':(io.BytesIO(b'not a pdf'),'bad.pdf')})
        self.assertEqual(response.status_code,400)
        writer = PdfWriter(); writer.add_blank_page(width=100,height=100); buffer = io.BytesIO(); writer.write(buffer)
        response = self.client.post('/api/upload',data={'file':(io.BytesIO(buffer.getvalue()),'scan.pdf')})
        self.assertIn('No readable text',response.json['error'])
        identifier = self.upload()
        response = self.client.post(f'/api/papers/{identifier}/summary',json={})
        self.assertEqual(response.status_code,503)
        self.assertIn('Connect a model',response.json['error'])
        self.assertNotIn('LLM_API_KEY',self.client.get('/api/config').json)

    def test_author_markers_and_abstract_footnotes(self):
        extracted = extract_pdf(paper_pdf(footnotes=True))
        self.assertEqual(extracted['metadata']['authors'],['Alice Chen','Bob Smith'])
        self.assertNotIn('Equal contribution',extracted['metadata']['abstract'])

    def test_real_llm_request_shape_full_text_sources_and_history(self):
        self.app.extensions['llm'].key = 'test-only-key'
        identifier = self.upload(); calls = []
        def upstream(url, **kwargs):
            calls.append(kwargs['json'])
            prompt = kwargs['json']['messages'][-1]['content']
            text = '## Overview\nA full-text research assistant [p. 1].' if 'Summarize the following' in prompt else 'The Aurora dataset contains 2,400 questions [p. 2].'
            return httpx.Response(200,json={'choices':[{'message':{'content':text}}]})
        with patch('research_assistant.llm.httpx.post',side_effect=upstream):
            response = self.client.post(f'/api/papers/{identifier}/summary',json={'language':'en'})
            self.assertEqual(response.status_code,202)
            self.assertEqual(self.await_job(response.json['job_id'])['status'],'completed')
            self.assertIn('END_OF_PAPER_EVIDENCE',calls[0]['messages'][-1]['content'])
            cached = self.client.post(f'/api/papers/{identifier}/summary',json={'language':'en'})
            self.assertTrue(cached.json['cached']); self.assertEqual(len(calls),1)
            question = self.client.post(f'/api/papers/{identifier}/questions',json={'question':'What datasets are used?','language':'en'})
            job = self.await_job(question.json['job_id'])
            self.assertEqual(job['status'],'completed')
            self.assertTrue(any(s['page'] == 2 and 'Aurora' in s['text'] for s in job['result']['sources']))
        detail = self.client.get('/api/papers/' + identifier).json
        self.assertEqual(len(detail['messages']),1)
        self.assertIn('Aurora',detail['messages'][0]['answer'])
        self.assertEqual(detail['messages'][0]['language'],'en')
        second = create_app(self.config)
        try:
            restored = second.test_client().get('/api/papers/' + identifier).json
            self.assertEqual(restored['paper']['summary'],detail['paper']['summary'])
            self.assertEqual(restored['messages'],detail['messages'])
        finally: second.extensions['executor'].shutdown(wait=True)

    def test_bad_key_fails_job_without_fake_summary(self):
        self.app.extensions['llm'].key = 'invalid-test-key'
        identifier = self.upload()
        with patch('research_assistant.llm.httpx.post',return_value=httpx.Response(401)):
            response = self.client.post(f'/api/papers/{identifier}/summary',json={})
            job = self.await_job(response.json['job_id'])
        self.assertEqual(job['status'],'failed'); self.assertIn('API key',job['error'])
        self.assertIsNone(self.client.get('/api/papers/' + identifier).json['paper']['summary'])

    def test_azure_responses_endpoint_auth_and_output(self):
        endpoint = 'https://example.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview'
        model = LLM({**self.config,'LLM_API_STYLE':'azure_responses','LLM_BASE_URL':endpoint,
                     'LLM_API_KEY':'test-azure-key','LLM_MODEL':'my-deployment'})
        response = {'status':'completed','output':[
            {'type':'reasoning','summary':[]},
            {'type':'message','content':[{'type':'output_text','text':'Grounded answer [p. 2].'}]}]}
        with patch('research_assistant.llm.httpx.post',return_value=httpx.Response(200,json=response)) as post:
            self.assertEqual(model.complete('Paper evidence'),'Grounded answer [p. 2].')
        args, kwargs = post.call_args
        self.assertEqual(args[0],endpoint)
        self.assertEqual(kwargs['headers'],{'api-key':'test-azure-key'})
        self.assertEqual(kwargs['json']['model'],'my-deployment')
        self.assertEqual(kwargs['json']['input'],'Paper evidence')
        self.assertFalse(kwargs['json']['store'])
        with patch('research_assistant.llm.httpx.post',return_value=httpx.Response(200,json={'status':'incomplete','output':[]})):
            with self.assertRaisesRegex(LLMError,'incomplete'): model.complete('Paper evidence')

    def test_arxiv_parsing_and_labelled_fallback(self):
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>1</o:totalResults><entry><id>http://arxiv.org/abs/1706.03762v1</id><title>Attention Is All You Need</title><author><name>Ashish Vaswani</name></author><published>2017-06-12T00:00:00Z</published><summary>The Transformer model.</summary></entry></feed>'''
        with patch('research_assistant.search.httpx.get',return_value=httpx.Response(200,content=xml,request=httpx.Request('GET','https://export.arxiv.org/api/query'))):
            result = arxiv_search('1706.03762')
        self.assertEqual(result['papers'][0]['external_id'],'arxiv:1706.03762')
        self.assertEqual(result['papers'][0]['year'],2017)
        with patch('research_assistant.search.arxiv_search',side_effect=httpx.ConnectError('offline')), patch('research_assistant.search.crossref_search',return_value={'papers':[],'total':0,'source':'crossref','warning':None,'start':0}):
            fallback = search('transformer')
        self.assertEqual(fallback['source'],'crossref')
        self.assertIn('arXiv is temporarily unavailable',fallback['warning'])

    def test_long_summary_reads_every_page_and_retrieval_finds_evidence(self):
        pages = [{'page':1,'text':'introduction ' * 5000},{'page':2,'text':'Aurora dataset evaluation. ' * 2000 + ' UNIQUE_END_MARKER'}]
        self.assertIn('UNIQUE_END_MARKER',chunks(pages)[-1])
        class Model:
            style = 'openai'
            prompts = []
            def complete(self,prompt): self.prompts.append(prompt); return 'Grounded notes [p. 2].'
        model = Model(); summarize(model,{'title':'Long paper','pages':pages},lambda _:None)
        self.assertTrue(any('UNIQUE_END_MARKER' in p for p in model.prompts))
        selected = retrieve(pages,'使用了哪些数据集？')
        self.assertTrue(any(p['page'] == 2 and 'Aurora' in p['text'] for p in selected))

    def test_origin_guard_and_deletion(self):
        identifier = self.upload()
        blocked = self.client.delete('/api/papers/' + identifier,headers={'Origin':'https://other.example'})
        self.assertEqual(blocked.status_code,403)
        self.assertEqual(self.client.delete('/api/papers/' + identifier).status_code,200)
        self.assertEqual(self.client.get('/api/papers/' + identifier).status_code,404)


if __name__ == '__main__': unittest.main(verbosity=2)
