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
        response = self.client.put(f'/api/reports/{report_id}', json={'workLocation': 'Updated'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f'/api/reports/{report_id}').json['workDone'], 'Test work')
        self.assertEqual(self.client.get('/api/reports').json[0]['workLocation'], 'Updated')
        backup = json.loads(self.client.get('/api/backup').data)
        self.assertEqual(backup['reports'][0]['id'], report_id)
        self.assertEqual(self.client.delete(f'/api/reports/{report_id}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/reports/{report_id}').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/reports/{report_id}').status_code, 404)

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
        self.client.put(f'/api/reports/{report_id}', json={'remainingMode':'manual', 'remainingDays':'-3'})
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
        for name in ('script.js','style.css','pdf.min.js','worker.min.js','Sarabun.ttf'):
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


if __name__ == '__main__':
    unittest.main()
