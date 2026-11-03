"""HW2 API tests: isolated SQLite stores and mocked external services."""
import tempfile
import unittest
from unittest.mock import patch
from research_assistant import create_app

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.tmp.name,
            'LLM_API_KEY': '', 'APP_USERNAME': '', 'APP_PASSWORD': ''})
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions['executor'].shutdown(wait=True)
        self.tmp.cleanup()

    def test_health(self):
        self.assertEqual(self.client.get('/api/health').json, {'status': 'ok'})

    def test_search_invalid_query(self):
        response = self.client.get('/api/search?q=x')
        self.assertEqual(response.status_code, 400)
        self.assertIn('keywords', response.json['error'])

    def test_save_and_find_paper(self):
        data = {'external_id':'arxiv:1706.03762v2', 'source':'arxiv',
                'title':'Attention Is All You Need', 'authors':['Ashish Vaswani'], 'year':2017}
        saved = self.client.post('/api/papers', json=data)
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json['paper']['external_id'], 'arxiv:1706.03762')
        found = self.client.get('/api/papers?q=vaswani').json['papers']
        self.assertEqual([p['id'] for p in found], [saved.json['paper']['id']])

    def test_unknown_paper(self):
        response = self.client.get('/api/papers/nonexistent')
        self.assertEqual(response.status_code, 404)
        self.assertIn('not found', response.json['error'])

    def test_metadata_validation_rejects_invalid_values_without_saving(self):
        valid = {'external_id':'arxiv:1706.03762', 'source':'arxiv', 'title':'Paper'}
        for change in ({'title':' '}, {'authors':'Alice'}, {'year':True},
                       {'year':1899}, {'abstract':'x' * 20001}, {'external_id':'arxiv:bad'}):
            with self.subTest(change=change):
                response = self.client.post('/api/papers', json={**valid, **change})
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.json)
        self.assertEqual(self.client.get('/api/papers').json['papers'], [])

    def test_search_cache_updates_saved_marker(self):
        result = {'papers':[{'external_id':'arxiv:1706.03762', 'title':'Attention'}],
                  'total':1, 'source':'arxiv', 'warning':None, 'start':0}
        with patch('research_assistant.search', return_value=result) as remote:
            first = self.client.get('/api/search?q=attention')
            self.assertIsNone(first.json['papers'][0]['saved_id'])
            saved = self.client.post('/api/papers', json={'external_id':'arxiv:1706.03762',
                'source':'arxiv', 'title':'Attention'})
            second = self.client.get('/api/search?q=attention')
            self.assertEqual(second.json['papers'][0]['saved_id'], saved.json['paper']['id'])
            self.assertEqual(remote.call_count, 1)

    def test_search_rejects_bad_options(self):
        for query in ('q=paper&source=bad', 'q=paper&sort=bad', 'q=paper&start=-1',
                      'q=paper&start=abc'):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/api/search?' + query).status_code, 400)

    def test_basic_auth_and_health_exception(self):
        import base64
        self.app.config.update(APP_USERNAME='reader', APP_PASSWORD='secret')
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.client.get('/api/papers').status_code, 401)
        token = base64.b64encode(b'reader:secret').decode()
        self.assertEqual(self.client.get('/api/papers', headers={'Authorization':'Basic ' + token}).status_code, 200)

    def test_analysis_validation_and_missing_content(self):
        self.app.extensions['llm'].key = 'test-key'
        saved = self.client.post('/api/papers', json={'external_id':'doi:10.1000/example',
            'source':'crossref', 'title':'Paper'})
        identifier = saved.json['paper']['id']
        path = f'/api/papers/{identifier}'
        self.assertEqual(self.client.post(path + '/summary', json={'language':'fr'}).status_code, 400)
        self.assertEqual(self.client.post(path + '/questions', json={'question':'  '}).status_code, 400)
        response = self.client.post(path + '/summary', json={})
        self.assertEqual(response.status_code, 422)
        self.assertIn('Attach', response.json['error'])
        self.assertEqual(self.client.get(path + '/pdf').status_code, 404)

    def test_crossref_parses_metadata_and_html(self):
        import httpx
        from research_assistant.search import crossref_search
        payload = {'message': {'total-results': 1, 'items': [{'DOI':'10.1234/ABC',
            'title':['<i>Research</i> &amp; Evidence'], 'author':[{'given':'Ada','family':'Lee'}],
            'published':{'date-parts':[[2024,1]]}, 'abstract':'<p>Useful &amp; clear.</p>'}]}}
        request = httpx.Request('GET', 'https://api.crossref.org/works')
        with patch('research_assistant.search.httpx.get', return_value=httpx.Response(200, json=payload, request=request)):
            result = crossref_search('research', sort='newest')
        paper = result['papers'][0]
        self.assertEqual((paper['external_id'], paper['title'], paper['authors'], paper['year']),
                         ('doi:10.1234/abc', 'Research & Evidence', ['Ada Lee'], 2024))
        self.assertEqual(paper['abstract'], 'Useful & clear.')
        self.assertEqual(paper['pdf_url'], '')

    def test_cross_site_request_is_rejected(self):
        response = self.client.post('/api/papers', json={}, headers={'Sec-Fetch-Site':'cross-site'})
        self.assertEqual(response.status_code, 403)
        self.assertIn('Cross-site', response.json['error'])
