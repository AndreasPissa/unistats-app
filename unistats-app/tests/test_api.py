"""Run with: python -m unittest discover -s tests -p test_*.py -v"""
import asyncio
import re
import unittest
from collections import OrderedDict
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from backend import main
from backend.analytics import DATASETS
from backend.catalogue import QUERIES
from backend.config import PREFIXES, FROM, BASE
from backend.database import Database, DatabaseUnavailable


class APIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()
        cls.metadata = cls.client.get('/api/metadata?source=snapshot').json()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        main.requests_by_ip.clear()

    def explore(self, **params):
        response = self.client.get('/api/explore', params={'source': 'snapshot', **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_metadata_and_ai_contract(self):
        self.assertEqual(len(self.metadata['departments']), 32)
        self.assertEqual(len(self.client.get('/api/catalogue').json()['queries']), 41)
        with patch.object(main.assistant, 'status', AsyncMock(return_value={'enabled': True, 'ready': False, 'provider': 'openai_compatible', 'executionSource': 'live'})):
            self.assertEqual(self.client.get('/api/ai/status').json()['executionSource'], 'live')
        self.assertEqual(self.client.post('/api/ai/query', json={'question': 'Hi', 'source': 'snapshot'}).status_code, 422)

    def test_institution_metadata_preserves_rdf_identifiers_and_resolves_websites(self):
        institutions = self.metadata['institutions']
        self.assertEqual(len(institutions), 79)
        self.assertEqual(sum(i['kind'] == 'university' for i in institutions.values()), 20)
        self.assertEqual(sum(i['kind'] == 'department' for i in institutions.values()), 32)
        self.assertEqual(sum(i['kind'] == 'school' for i in institutions.values()), 27)
        self.assertEqual(institutions['http://www.cs.aueb.gr/']['url'], 'https://www.dept.aueb.gr/cs')
        department = next(d for d in self.metadata['departments'] if d['uri'] == 'http://www.cs.aueb.gr/')
        self.assertTrue(department['school'])
        self.assertEqual(len(department['programs']), 3)

    def test_campus_coordinates_match_the_supplied_workbook(self):
        from scripts.import_locations import read_workbook
        from backend.config import ROOT
        from backend.locations import load_locations
        snapshot = load_locations()
        expected = read_workbook(ROOT.parent / 'cs_schools.xlsx')
        self.assertEqual(snapshot['sha256'], expected['sha256'])
        self.assertEqual(snapshot['locations'], expected['locations'])
        self.assertEqual(len(snapshot['locations']), 32)
        for department in self.metadata['departments']:
            self.assertEqual(department['location'], expected['locations'][department['uri']])
            self.assertTrue(35 < department['location']['latitude'] < 42)
            self.assertTrue(19 < department['location']['longitude'] < 27)
        self.assertEqual(self.metadata['locationSource']['source'], 'cs_schools.xlsx')
        response = self.client.get('/')
        self.assertIn("connect-src 'self' https://vector.openstreetmap.org", response.headers['content-security-policy'])
        self.assertEqual(response.headers['referrer-policy'], 'strict-origin-when-cross-origin')

    def test_totals_match_independent_raw_observation_sums(self):
        for dataset, config in DATASETS.items():
            with self.subTest(dataset=dataset):
                result = self.explore(dataset=dataset)
                scope = ''
                if dataset == 'postgraduates':
                    scope = '?o dim:program ?p . ?p uni:hasUniversityProgramType ?type . VALUES ?type {res:MSc res:PhD}'
                if dataset == 'entry': scope = '?o dim:studyYear res:StudyYear1 .'
                query = PREFIXES + f'''SELECT (SUM(?n) AS ?total) {FROM} WHERE {{
                    ?o qb:dataSet <{BASE}dataset/{config['dataset']}> ; dim:academicYear res:{result['year']} ; dim:count ?n . {scope} }}'''
                expected = int(main.db.local(query)['rows'][0]['total'])
                self.assertEqual(result['summary']['total'], expected)
                self.assertEqual(sum(r['total'] for r in result['allRows']), expected)
                self.assertEqual(sum(r['count'] for r in result['distribution']), expected)
                self.assertEqual(result['source']['mode'], 'snapshot')
                self.assertIsNone(next(r for r in result['trend'] if r['year'] == 'AY2017_2018')['value'])

    def test_known_undergraduate_counts_and_filters(self):
        all_data = self.explore()
        self.assertEqual(all_data['summary']['total'], 58466)
        self.assertEqual(all_data['summary']['female'], 10991)
        self.assertEqual(all_data['summary']['beyond'], 28784)
        female = self.explore(gender='Female')
        self.assertEqual(female['summary']['total'], 10991)
        self.assertEqual(female['summary']['femaleShare'], 100)
        d = self.metadata['departments'][0]
        filtered = self.explore(department=d['uri'])
        self.assertEqual(len(filtered['allRows']), 1)
        self.assertEqual(filtered['allRows'][0]['id'], d['uri'])
        cities = self.explore(group_by='city')
        city = cities['allRows'][0]
        self.assertEqual(self.explore(city=city['id'])['summary']['total'], city['total'])

    def test_missing_combinations_are_not_zero_observations(self):
        a = self.metadata['departments'][0]
        b = next(d for d in self.metadata['departments'] if d['university'] != a['university'])
        result = self.explore(university=a['university'], department=b['uri'])
        self.assertEqual(result['summary']['observedGroups'], 0)
        self.assertIsNone(result['summary']['femaleShare'])
        self.assertEqual(result['allRows'], [])
        self.assertTrue(all(r['value'] is None for r in result['trend']))
        staff = self.explore(dataset='staff')
        self.assertNotIn('AY2020_2021', staff['availableYears'])
        self.assertIsNone(next(r for r in staff['trend'] if r['year']=='AY2020_2021')['value'])

    def test_every_corrected_catalogue_query_executes(self):
        for query_id in sorted(QUERIES):
            with self.subTest(query=query_id):
                response = self.client.post(f'/api/catalogue/{query_id}/run', json={'source': 'snapshot'})
                self.assertEqual(response.status_code, 200, response.text[:300])
                data = response.json()
                self.assertGreater(data['totalRows'], 0)
                if query_id == '05_master_filterable':
                    self.assertEqual(data['totalRows'], 14293)
                    self.assertTrue(all(r['programType'].endswith(('/MSc', '/PhD')) for r in data['rows']))
                if query_id == 'X05_students_per_dep': self.assertEqual(data['totalRows'], 169)
                if query_id == 'X01_cohort_2020_2024': self.assertEqual(data['totalRows'], 12)
                if query_id == 'X03_delta_otgr': self.assertEqual(data['totalRows'], 11)

    def test_catalogue_scope_and_typed_filters(self):
        response = self.client.post('/api/catalogue/05_master_filterable/run', json={
            'source': 'snapshot', 'filters': {'programType': 'MSc', 'academicYear': 'AY2023_2024', 'gender': 'Female'}})
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()['rows']
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r['programType'].endswith('/MSc') and r['gender'].endswith('/Female') and r['academicYear'].endswith('/AY2023_2024') for r in rows))

    def test_rejects_unapproved_queries_and_invalid_values(self):
        for params in [{'dataset': 'invented'}, {'source': 'other'}, {'department': 'http://evil.example'}, {'top': 999}, {'year':'AY2017_2018'}, {'gender': 'DROP ALL'}]:
            self.assertEqual(self.client.get('/api/explore', params={'source':'snapshot', **params}).status_code, 422)
        for body in [{'query': 'DELETE WHERE {?s ?p ?o}'}, {'filters': {'programType': 'BSc'}}, {'filters': {'gender': 'Female } UNION {?s ?p ?o}'}}, {'filters': {'academicYear': 'AY2023_2024 } DROP ALL'}}, {'filters': {'department': 'http://evil.example'}}]:
            self.assertEqual(self.client.post('/api/catalogue/05_master_filterable/run', json=body).status_code, 422)
        self.assertEqual(self.client.post('/api/catalogue/unknown/run', json={}).status_code, 422)
        self.assertEqual(self.client.get('/assets/../backend/config.py').status_code, 404)
        self.assertEqual(self.client.post('/api/ai/query', content='x'*17000, headers={'Content-Type':'application/json'}).status_code, 413)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database.__new__(Database)
        self.db.cache = OrderedDict()
        self.db.remote_retry_at = 0
        self.db.remote_state = 'not_checked'
        self.db.limit = asyncio.Semaphore(4)
        self.db.local = lambda query: {'columns':['count'], 'rows':[{'count':'42'}]}

    async def test_auto_fallback_is_labelled_and_live_failure_is_not_substituted(self):
        self.db.remote = AsyncMock(side_effect=httpx.ConnectError('offline'))
        result = await self.db.query('SELECT ...', 'auto')
        self.assertEqual(result['source']['mode'], 'snapshot')
        self.assertTrue(result['source']['reason'])
        with self.assertRaises(DatabaseUnavailable): await self.db.query('SELECT ...', 'live')
        cached = await self.db.query('SELECT ...', 'auto')
        self.assertTrue(cached['source']['cached'])

    async def test_remote_select_and_cache_preserve_provenance(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={'head':{'vars':['count']},'results':{'bindings':[{'count':{'type':'literal','value':'123'}}]}})
        self.db.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            result = await self.db.query('SELECT ?count WHERE {}', 'live')
            self.assertEqual(result['rows'], [{'count':'123'}])
            self.assertEqual(result['source']['mode'], 'live')
            self.assertIsNone(result['source']['snapshotDate'])
            self.assertEqual(seen[0].method, 'GET')
            self.assertIn('query=', str(seen[0].url))
            self.assertTrue((await self.db.query('SELECT ?count WHERE {}','live'))['source']['cached'])
            self.assertEqual(len(seen), 1)
            fresh = await self.db.query('SELECT ?count WHERE {}', 'live', fresh=True)
            self.assertFalse(fresh['source']['cached'])
            self.assertEqual(len(seen), 2)
            await self.db.client.aclose()
            self.db.remote = AsyncMock(side_effect=httpx.ConnectError('offline'))
            with self.assertRaises(DatabaseUnavailable):
                await self.db.query('SELECT ?count WHERE {}', 'live', fresh=True)
        finally: await self.db.close()

    async def test_virtuoso_partial_results_are_rejected(self):
        self.db.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={'X-SQL-State':'S1TAT'}, json={})))
        try:
            with self.assertRaises(DatabaseUnavailable): await self.db.remote('SELECT * WHERE {}')
        finally: await self.db.close()

    async def test_server_row_cap_is_paged_without_losing_records(self):
        offsets = []
        def respond(request):
            offset = int(re.search(r'OFFSET (\d+)', request.url.params['query'])[1])
            offsets.append(offset)
            return httpx.Response(200, headers={'X-SPARQL-MaxRows':'2'}, json={
                'head':{'vars':['n']}, 'results':{'bindings':[
                    {'n':{'type':'literal','value':str(i)}} for i in range(offset,min(offset+2,5))]}})
        self.db.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            result = await self.db.remote('SELECT ?n WHERE {} ORDER BY ?n')
            self.assertEqual(result['rows'], [{'n':str(i)} for i in range(5)])
            self.assertEqual(offsets, [0,2,4])
        finally: await self.db.close()


if __name__ == '__main__': unittest.main()
