"""Run: python -m unittest discover -s tests -v (uses temporary databases only)."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BOOT = tempfile.TemporaryDirectory(dir=ROOT)
os.environ['RAILWAY_VOLUME_MOUNT_PATH'] = BOOT.name
os.environ['ISSUER_ACCESS_TOKEN'] = 'test-issuer-capability-0123456789abcdef'
for key in ('DATABASE_URL', 'POSTGRES_URL', 'POSTGRES_PRISMA_URL', 'VERCEL'):
    os.environ.pop(key, None)
import app as reports


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        reports.DB_PATH = str(Path(self.directory.name) / 'report.db')
        reports.init_db()
        reports.app.config['TESTING'] = True
        self.client = reports.app.test_client()
        self.client.environ_base["HTTP_AUTHORIZATION"] = "Bearer "+os.environ["ISSUER_ACCESS_TOKEN"]
        self.data = dict(reports.DEFAULTS, reportDay='24', reportMonth='ตุลาคม', reportYear='2569',
                         workDone='Test work', workLocation='Room 2507', staff_office='0')

    def tearDown(self):
        self.directory.cleanup()

    def create(self, data=None):
        response = self.client.post('/api/reports', json=data or self.data)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.json['id']

    def test_create_update_delete_and_backup(self):
        report_id = self.create()
        report = self.client.get(f'/api/reports/{report_id}').json
        self.assertEqual(report['staff_office'], '0')
        self.assertTrue(report['updated_at'].endswith('+07:00'))
        response = self.client.put(f'/api/reports/{report_id}', json={'workLocation': 'Updated', 'version':1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f'/api/reports/{report_id}').json['workDone'], 'Test work')
        self.assertEqual(self.client.get('/api/reports').json[0]['workLocation'], 'Updated')
        backup = json.loads(self.client.get('/api/backup').data)
        self.assertEqual(backup['reports'][0]['id'], report_id)
        self.assertEqual(self.client.delete(f'/api/reports/{report_id}',json={'version':2}).status_code, 200)
        self.assertEqual(self.client.get(f'/api/reports/{report_id}').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/reports/{report_id}',json={'version':2}).status_code, 404)

    def test_invalid_data(self):
        for patch in [{'reportDay':'31','reportMonth':'กุมภาพันธ์'}, {'staff_office':'-1'},
                      {'eq_crane':'1.5'}, {'extraRows':'{}'}, {'extraRows':'[{"group":9}]'},
                      {'weatherAM':'bad'}, {'remainingMode':'bad'}, {'contractStartDate':'bad'},
                      {'projectTitle':[]}, {'workDone':'x'*30001}]:
            response = self.client.post('/api/reports', json={**self.data, **patch})
            self.assertEqual(response.status_code, 400, patch)
            self.assertIn('error', response.json)
        self.assertEqual(self.client.post('/api/preview', json=[]).status_code, 400)

    def test_remaining_auto_and_manual(self):
        auto = dict(self.data, reportDay='5', reportMonth='พฤษภาคม', remainingDays='999')
        report_id = self.create(auto)
        self.assertEqual(self.client.get(f'/api/reports/{report_id}').json['remainingDays'], '120')
        self.client.put(f'/api/reports/{report_id}', json={'remainingMode':'manual', 'remainingDays':'-3','version':1})
        self.assertEqual(self.client.get(f'/api/reports/{report_id}').json['remainingDays'], '-3')

    def test_custom_captions_rows_and_explicit_empty_values(self):
        data = dict(self.data, staff_office_label='Custom staff', projectTitle='',
                    workHeading='Custom heading', remainingMode='manual', remainingDays='4',
                    extraRows=json.dumps([{'group':1,'label':'Extra equipment','count':'2'}]))
        report_id = self.create(data)
        saved = self.client.get(f'/api/reports/{report_id}').json
        for key in ('staff_office_label','projectTitle','workHeading','extraRows'):
            self.assertEqual(saved[key], data[key])
        self.assertEqual(self.client.get(f'/api/reports/{report_id}/pdf').status_code,200)

    def test_existing_database_migration_preserves_reports(self):
        source = ROOT / 'report.db'
        digest = hashlib.sha256(source.read_bytes()).digest()
        copied = Path(self.directory.name) / 'legacy.db'
        with closing(sqlite3.connect(copied)) as db:
            db.execute('CREATE TABLE reports (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, workDone TEXT)')
            db.execute("INSERT INTO reports VALUES (1, '2026-01-01', 'Existing report')")
            db.commit()
        with closing(sqlite3.connect(copied)) as db:
            before = db.execute('SELECT id, created_at FROM reports ORDER BY id').fetchall()
        reports.DB_PATH = str(copied)
        reports.init_db()
        with closing(sqlite3.connect(copied)) as db:
            self.assertEqual(before, db.execute('SELECT id, created_at FROM reports ORDER BY id').fetchall())
            self.assertIn('updated_at', [row[1] for row in db.execute('PRAGMA table_info(reports)')])
        self.assertTrue(list(copied.parent.glob('legacy.db.before-migration-*.bak')))
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)
        reports.init_db()  # Migration is repeatable.

    def test_home_and_static_assets(self):
        self.assertEqual(self.client.get('/').status_code,200)
        for name in ('script.js','thai-date.js','style.css','pdf.min.js','worker.min.js','Sarabun.ttf'):
            response = self.client.get('/static/'+name)
            self.assertEqual(response.status_code,200)
            response.close()

    def test_postgres_column_names(self):
        self.assertEqual(reports.canonical_row({'reportday':'24','worklocation':'A','staff_office':'0'}),
                         {'reportDay':'24','workLocation':'A','staff_office':'0'})

    def test_pdf_overflow_retains_all_lines(self):
        try:
            import fitz
        except ImportError:
            self.skipTest('Install PyMuPDF for PDF content verification')
        data = dict(self.data, workDone='\n'.join('ROW-%03d'%i for i in range(80)),
                    problems='\n'.join('PROBLEM-%03d'%i for i in range(30)))
        response = self.client.post('/api/preview', json=data)
        self.assertEqual(response.status_code,200)
        pdf = fitz.open(stream=response.data,filetype='pdf')
        self.assertGreater(len(pdf),1)
        text = '\n'.join(page.get_text() for page in pdf)
        for i in range(80):
            self.assertEqual(text.count('ROW-%03d'%i),1)
        for i in range(30):
            self.assertEqual(text.count('PROBLEM-%03d'%i),1)
        for page in pdf:
            self.assertEqual(tuple(page.rect), (0,0,612,792))
            for word in page.get_text('words'):
                self.assertGreaterEqual(word[0],0)
                self.assertLessEqual(word[2],612)

    def test_long_headers_and_many_extra_rows_finish(self):
        try:
            import fitz
        except ImportError:
            self.skipTest('Install PyMuPDF for PDF content verification')
        data = dict(self.data, projectTitle='LONG HEADER ' * 40,
                    workHeading='CUSTOM HEADING ' * 8,
                    extraRows=json.dumps([{'group':0,'label':'EXTRA-%03d'%i,'count':'1'} for i in range(100)]))
        response = self.client.post('/api/preview', json=data)
        self.assertEqual(response.status_code,200)
        pdf = fitz.open(stream=response.data,filetype='pdf')
        self.assertLess(len(pdf),20)
        text = '\n'.join(page.get_text() for page in pdf)
        for i in range(100):
            self.assertEqual(text.count('EXTRA-%03d'%i),1)


class WorkflowTest(unittest.TestCase):
    setUp = ReportsTest.setUp
    tearDown = ReportsTest.tearDown
    create = ReportsTest.create

    def issue(self, allowed=None, **values):
        report_id = self.create({**self.data, **values, 'editable_fields':allowed or ['workDone','staff_office']})
        response = self.client.post(f'/api/reports/{report_id}/workflow', json={'version':1,'action':'issue'})
        self.assertEqual(response.status_code,200,response.json)
        token = response.json['filler_path'].split('#')[1]
        filler = reports.app.test_client()
        filler.environ_base['HTTP_AUTHORIZATION'] = 'Bearer '+token
        return report_id, filler, token

    def test_public_and_old_endpoints_are_protected(self):
        report_id, _, _ = self.issue()
        public = reports.app.test_client()
        for endpoint in ['/api/reports','/api/session','/api/backup',f'/api/reports/{report_id}/events']:
            self.assertEqual(public.get(endpoint).status_code,403)
        for endpoint in [f'/api/reports/{report_id}',f'/api/reports/{report_id}/pdf']:
            self.assertEqual(public.get(endpoint).status_code,404)
        self.assertEqual(public.post('/api/preview',json=self.data).status_code,403)
        self.assertEqual(public.post('/api/reports',json=self.data).status_code,403)
        self.assertEqual(public.get('/static/app.py').status_code,404)
        self.assertNotIn('Room 2507',public.get('/').get_data(as_text=True))

    def test_filler_edit_lock_and_document_scope(self):
        report_id, filler, _ = self.issue()
        another = self.create()
        data = filler.get(f'/api/reports/{report_id}').json
        self.assertEqual(data['role'],'filler')
        self.assertNotIn('fill_token_hash',data)
        self.assertEqual(filler.get(f'/api/reports/{another}').status_code,404)
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':2,'projectTitle':'FORGED'}).status_code,403)
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':2,'editable_fields':['projectTitle']}).status_code,403)
        self.assertEqual(filler.delete(f'/api/reports/{report_id}',json={'version':2}).status_code,403)
        self.assertEqual(filler.get('/api/reports').status_code,403)
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':2,'workDone':'Filled'}).status_code,200)
        self.assertEqual(self.client.get(f'/api/reports/{report_id}').json['workDone'],'Filled')
        self.assertEqual(filler.get(f'/api/reports/{report_id}/pdf').status_code,200)

    def test_full_lifecycle(self):
        report_id, filler, _ = self.issue()
        url = f'/api/reports/{report_id}/workflow'
        self.assertEqual(self.client.put(f'/api/reports/{report_id}',json={'version':2,'workDone':'blocked'}).status_code,403)
        self.assertEqual(filler.post(url,json={'version':2,'action':'complete'}).status_code,403)
        self.assertEqual(filler.post(url,json={'version':2,'action':'submit'}).json['status'],'review')
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':3,'workDone':'blocked'}).status_code,403)
        self.assertEqual(filler.post('/api/preview',json={'report_id':report_id,'version':3}).status_code,200)
        self.assertEqual(self.client.post(url,json={'version':3,'action':'complete'}).json['status'],'completed')
        self.assertEqual(self.client.put(f'/api/reports/{report_id}',json={'version':4,'workDone':'blocked'}).status_code,403)
        reopened = self.client.post(url,json={'version':4,'action':'reopen'})
        self.assertEqual(reopened.json['status'],'open')
        self.assertEqual(filler.get(f'/api/reports/{report_id}').status_code,404)
        events = self.client.get(f'/api/reports/{report_id}/events').json
        self.assertEqual([e['action'] for e in events],['reopen','complete','submit','issue','created'])
        self.assertEqual(events[2]['actor'],'filler')

    def test_rotation_revocation_and_no_token_leaks(self):
        report_id, filler, old_token = self.issue()
        url = f'/api/reports/{report_id}/workflow'
        result = self.client.post(url,json={'version':2,'action':'rotate'})
        self.assertEqual(filler.get(f'/api/reports/{report_id}').status_code,404)
        new_token = result.json['filler_path'].split('#')[1]
        filler.environ_base['HTTP_AUTHORIZATION'] = 'Bearer '+new_token
        self.assertEqual(filler.get(f'/api/reports/{report_id}').status_code,200)
        for endpoint in [f'/api/reports/{report_id}', '/api/reports', f'/api/reports/{report_id}/events', '/api/backup']:
            body = self.client.get(endpoint).get_data(as_text=True)
            self.assertNotIn(old_token,body)
            self.assertNotIn(new_token,body)
        self.client.post(url,json={'version':3,'action':'revoke'})
        self.assertEqual(filler.get(f'/api/reports/{report_id}').status_code,404)

    def test_optimistic_concurrency(self):
        report_id, filler, _ = self.issue()
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'workDone':'no version'}).status_code,428)
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':2,'workDone':'First'}).status_code,200)
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':2,'workDone':'Stale'}).status_code,409)
        self.assertEqual(filler.post(f'/api/reports/{report_id}/workflow',json={'version':2,'action':'submit'}).status_code,409)
        self.assertEqual(filler.get(f'/api/reports/{report_id}').json['workDone'],'First')

    def test_extra_counts_without_structural_access(self):
        extra = [{'group':0,'label':'Fixed item','count':'1'}]
        report_id, filler, _ = self.issue(['extraCount:0'],extraRows=json.dumps(extra))
        extra[0]['count']='2'
        response=filler.put(f'/api/reports/{report_id}',json={'version':2,'extraRows':json.dumps(extra)})
        self.assertEqual(response.status_code,200,response.json)
        extra[0]['label']='FORGED'
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':3,'extraRows':json.dumps(extra)}).status_code,403)
        extra[0]['label']='Fixed item'
        extra.append({'group':0,'label':'New item','count':'9'})
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':3,'extraRows':json.dumps(extra)}).status_code,403)

    def test_locked_computed_field_and_preview(self):
        report_id, filler, _ = self.issue(['reportDay'])
        before=filler.get(f'/api/reports/{report_id}').json['remainingDays']
        response=filler.put(f'/api/reports/{report_id}',json={'version':2,'reportDay':'25'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(filler.get(f'/api/reports/{report_id}').json['remainingDays'],before)
        self.assertEqual(filler.post('/api/preview',json={'report_id':report_id,'version':3,'projectTitle':'FORGED'}).status_code,403)

    def test_invalid_permissions(self):
        for allowed in ['workDone',['version'],['extraRows'],[{}]]:
            response=self.client.post('/api/reports',json={**self.data,'editable_fields':allowed})
            self.assertEqual(response.status_code,400)

    def test_editing_issued_details_revokes_filler(self):
        report_id, filler, _ = self.issue()
        result=self.client.post(f'/api/reports/{report_id}/workflow',json={'version':2,'action':'edit'})
        self.assertEqual(result.json['status'],'draft')
        self.assertEqual(filler.get(f'/api/reports/{report_id}').status_code,404)
        self.assertEqual(self.client.put(f'/api/reports/{report_id}',json={'version':3,'projectTitle':'New title'}).status_code,200)

    def test_issuer_can_edit_after_approval_without_keeping_old_approval(self):
        report_id,filler,_=self.issue()
        url=f'/api/reports/{report_id}'
        filler.post(url+'/workflow',json={'version':2,'action':'submit'})
        self.client.post(url+'/workflow',json={'version':3,'action':'complete'})
        self.assertEqual(self.client.get(url).json['status'],'completed')
        response=self.client.post(url+'/workflow',json={'version':4,'action':'edit'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['status'],'draft')
        self.assertEqual(filler.get(url).status_code,404)
        self.assertEqual(self.client.put(url,json={'version':5,'workDone':'Corrected after approval'}).status_code,200)
        self.assertEqual(self.client.get(url).json['workDone'],'Corrected after approval')
        self.assertEqual(self.client.get(url).json['status'],'draft')
        self.assertEqual(self.client.post(url+'/workflow',json={'version':6,'action':'complete'}).status_code,400)
        events=self.client.get(url+'/events').json
        self.assertEqual([e['action'] for e in events[:3]],['saved','edit','complete'])

    def test_submit_action_is_after_form_fields(self):
        html=self.client.get('/fill/1').get_data(as_text=True)
        self.assertEqual(html.count('id="btnSubmit"'),1)
        self.assertGreater(html.index('id="btnSubmit"'),html.index('name="dueDate"'))
        self.assertLess(html.index('id="btnSubmit"'),html.index('</form>'))

    def test_parallel_saves_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        report_id, _, token = self.issue()
        barrier=threading.Barrier(2)
        def save(text):
            client=reports.app.test_client()
            client.environ_base['HTTP_AUTHORIZATION']='Bearer '+token
            barrier.wait(timeout=5)
            return client.put(f'/api/reports/{report_id}',json={'version':2,'workDone':text}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(save,['Concurrent A','Concurrent B']))
        self.assertEqual(sorted(results),[200,409])
        events=self.client.get(f'/api/reports/{report_id}/events').json
        self.assertEqual(sum(event['action']=='saved' for event in events),1)

    def test_issue_undated_draft_and_finish_date_as_filler(self):
        report_id, filler, _=self.issue(['reportDay','reportMonth','reportYear'],reportDay='',reportMonth='',reportYear='')
        url=f'/api/reports/{report_id}/workflow'
        self.assertEqual(filler.post(url,json={'version':2,'action':'submit'}).status_code,400)
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':2,'reportDay':'24'}).status_code,200)
        self.assertEqual(filler.put(f'/api/reports/{report_id}',json={'version':3,'reportMonth':'ตุลาคม','reportYear':'2569'}).status_code,200)
        self.assertEqual(filler.post(url,json={'version':4,'action':'submit'}).status_code,200)

    def test_home_opens_issuer_form_without_private_link(self):
        public=reports.app.test_client()
        page=public.get('/').get_data(as_text=True)
        self.assertIn('id="reportForm"',page)
        self.assertIn('data-role="issuer"',page)
        self.assertNotIn('ระบบเอกสารร่วมกัน</h1>',page)

    def test_browser_issuers_can_only_manage_their_own_documents(self):
        import secrets
        first=reports.app.test_client()
        second=reports.app.test_client()
        first.environ_base['HTTP_AUTHORIZATION']='Bearer issuer_'+secrets.token_urlsafe(32)
        second.environ_base['HTTP_AUTHORIZATION']='Bearer issuer_'+secrets.token_urlsafe(32)
        self.assertEqual(first.get('/api/session').status_code,200)
        created=first.post('/api/reports',json=self.data)
        self.assertEqual(created.status_code,201)
        report_id=created.json['id']
        self.assertEqual(len(first.get('/api/reports').json),1)
        self.assertEqual(second.get('/api/reports').json,[])
        for endpoint in [f'/api/reports/{report_id}',f'/api/reports/{report_id}/pdf',f'/api/reports/{report_id}/events']:
            self.assertEqual(second.get(endpoint).status_code,404)
        self.assertEqual(second.put(f'/api/reports/{report_id}',json={'version':1,'workDone':'FORGED'}).status_code,404)
        self.assertEqual(second.delete(f'/api/reports/{report_id}',json={'version':1}).status_code,404)
        self.assertEqual(second.post(f'/api/reports/{report_id}/workflow',json={'version':1,'action':'issue'}).status_code,404)
        self.assertEqual(second.get('/api/backup').json['reports'],[])
        self.assertEqual(len(first.get('/api/backup').json['reports']),1)
        self.assertEqual(len(self.client.get('/api/reports').json),1)  # Existing administrator links still work.


if __name__ == '__main__':
    unittest.main()
