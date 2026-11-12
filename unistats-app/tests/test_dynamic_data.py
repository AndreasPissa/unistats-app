"""Data-driven filters and future RDF records; mutations affect only the in-memory test store."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend import main
from backend.ai_query import QueryCompiler, QueryPlan, Clarification
from backend.config import PREFIXES, FROM, BASE, GRAPHS


class DynamicDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        main.requests_by_ip.clear()

    def explore(self, **params):
        r = self.client.get('/api/explore', params={'source': 'snapshot', **params})
        self.assertEqual(r.status_code, 200, r.text[:600])
        return r.json()

    def raw_total(self, dataset, extra):
        q = PREFIXES + f'SELECT (SUM(?n) AS ?n) {FROM} WHERE {{ ?o qb:dataSet <{BASE}dataset/{dataset}> ; dim:academicYear res:AY2023_2024 ; dim:count ?n . {extra} }}'
        return int(main.db.local(q)['rows'][0]['n'])

    def test_staff_broad_and_specific_filters_preserve_population(self):
        all_staff = self.explore(dataset='staff')
        self.assertEqual(all_staff['summary']['total'], 1342)
        self.assertEqual(sum(r['count'] for r in all_staff['distribution']), 1342)
        dep = self.explore(dataset='staff', staff_group='RegularTeachingResearchStaff')
        members = {'Professor', 'AssociateProfessor', 'AssistantProfessor', 'Lecturer'}
        self.assertEqual(set(dep['availableCategories']), members)
        expected = self.raw_total('teachingStaffByCategoryDataset', '?o dim:staffCategory ?c . ?c skos:broader <' + BASE + 'resource/staffCategory/RegularTeachingResearchStaff> .')
        self.assertEqual(dep['summary']['total'], expected)
        rank = self.explore(dataset='staff', staff_group='RegularTeachingResearchStaff', category='Professor', category_detail='specific')
        self.assertEqual({r['id'] for r in rank['distribution']}, {'Professor'})
        self.assertLess(rank['summary']['total'], expected)
        detailed = self.explore(dataset='staff', category_detail='specific')
        self.assertEqual(sum(r['count'] for r in detailed['distribution']), 1342)
        external = self.explore(dataset='external', staff_group='RegularTeachingResearchStaff')
        self.assertTrue(external['availableStaffGroups'])

    def test_birth_filters_use_recorded_values_and_correct_cubes(self):
        for dataset, cube, extra in [
            ('postgraduates', 'studentsByBirthYearDataset', '?o dim:program ?p . ?p uni:hasUniversityProgramType ?type . VALUES ?type {res:MSc res:PhD}'),
            ('students', 'studentsByBirthYearStudyYearDataset', ''),
            ('entry', 'studentsByBirthYearStudyYearDataset', '?o dim:studyYear res:StudyYear1 .'),
            ('graduates', 'graduatesByBirthYearDataset', '?o dim:program ?p . ?p uni:hasUniversityProgramType res:BSc .'),
        ]:
            with self.subTest(dataset=dataset):
                data = self.explore(dataset=dataset, birth_year='2000')
                expected = self.raw_total(cube, extra + ' ?o dim:birthYear ?b . FILTER(STR(?b) = "2000")')
                self.assertEqual(data['summary']['total'], expected)
                self.assertIn('AGNOSTO', data['availableBirthYears'])
                self.assertIn('2000', data['availableBirthYears'])
                self.assertEqual(data['birthYear'], '2000')
                if dataset == 'graduates':
                    self.assertEqual({r['id'] for r in data['distribution']}, {'Female', 'Male'})
                    self.assertEqual(sum(r['count'] for r in data['distribution']), expected)
        grouped = self.explore(dataset='students', birth_year='1951 KAI PRIN', year='AY2016_2017')
        self.assertEqual(grouped['birthYear'], '1951 KAI PRIN')

    def test_impossible_cross_filters_and_injected_values_rejected(self):
        for params in [
            {'dataset': 'staff', 'birth_year': '2000'},
            {'dataset': 'graduates', 'birth_year': '2000', 'category': 'Excellent'},
            {'dataset': 'graduates', 'birth_year': '2000', 'metric': 'excellentShare'},
            {'dataset': 'postgraduates', 'birth_year': '2000" } UNION { ?s ?p ?o'},
            {'dataset': 'staff', 'staff_group': 'fake'},
        ]:
            with self.subTest(params=params):
                r = self.client.get('/api/explore', params={'source': 'snapshot', **params})
                self.assertEqual(r.status_code, 422, r.text[:200])

    def test_ai_compiler_supports_staff_groups_and_birth_filters(self):
        compiler = main.assistant.compiler
        for values in [dict(dataset='staff', staff_group='RegularTeachingResearchStaff'),
                       dict(dataset='staff', group_by='staff_group'),
                       dict(dataset='postgraduates', birth_year='2000'),
                       dict(dataset='students', birth_year='2000'),
                       dict(dataset='graduates', birth_year='2000')]:
            with self.subTest(values=values):
                query = compiler.compile(QueryPlan(mode='statistics', **values))
                rows = main.db.local(query['query'])['rows']
                self.assertGreater(len(rows), 0)
                expected = self.explore(**{k: v for k, v in values.items() if k != 'group_by'})['summary']['total']
                self.assertEqual(sum(float(r['value']) for r in rows), expected)
        with self.assertRaises(Clarification):
            compiler.compile(QueryPlan(mode='statistics', dataset='staff', birth_year='1970'))
        with self.assertRaises(Clarification):
            compiler.compile(QueryPlan(mode='statistics', dataset='graduates', birth_year='2000', group_by='category'))

    def test_new_year_institution_category_birth_value_and_parent_total_are_discovered(self):
        fixture = f'''
        <https://future.example/department> a <{BASE}ontology/Department> ;
          <{BASE}ontology/hasUniversityProgram> <https://future.example/program> ;
          <http://www.w3.org/2000/01/rdf-schema#label> "Future Department"@en ;
          <http://www.w3.org/2003/01/geo/wgs84_pos#lat> "38.1" ;
          <http://www.w3.org/2003/01/geo/wgs84_pos#long> "23.1" .
        <https://future.example/program> <{BASE}ontology/hasUniversityProgramType> <{BASE}resource/BSc> .
        <https://future.example/school> <{BASE}ontology/hasDepartment> <https://future.example/department> .
        <https://future.example/university> <{BASE}ontology/hasSchool> <https://future.example/school> ;
          <http://www.w3.org/2000/01/rdf-schema#label> "Future University"@en .
        <{BASE}resource/staffCategory/FutureRank> <http://www.w3.org/2004/02/skos/core#broader> <{BASE}resource/staffCategory/RegularTeachingResearchStaff> ;
          <http://www.w3.org/2000/01/rdf-schema#label> "Future faculty rank"@en .
        <urn:test:new-students> <http://purl.org/linked-data/cube#dataSet> <{BASE}dataset/studentsByStudyYearDataset> ;
          <{BASE}dimension/academicYear> <{BASE}resource/AY2030_2031> ; <{BASE}dimension/program> <https://future.example/program> ;
          <{BASE}dimension/gender> <{BASE}resource/Female> ; <{BASE}dimension/count> 7 ; <{BASE}dimension/studyYear> <{BASE}resource/StudyYear1> .
        <urn:test:new-birth> <http://purl.org/linked-data/cube#dataSet> <{BASE}dataset/studentsByBirthYearStudyYearDataset> ;
          <{BASE}dimension/academicYear> <{BASE}resource/AY2030_2031> ; <{BASE}dimension/program> <https://future.example/program> ;
          <{BASE}dimension/gender> <{BASE}resource/Female> ; <{BASE}dimension/count> 7 ;
          <{BASE}dimension/studyYear> <{BASE}resource/FutureStudyYear> ; <{BASE}dimension/birthYear> "2012" .
        <urn:test:new-staff> <http://purl.org/linked-data/cube#dataSet> <{BASE}dataset/teachingStaffByCategoryDataset> ;
          <{BASE}dimension/academicYear> <{BASE}resource/AY2030_2031> ; <{BASE}dimension/department> <https://future.example/department> ;
          <{BASE}dimension/gender> <{BASE}resource/Female> ; <{BASE}dimension/count> 3 ;
          <{BASE}dimension/staffCategory> <{BASE}resource/staffCategory/FutureRank> .
        <urn:test:parent-total> <http://purl.org/linked-data/cube#dataSet> <{BASE}dataset/teachingStaffByCategoryDataset> ;
          <{BASE}dimension/academicYear> <{BASE}resource/AY2030_2031> ; <{BASE}dimension/department> <https://future.example/department> ;
          <{BASE}dimension/gender> <{BASE}resource/Female> ; <{BASE}dimension/count> 3 ;
          <{BASE}dimension/staffCategory> <{BASE}resource/staffCategory/RegularTeachingResearchStaff> .
        <urn:test:root-total> <http://purl.org/linked-data/cube#dataSet> <{BASE}dataset/teachingStaffByCategoryDataset> ;
          <{BASE}dimension/academicYear> <{BASE}resource/AY2030_2031> ; <{BASE}dimension/department> <https://future.example/department> ;
          <{BASE}dimension/gender> <{BASE}resource/Female> ; <{BASE}dimension/count> 3 ;
          <{BASE}dimension/staffCategory> <{BASE}resource/staffCategory/AllTeachingStaff> .
        '''
        before = self.client.get('/api/metadata?source=snapshot').json()
        main.db.store.update(f'INSERT DATA {{ GRAPH <{GRAPHS[0]}> {{ {fixture} }} }}')
        try:
            # Simulated live transport reads the isolated RDF store, never writes to the real endpoint.
            with patch.object(main.db, 'remote', AsyncMock(side_effect=main.db.local)):
                updated = self.client.get('/api/metadata?source=live&refresh=true').json()
                self.assertEqual(len(updated['departments']), len(before['departments']) + 1)
                self.assertIn('AY2030_2031', updated['availableYears'])
                self.assertIn('2012', updated['vocabulary']['students']['birthYears'])
                self.assertIn('FutureStudyYear', updated['vocabulary']['students']['birthCategories'])
                self.assertNotIn('FutureStudyYear', updated['vocabulary']['students']['categories'])
                self.assertNotEqual(before['revision'], updated['revision'])
                self.assertNotIn('AllTeachingStaff', [g['id'] for g in updated['staffGroups']])
                dept = next(d for d in updated['departments'] if d['uri'] == 'https://future.example/department')
                self.assertEqual(dept['location']['latitude'], 38.1)
                self.assertEqual(updated['institutions'][dept['uri']]['url'], dept['uri'])
                for dataset, total in [('students', 7), ('staff', 3)]:
                    r = self.client.get('/api/explore', params={'dataset': dataset, 'source': 'live', 'refresh': True}).json()
                    self.assertEqual(r['year'], 'AY2030_2031')
                    self.assertEqual(r['summary']['total'], total)
                compiler = QueryCompiler(updated, main.db)
                q = compiler.compile(QueryPlan(mode='statistics', dataset='staff', staff_group='RegularTeachingResearchStaff', category='FutureRank'))
                self.assertEqual(float(main.db.local(q['query'])['rows'][0]['value']), 3)
                q = compiler.compile(QueryPlan(mode='statistics', dataset='students', birth_year='2012', category='FutureStudyYear'))
                self.assertEqual(float(main.db.local(q['query'])['rows'][0]['value']), 7)
                with patch.object(main.assistant.provider, 'ensure_ready'), patch.object(main.assistant, 'plan', AsyncMock(return_value=QueryPlan(mode='statistics', birth_year='2012'))):
                    answer = self.client.post('/api/ai/query', json={'question': 'How many undergraduates born in 2012 in the latest academic year?'}).json()
                    self.assertEqual(answer['years'], ['AY2030_2031'])
                    self.assertEqual(answer['answer'], '7 students')
                    self.assertEqual(main.assistant.revision, updated['revision'])
        finally:
            main.db.store.update(f'DELETE DATA {{ GRAPH <{GRAPHS[0]}> {{ {fixture} }} }}')
            main.db.cache.clear()
            main.metadata_store.cache.clear()
            main.assistant.set_context(main.metadata)


if __name__ == '__main__':
    unittest.main()
