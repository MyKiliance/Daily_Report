"""Capability links, per-field permissions and atomic document transitions."""
import hashlib
import hmac
import io
import json
import os
import re
from pathlib import Path
import secrets
import unicodedata
from flask import abort, jsonify, render_template, request, send_file

STATUSES = {'draft': 'ร่าง', 'open': 'เปิดให้กรอก', 'review': 'ส่งกลับตรวจ', 'completed': 'เสร็จสิ้น'}


def pdf_filename(data):
    """Use the project name, while keeping it portable across file systems."""
    title = str(data.get('projectSubtitle') or '').strip() or str(data.get('projectTitle') or '').strip() or 'เอกสาร'
    title = unicodedata.normalize('NFC', title)
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '-', title)
    title = re.sub(r'\s+', ' ', title).strip(' .-')
    title = title.encode('utf-8')[:200].decode('utf-8', errors='ignore').rstrip(' .-') or 'เอกสาร'
    if re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',title):
        title = '_'+title
    return title+'.pdf'


def init_schema(cur, postgres):
    primary = 'SERIAL PRIMARY KEY' if postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    cur.execute('''CREATE TABLE IF NOT EXISTS report_workflows (
        report_id INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'draft', editable_fields TEXT NOT NULL DEFAULT '[]',
        fill_token_hash TEXT, revision INTEGER NOT NULL DEFAULT 1)''')
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='report_workflows'" if postgres else "PRAGMA table_info(report_workflows)")
    columns={row[0] if postgres else row[1] for row in cur.fetchall()}
    if 'owner_hash' not in columns:
        cur.execute('ALTER TABLE report_workflows ADD COLUMN owner_hash TEXT')
    cur.execute(f'''CREATE TABLE IF NOT EXISTS report_events (
        id {primary}, report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
        actor TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL, changes TEXT NOT NULL)''')
    cur.execute('''INSERT INTO report_workflows (report_id) SELECT id FROM reports
        WHERE id NOT IN (SELECT report_id FROM report_workflows)
        ON CONFLICT (report_id) DO NOTHING''')


def issuer_key(data_dir, on_vercel):
    configured = os.environ.get('ISSUER_ACCESS_TOKEN', '')
    if configured:
        return configured if len(configured) >= 32 else None
    if on_vercel:
        return None
    path = Path(data_dir) / '.issuer-access-key'
    try:
        with path.open('x', encoding='utf-8') as file:
            file.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    return path.read_text(encoding='utf-8').strip()


