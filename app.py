# -*- coding: utf-8 -*-
"""
daily-report - รายงานการก่อสร้างประจำวัน
Flask app: บันทึกข้อมูลรายงานประจำวันลง SQLite หรือ PostgreSQL (แล้วแต่สภาพแวดล้อม),
พรีวิว PDF ในเบราว์เซอร์ด้วย pdf.js, และส่งออกเป็นไฟล์ PDF ที่มีโครงสร้าง/ตาราง ตรงกับไฟล์ต้นฉบับ template.pdf

โหมดฐานข้อมูล:
- ถ้ามีตัวแปรแวดล้อม DATABASE_URL หรือ POSTGRES_URL (เช่นตอน deploy บน Vercel + Vercel Postgres/Neon/Supabase)
  แอปจะต่อ PostgreSQL แทน เพราะ Vercel เป็น serverless ไฟล์ระบบเขียนไม่ถาวร ใช้ SQLite ไฟล์เก็บข้อมูลจริงไม่ได้
- ถ้าไม่มีตัวแปรนี้ (รันในเครื่อง หรือ deploy บน Railway ที่มี persistent disk) จะใช้ SQLite ไฟล์ตามเดิม
"""
import os
import io
import re
import datetime
import json
import unicodedata
from workflow import init_schema, install as install_workflow
from report_schema import LABELS, GROUPS, EXTRA_FIELDS, label_key
from pdf_renderer import render_report

from flask import Flask, request, jsonify, render_template, send_file, g, abort

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# เลือกโหมดฐานข้อมูล: PostgreSQL (ถ้ามี DATABASE_URL/POSTGRES_URL) หรือ SQLite (ค่าเริ่มต้น)
# ---------------------------------------------------------------------------
DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("POSTGRES_PRISMA_URL")
)
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    # Vercel Postgres/Neon บางครั้งให้ URL แบบ "postgres://" ซึ่ง psycopg2 รับได้อยู่แล้ว ไม่ต้องแปลง
else:
    import sqlite3

# บน Vercel ระบบไฟล์อ่านได้อย่างเดียว ยกเว้นโฟลเดอร์ /tmp (และข้อมูลใน /tmp ก็ไม่ถาวรข้าม request)
# ตัวแปร VERCEL ถูกตั้งค่าอัตโนมัติโดย Vercel เสมอตอนรันจริง
ON_VERCEL = bool(os.environ.get("VERCEL"))

# ถ้ามีการต่อ Railway Volume ไว้ Railway จะตั้งค่า RAILWAY_VOLUME_MOUNT_PATH ให้อัตโนมัติ
# ใช้โฟลเดอร์นั้นเก็บ report.db (โหมด SQLite) และ output/ เพื่อให้ข้อมูลไม่หายตอน redeploy
# บน Vercel ใช้ /tmp เพราะเป็นที่เดียวที่เขียนได้ (ใช้เก็บแค่ไฟล์พรีวิวชั่วคราว ไม่ใช่ที่เก็บข้อมูลหลัก)
# ถ้ารันในเครื่องตัวเอง (ไม่มีตัวแปรเหล่านี้) จะใช้โฟลเดอร์โปรเจกต์ตามปกติ
if ON_VERCEL:
    DATA_DIR = "/tmp"
else:
    DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", BASE_DIR)

DB_PATH = os.path.join(DATA_DIR, "report.db")  # ใช้เฉพาะโหมด SQLite
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
OUTPUT_DIR = os.path.join(DATA_DIR, "output")
PREVIEW_PDF_PATH = os.path.join(OUTPUT_DIR, "preview.pdf")

try:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
except OSError:
    pass  # เผื่อกรณีระบบไฟล์อ่านได้อย่างเดียว การพรีวิวยังทำงานได้โดยไม่ต้องเขียนไฟล์แคช


def _pick_font(preferred_name, fallback_name):
    """ใช้ฟอนต์ TH Sarabun New ถ้ามีไฟล์อยู่ในโฟลเดอร์ fonts/ ไม่เช่นนั้นใช้ฟอนต์สำรองที่แถมมาให้"""
    preferred = os.path.join(FONTS_DIR, preferred_name)
    if os.path.isfile(preferred):
        return preferred
    return os.path.join(FONTS_DIR, fallback_name)


# ถ้าวางไฟล์ fonts/THSarabunNew.ttf และ fonts/THSarabunNew-Bold.ttf เอง โปรแกรมจะใช้ไฟล์นั้นแทนอัตโนมัติ
FONT_REGULAR = _pick_font("THSarabunNew.ttf", "Sarabun.ttf")
FONT_BOLD = _pick_font("THSarabunNew-Bold.ttf", "Sarabun-Bold.ttf")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

THAI_MONTHS = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]

# ---------------------------------------------------------------------------
# ฟิลด์ทั้งหมดของแบบฟอร์ม (ต้องตรงกับ name="" ใน index.html / script.js)
# โครงสร้างนี้อ้างอิงตำแหน่ง/ตาราง/หัวข้อ ตรงตามไฟล์ template.pdf ต้นฉบับทุกจุด
# ---------------------------------------------------------------------------
FIELDS = [
    # ป้ายหัวเอกสาร (แก้ไขได้ทั้งหมด)
    "projectTitle", "projectSubtitle", "projectAddress", "contractLine",
    # วันที่ (แยกเป็น วัน/เดือน/พ.ศ. ตรงตามต้นฉบับ)
    "reportDay", "reportMonth", "reportYear",
    "totalDays", "remainingDays", "dueDate", "contractStartDate",
    # สภาพอากาศ
    "weatherAM", "weatherPM",
    # จำนวนรวม (บรรทัดหัวตาราง)
    "totalStaffCount", "totalEquipmentCount",
    # ตารางกลุ่มที่ 1: จำนวนเจ้าหน้าที่ของผู้รับจ้าง (7 แถวคงที่)
    "staff_office", "staff_supervisor", "staff_carpenter", "staff_steel",
    "staff_mason", "staff_labourer", "staff_painter",
    # ตารางกลุ่มที่ 2: จำนวนเครื่องจักรของผู้รับจ้าง (4 แถว)
    "eq_excavator", "eq_sixwheel", "eq_crane", "eq_other_label", "eq_other_count",
    # ตารางกลุ่มที่ 3: รายการเพิ่มเติม (2 แถว)
    "eq_roofframe", "staff_electrician",
    # รายละเอียดงาน
    "workLocation", "workDone", "workQuantity",
    # ปัญหา / แนวทางแก้ไข
    "problems", "solutions",
    # ลายเซ็น
    "supervisorName", "contractorRepName",
]

DEFAULTS = {
    "projectTitle": "รายงานการก่อสร้างประจำวัน",
    "projectSubtitle": "ปรับปรุงห้องเรียน 2507, 2509, 2510, ห้องศรีตรัง, ห้อง Visiting Professor, ห้องวิเทศสัมพันธ์",
    "projectAddress": "ณ มหาวิทยาลัยสงขลานครินทร์ วิทยาเขตภูเก็ต ตำบลกะทู้ อำเภอกะทู้ จังหวัดภูเก็ต",
    "contractLine": "สัญญาจ้างเลขที่ 7/2569 ลงวันที่ 5 พฤษภาคม 2569",
    "totalDays": "120",
    "dueDate": "2 กันยายน 2569",
    "contractStartDate": "2026-05-05",  # ตรงกับ "ลงวันที่ 5 พฤษภาคม 2569" ใน contractLine (พ.ศ. 2569 = ค.ศ. 2026)
    "supervisorName": "นายจรูญ เกื้อชู",
    "contractorRepName": "บริษัท อินเตอร์เทค อิเลคทริคแอด์บิ้ลด์ จำกัด",
}

FIELDS += EXTRA_FIELDS
DEFAULTS.update(LABELS)
DEFAULTS.update({label_key(k): title for group in GROUPS for k, title in group})
DEFAULTS.update(extraRows="[]", remainingMode="auto")



# ---------------------------------------------------------------------------
# Database helpers
# รองรับทั้ง SQLite (ค่าเริ่มต้น/local/Railway) และ PostgreSQL (เช่นตอน deploy บน Vercel)
# PH = ตัวคั่นพารามิเตอร์ใน SQL ("?" สำหรับ SQLite, "%s" สำหรับ PostgreSQL)
# ---------------------------------------------------------------------------
PH = "%s" if USE_POSTGRES else "?"