def install(app, services):
    s = services
    fields = [field for field in s['FIELDS'] if field != 'updated_at']
    # Configuration and structure are always issuer-only; data fields can be delegated.
    configurable = set(s['LABELS']) | {s['label_key'](k) for group in s['GROUPS'] for k, _ in group}
    delegate = [field for field in fields if field not in configurable and field not in {
        'projectTitle', 'projectSubtitle', 'projectAddress', 'contractLine', 'contractStartDate',
        'totalDays', 'dueDate', 'remainingMode', 'extraRows'}]
    default_delegate = [key for key in delegate if key not in ('supervisorName', 'contractorRepName', 'remainingDays')]
    delegate += ['extraCount:'+str(i) for i in range(100)]
    ph = s['PH']

    def db():
        return s['get_db']()

    def query(sql, params=()):
        return s['db_query'](db(), sql, params)

    def bearer():
        header = request.headers.get('Authorization', '')
        return header[7:] if header.startswith('Bearer ') else ''

    def manager():
        key = issuer_key(s['DATA_DIR'], s['ON_VERCEL'])
        return bool(key and bearer() and hmac.compare_digest(key, bearer()))

    def owner_digest():
        return hashlib.sha256(bearer().encode()).hexdigest()

    def require_issuer():
        if not manager() and not re.fullmatch(r'issuer_[A-Za-z0-9_-]{43}', bearer()):
            abort(403, description='กรุณาเปิดหน้าผู้ออกเอกสารเพื่อเริ่มใช้งาน')

    def require_document_issuer(role):
        if role != 'issuer':
            abort(403, description='เฉพาะผู้ออกเอกสารเจ้าของรายการเท่านั้น')

    def load(report_id):
        record = query(f'SELECT * FROM reports WHERE id = {ph}', (report_id,)).fetchone()
        workflow = query(f'SELECT * FROM report_workflows WHERE report_id = {ph}', (report_id,)).fetchone()
        if record is None or workflow is None:
            abort(404, description='ไม่พบเอกสารหรือลิงก์ถูกยกเลิก')
        return s['canonical_row'](record), dict(workflow)

    def access(report_id):
        record, workflow = load(report_id)
        if manager() or (bearer() and workflow.get('owner_hash') and hmac.compare_digest(owner_digest(), workflow['owner_hash'])):
            return record, workflow, 'issuer'
        token = bearer()
        digest = hashlib.sha256(token.encode()).hexdigest() if token else ''
        if not token or not workflow['fill_token_hash'] or not hmac.compare_digest(digest, workflow['fill_token_hash']):
            abort(404, description='ไม่พบเอกสารหรือลิงก์ถูกยกเลิก')
        return record, workflow, 'filler'

    def public(record, workflow, role):
        return {**record, 'version': workflow['revision'], 'status': workflow['status'],
                'editable_fields': json.loads(workflow['editable_fields']), 'role': role}

    def raw_json():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='ข้อมูลต้องเป็น JSON object')
        return data

    def version(raw, workflow):
        if type(raw.get('version')) is not int:
            abort(428, description='กรุณาเปิดเอกสารล่าสุดก่อนบันทึก')
        if raw['version'] != workflow['revision']:
            abort(409, description='เอกสารถูกแก้ไขแล้ว กรุณาโหลดข้อมูลล่าสุดก่อนบันทึก')

    def event(report_id, role, action, changes):
        query(f'INSERT INTO report_events (report_id, actor, action, created_at, changes) VALUES ({ph},{ph},{ph},{ph},{ph})',
              (report_id, role, action, s['now_bangkok'](), json.dumps(changes, ensure_ascii=False)))

    def claim(report_id, workflow, changes=None):
        assignments = ['revision = revision + 1']
        values = []
        for key, value in (changes or {}).items():
            assignments.append(f'{key} = {ph}')
            values.append(value)
        cursor = query(f'UPDATE report_workflows SET {", ".join(assignments)} WHERE report_id = {ph} AND revision = {ph}',
                       values + [report_id, workflow['revision']])
        if cursor.rowcount != 1:
            db().rollback()
            abort(409, description='เอกสารถูกแก้ไขแล้ว กรุณาโหลดข้อมูลล่าสุดก่อนบันทึก')

    def validate_change(raw, record, workflow, role, preview=False):
        unknown = set(raw) - set(fields) - {'version', 'report_id', 'editable_fields'}
        if unknown:
            abort(400, description='พบช่องข้อมูลที่ไม่รองรับ')
        if role == 'filler':
            if workflow['status'] != 'open':
                if preview and not any(k in fields and str(v) != str(record.get(k) or '') for k, v in raw.items()):
                    return {k: v for k, v in record.items() if k in s['FIELDS']}
                abort(403, description='เอกสารนี้ปิดรับการกรอกแล้ว')
            allowed = set(json.loads(workflow['editable_fields']))
            if 'editable_fields' in raw:
                abort(403, description='ผู้กรอกไม่สามารถกำหนดสิทธิ์ได้')
            forbidden = {k for k in raw if k in fields and k != 'extraRows' and k not in allowed and str(raw[k]) != str(record.get(k) if record.get(k) is not None else s['DEFAULTS'].get(k, ''))}
            if forbidden:
                abort(403, description='ไม่อนุญาตให้แก้ช่องที่ถูกล็อก')
            extra_data = raw.get('extraRows')
            if extra_data is not None:
                try:
                    old_extra = json.loads(record.get('extraRows') or '[]')
                    new_extra = json.loads(extra_data)
                    if not isinstance(new_extra,list) or len(new_extra) != len(old_extra):
                        raise ValueError()
                    for i, (old, new) in enumerate(zip(old_extra, new_extra)):
                        if not isinstance(new,dict) or new.get('label') != old['label'] or new.get('group') != old['group'] or set(new) != set(old):
                            raise ValueError()
                        if new.get('count') != old['count'] and 'extraCount:'+str(i) not in allowed:
                            raise ValueError()
                except (ValueError, TypeError, KeyError):
                    abort(403, description='ไม่อนุญาตให้เปลี่ยนโครงสร้างหรือจำนวนรายการที่ถูกล็อก')
            raw = {k: v for k, v in raw.items() if k in allowed or k == 'extraRows'}
        elif workflow['status'] in ('open', 'completed') and not preview:
            abort(403, description='ต้องเปิดแก้ไขเอกสารก่อนเปลี่ยนรายละเอียด')
        result = s['report_data'](require_date=False, existing=record, raw=raw)
        # A computed value must never change a locked field on behalf of the filler.
        if role == 'filler' and 'remainingDays' not in set(json.loads(workflow['editable_fields'])):
            result['remainingDays'] = record.get('remainingDays') or ''
        return result

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(428)
    def workflow_error(error):
        return jsonify(error=error.description), error.code

    @app.after_request
    def private_headers(response):
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.before_request
    def restrict_static():
        if request.path.startswith('/static/') and request.path.removeprefix('/static/') not in {
                'script.js', 'thai-date.js', 'style.css', 'pdf.min.js', 'worker.min.js', 'Sarabun.ttf', 'Sarabun-Bold.ttf'}:
            abort(404)

    @app.route('/')
    @app.route('/manage')
    @app.route('/fill/<int:report_id>')
    def workspace(report_id=None):
        return render_template('index.html', defaults=s['DEFAULTS'], months=s['THAI_MONTHS'],
                               labels=s['LABELS'], groups=s['GROUPS'], label_key=s['label_key'],
                               role='filler' if report_id else 'issuer', report_id=report_id,
                               delegate=delegate, default_delegate=default_delegate)

    @app.route('/api/session')
    def session_info():
        require_issuer()
        return jsonify(role='issuer')

    @app.route('/api/reports', methods=['GET', 'POST'])
    def reports():
        require_issuer()
        if request.method == 'GET':
            where = '' if manager() else f' WHERE w.owner_hash={ph}'
            rows = query('SELECT r.id, r.created_at, r.updated_at, r.reportDay, r.reportMonth, r.reportYear, r.projectTitle, r.workLocation, w.status, w.revision AS version FROM reports r JOIN report_workflows w ON r.id=w.report_id'+where+' ORDER BY r.id DESC', () if manager() else (owner_digest(),)).fetchall()
            return jsonify([s['canonical_row'](row) for row in rows])
        raw = raw_json()
        data = s['report_data'](require_date=False, raw=raw)
        values = [str(data.get(k) or '') for k in s['FIELDS']]
        suffix = ' RETURNING id' if s['USE_POSTGRES'] else ''
        cur = query(f'INSERT INTO reports (created_at, {", ".join(s["FIELDS"])}) VALUES ({", ".join([ph]*(len(values)+1))}){suffix}', [s['now_bangkok']()]+values)
        report_id = cur.fetchone()['id'] if s['USE_POSTGRES'] else cur.lastrowid
        allowed = raw.get('editable_fields', default_delegate)
        if not isinstance(allowed, list) or any(not isinstance(k, str) or k not in delegate for k in allowed):
            abort(400, description='รายการช่องที่อนุญาตไม่ถูกต้อง')
        query(f'INSERT INTO report_workflows (report_id, editable_fields, owner_hash) VALUES ({ph},{ph},{ph})', (report_id, json.dumps(sorted(set(allowed))), owner_digest()))
        event(report_id, 'issuer', 'created', {k: data.get(k, '') for k in fields})
        db().commit()
        return jsonify(id=report_id, version=1, status='draft'), 201

    @app.route('/api/reports/<int:report_id>', methods=['GET', 'PUT', 'DELETE'])
    def document(report_id):
        record, workflow, role = access(report_id)
        if request.method == 'GET':
            return jsonify(public(record, workflow, role))
        raw = raw_json()
        version(raw, workflow)
        if request.method == 'DELETE':
            require_document_issuer(role)
            claim(report_id, workflow)
            query(f'DELETE FROM report_events WHERE report_id={ph}', (report_id,))
            query(f'DELETE FROM report_workflows WHERE report_id={ph}', (report_id,))
            query(f'DELETE FROM reports WHERE id={ph}', (report_id,))
            db().commit()
            return jsonify(deleted=report_id)
        data = validate_change(raw, record, workflow, role)
        metadata = {}
        if role == 'issuer' and 'editable_fields' in raw:
            allowed = raw['editable_fields']
            if not isinstance(allowed, list) or any(not isinstance(k, str) or k not in delegate for k in allowed):
                abort(400, description='รายการช่องที่อนุญาตไม่ถูกต้อง')
            metadata['editable_fields'] = json.dumps(sorted(set(allowed)))
        claim(report_id, workflow, metadata)
        query(f'UPDATE reports SET {", ".join(k+" = "+ph for k in s["FIELDS"])} WHERE id={ph}',
              [str(data.get(k) or '') for k in s['FIELDS']]+[report_id])
        changes = {k: {'before': record.get(k), 'after': data.get(k)} for k in fields if str(record.get(k) or '') != str(data.get(k) or '')}
        if metadata:
            changes['editable_fields'] = {'before': json.loads(workflow['editable_fields']), 'after': json.loads(metadata['editable_fields'])}
        event(report_id, role, 'saved', changes)
        db().commit()
        return jsonify(id=report_id, version=workflow['revision']+1, status=workflow['status'])

    @app.route('/api/reports/<int:report_id>/workflow', methods=['POST'])
    def transition(report_id):
        record, workflow, role = access(report_id)
        raw = raw_json()
        version(raw, workflow)
        action = raw.get('action')
        status = workflow['status']
        changes, response = {}, {}
        if role == 'filler':
            if action != 'submit' or status != 'open':
                abort(403, description='ไม่อนุญาตให้เปลี่ยนสถานะนี้')
            s['report_data'](require_date=True, existing=record, raw={})
            changes['status'] = 'review'
        elif action == 'issue' and status in ('draft', 'review'):
            changes['status'] = 'open'
        elif action == 'reopen' and status in ('review', 'completed'):
            changes['status'] = 'open'
        elif action == 'edit' and status in ('open', 'completed'):
            changes['status'] = 'draft'
            changes['fill_token_hash'] = None
        elif action == 'complete' and status == 'review':
            changes['status'] = 'completed'
        elif action == 'rotate' and status != 'draft':
            pass
        elif action == 'revoke':
            changes['fill_token_hash'] = None
        else:
            abort(400, description='ไม่สามารถเปลี่ยนสถานะตามคำขอนี้')
        if role == 'issuer' and action in ('issue', 'reopen', 'rotate'):
            if not json.loads(workflow['editable_fields']):
                abort(400, description='เลือกช่องที่ให้ผู้ว่าจ้างกรอกอย่างน้อยหนึ่งช่องก่อนออกเอกสาร')
            token = secrets.token_urlsafe(32)
            changes['fill_token_hash'] = hashlib.sha256(token.encode()).hexdigest()
            response['filler_path'] = f'/fill/{report_id}#{token}'
        claim(report_id, workflow, changes)
        event(report_id, role, action, {'before': status, 'after': changes.get('status', status)})
        query(f'UPDATE reports SET updated_at={ph} WHERE id={ph}', (s['now_bangkok'](),report_id))
        db().commit()
        return jsonify(id=report_id, version=workflow['revision']+1, status=changes.get('status',status), **response)

    @app.route('/api/reports/<int:report_id>/events')
    def events(report_id):
        require_issuer()
        _, _, role = access(report_id)
        require_document_issuer(role)
        rows = query(f'SELECT actor,action,created_at,changes FROM report_events WHERE report_id={ph} ORDER BY id DESC', (report_id,)).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.route('/api/reports/<int:report_id>/pdf')
    def pdf(report_id):
        record, _, _ = access(report_id)
        return send_file(io.BytesIO(s['build_pdf'](record)), mimetype='application/pdf', as_attachment=True,
                         download_name=pdf_filename(record))

    @app.route('/api/preview', methods=['POST'])
    def preview():
        raw = raw_json()
        report_id = raw.get('report_id')
        if report_id is not None:
            if type(report_id) is not int:
                abort(400, description='รหัสเอกสารไม่ถูกต้อง')
            record, workflow, role = access(report_id)
            version(raw, workflow)
            data = validate_change(raw, record, workflow, role, preview=True)
        else:
            require_issuer()
            data = s['report_data'](raw=raw)
        return send_file(io.BytesIO(s['build_pdf'](data)), mimetype='application/pdf', download_name=pdf_filename(data))

    @app.route('/api/backup')
    def backup():
        require_issuer()
        where = '' if manager() else f' WHERE id IN (SELECT report_id FROM report_workflows WHERE owner_hash={ph})'
        rows = [s['canonical_row'](r) for r in query('SELECT * FROM reports'+where+' ORDER BY id', () if manager() else (owner_digest(),)).fetchall()]
        return send_file(io.BytesIO(json.dumps({'reports': rows},ensure_ascii=False).encode()),mimetype='application/json',as_attachment=True,download_name='reports.json')