def get_db():
    if "db" not in g:
        if USE_POSTGRES:
            g.db = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        else:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def canonical_row(row):
    values = dict(row)
    names = {field.lower(): field for field in FIELDS}
    return {names.get(key, key): value for key, value in values.items()}


def db_query(db, sql, params=()):
    """รันคำสั่ง SQL แล้วคืน cursor เดียวกันไม่ว่าจะใช้ฐานข้อมูลแบบไหน
    (แถวที่ได้จะเข้าถึงเหมือน dict ได้ทั้งคู่ผ่าน dict(row))"""
    if USE_POSTGRES:
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = db.cursor()
    cur.execute(sql, params)
    return cur


def init_db():
    if ON_VERCEL and not USE_POSTGRES:
        raise RuntimeError("Vercel requires DATABASE_URL for persistent report storage")
    db = psycopg2.connect(DATABASE_URL, connect_timeout=10) if USE_POSTGRES else sqlite3.connect(DB_PATH)
    try:
        cur = db.cursor()
        if USE_POSTGRES:
            cur.execute("SELECT pg_advisory_xact_lock(73452186)")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'reports'" if USE_POSTGRES else "PRAGMA table_info(reports)")
        existing = {row[0] if USE_POSTGRES else row[1] for row in cur.fetchall()}
        missing = [field for field in FIELDS if field.lower() not in {x.lower() for x in existing}]
        if existing and missing and not USE_POSTGRES:
            backup = sqlite3.connect(DB_PATH + '.before-migration-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S') + '.bak')
            db.backup(backup)
            backup.close()
        primary = 'SERIAL PRIMARY KEY' if USE_POSTGRES else 'INTEGER PRIMARY KEY AUTOINCREMENT'
        columns = ', '.join(f'{field} TEXT' for field in FIELDS)
        cur.execute(f'CREATE TABLE IF NOT EXISTS reports (id {primary}, created_at TEXT NOT NULL, {columns})')
        if existing:
            for field in missing:
                cur.execute(f'ALTER TABLE reports ADD COLUMN {"IF NOT EXISTS " if USE_POSTGRES else ""}{field} TEXT')
        init_schema(cur, USE_POSTGRES)
        db.commit()
    finally:
        db.close()


def now_bangkok():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).isoformat(timespec='seconds')


@app.errorhandler(400)
def bad_request(error):
    return jsonify(error=error.description), 400


def report_data(require_date=False, existing=None, raw=None):
    raw = request.get_json(silent=True) if raw is None else raw
    if not isinstance(raw, dict):
        abort(400, description='ข้อมูลต้องเป็น JSON object')
    data = {**DEFAULTS, **{k: v for k, v in (existing or {}).items() if v is not None}}
    for key in FIELDS:
        if key == 'updated_at':
            continue
        if key in raw:
            if not isinstance(raw[key], (str, int, float)) or isinstance(raw[key], bool):
                abort(400, description='ชนิดข้อมูลไม่ถูกต้อง: '+key)
            data[key] = str(raw[key])
        if len(str(data.get(key) or '')) > 30000:
            abort(400, description='ข้อมูลยาวเกินกำหนด: '+key)
    for key in LABELS:
        if len(data.get(key, '')) > 120:
            abort(400, description='หัวข้อยาวเกิน 120 ตัวอักษร: '+key)
    numeric = [k for group in GROUPS for k, _ in group] + ['totalDays', 'totalStaffCount', 'totalEquipmentCount']
    for key in numeric + ['remainingDays']:
        val = str(data.get(key) or '')
        if val and (not re.fullmatch(r'-?\d{1,7}', val) or (key != 'remainingDays' and int(val)<0)):
            abort(400, description='จำนวนต้องเป็นจำนวนเต็มที่ถูกต้อง: '+key)
    date = [data.get(k) for k in ['reportDay','reportMonth','reportYear']]
    if require_date or all(date):
        try:
            report_date = datetime.date(int(date[2])-543, THAI_MONTHS.index(date[1])+1, int(date[0]))
        except (ValueError, TypeError):
            abort(400, description='กรุณาระบุวันที่รายงานให้ถูกต้องครบวัน เดือน และ พ.ศ.')
    else:
        report_date = None
    start = data.get('contractStartDate')
    try:
        start_date = datetime.date.fromisoformat(start) if start else None
    except ValueError:
        abort(400, description='วันที่เริ่มสัญญาไม่ถูกต้อง')
    if data.get('remainingMode') not in ('auto','manual'):
        abort(400, description='โหมดวันคงเหลือไม่ถูกต้อง')
    if data.get('remainingMode') == 'auto':
        data['remainingDays'] = str(int(data['totalDays'])-(report_date-start_date).days) if report_date and start_date and data.get('totalDays') else ''
    for key in ['weatherAM','weatherPM']:
        if data.get(key, '') not in ('','แจ่มใส','ฝนตก','ฝนตกหนัก'):
            abort(400, description='สภาพอากาศไม่ถูกต้อง')
    try:
        extra = json.loads(data.get('extraRows') or '[]')
        if not isinstance(extra,list) or len(extra)>100:
            raise ValueError()
        for row in extra:
            if not isinstance(row,dict) or type(row.get('group')) is not int or row['group'] not in range(3):
                raise ValueError()
            if not isinstance(row.get('label'),str) or len(row['label'])>300:
                raise ValueError()
            if not isinstance(row.get('count'),str) or (row['count'] and not re.fullmatch(r'\d{1,7}',row['count'])):
                raise ValueError()
        data['extraRows'] = json.dumps(extra,ensure_ascii=False)
    except (ValueError,TypeError,KeyError):
        abort(400,description='รายการเพิ่มเติมไม่ถูกต้อง (สูงสุด 100 รายการ)')
    data['updated_at'] = now_bangkok()
    return data


# ---------------------------------------------------------------------------
# PDF generation
# วาดเอกสารให้ตรงกับโครงสร้าง/ตาราง/หัวข้อ ของ template.pdf ต้นฉบับ
# (ขนาดหน้ากระดาษ Letter 612x792pt ตรงกับต้นฉบับ, ตำแหน่งตาราง/คอลัมน์/แถว อิงจากพิกัดจริงในไฟล์ต้นฉบับ)
# ---------------------------------------------------------------------------
_FONTS_REGISTERED = False


def register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("Sarabun", FONT_REGULAR))
    pdfmetrics.registerFont(TTFont("Sarabun-Bold", FONT_BOLD))
    _FONTS_REGISTERED = True


_TOKEN_RE = re.compile(r"[A-Za-z0-9./%\-]+|\s+|.", re.UNICODE)


def wrap_text(text, font, size, max_width):
    """
    แบ่งข้อความยาวออกเป็นหลายบรรทัดให้พอดีกับความกว้างที่กำหนด
    ตัดทีละตัวอักษรสำหรับข้อความไทย (ไม่มีช่องว่างระหว่างคำ) แต่ไม่ตัดกลาง
    ตัวเลข/คำภาษาอังกฤษ (เช่น "2507" จะไม่ถูกตัดเป็น "250" กับ "7")
    """
    if not text:
        return [""]
    lines = []
    for raw_line in str(text).split("\n"):
        tokens = []
        for token in _TOKEN_RE.findall(raw_line):
            if tokens and (unicodedata.category(token[0]).startswith('M') or token == '\u0e33'):
                tokens[-1] += token
            else:
                tokens.append(token)
        current = ""
        for tok in tokens:
            trial = current + tok
            if pdfmetrics.stringWidth(trial, font, size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                # ถ้าตัวโทเค็นเดียวก็ยาวเกินความกว้างอยู่แล้ว (คำอังกฤษ/ตัวเลขยาวมาก) ให้ตัดทีละตัวแทน
                if pdfmetrics.stringWidth(tok, font, size) > max_width:
                    piece = ""
                    for ch in tok:
                        trial_piece = piece + ch
                        if pdfmetrics.stringWidth(trial_piece, font, size) <= max_width:
                            piece = trial_piece
                        else:
                            if piece:
                                lines.append(piece)
                            piece = ch
                    current = piece
                else:
                    current = tok
        lines.append(current)
    return lines if lines else [""]


def build_pdf(data: dict) -> bytes:
    return render_report(data, DEFAULTS, register_fonts, wrap_text)


install_workflow(app, globals())


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    # debug=True เฉพาะตอนไม่มีตัวแปร PORT จาก Railway/host อื่น (รันในเครื่องตัวเอง)
    debug_mode = "PORT" not in os.environ
    app.run(debug=debug_mode, host="0.0.0.0", port=port)
else:
    init_db()
