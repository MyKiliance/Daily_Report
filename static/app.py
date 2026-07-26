# -*- coding: utf-8 -*-
"""
daily-report - รายงานการก่อสร้างประจำวัน
Flask app: บันทึกข้อมูลรายงานประจำวันลง SQLite, พรีวิว PDF ในเบราว์เซอร์ด้วย pdf.js,
และส่งออกเป็นไฟล์ PDF
"""
import os
import io
import sqlite3
import datetime
import json

from flask import Flask, request, jsonify, render_template, send_file, g, abort

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "report.db")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
PREVIEW_PDF_PATH = os.path.join(OUTPUT_DIR, "preview.pdf")

os.makedirs(OUTPUT_DIR, exist_ok=True)


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

# ---------------------------------------------------------------------------
# ฟิลด์ทั้งหมดของแบบฟอร์ม (ต้องตรงกับ name="" ใน index.html / script.js)
# ---------------------------------------------------------------------------
FIELDS = [
    "reportDate", "totalDays", "remainingDays", "dueDate",
    "weatherAM", "weatherPM",
    "staff_supervisor", "staff_electrician", "staff_mason", "staff_carpenter",
    "staff_steelworker", "staff_painter", "staff_labourer", "staff_office",
    "staff_other_label", "staff_other_count",
    "eq_excavator", "eq_sixwheel", "eq_crane", "eq_roofframe",
    "eq_other_label", "eq_other_count",
    "workLocation", "workDone", "workQuantity",
    "problems", "solutions",
    "contractor", "supervisorName", "contractorRepName",
]

STAFF_ROWS = [
    ("staff_supervisor", "นายช่างคุมงาน"),
    ("staff_electrician", "ช่างไฟฟ้า"),
    ("staff_mason", "ช่างปูน"),
    ("staff_carpenter", "ช่างไม้"),
    ("staff_steelworker", "ช่างเหล็ก"),
    ("staff_painter", "ช่างทาสี"),
    ("staff_labourer", "กรรมกร"),
    ("staff_office", "เจ้าหน้าที่ประจำสำนักงาน"),
]

EQUIP_ROWS = [
    ("eq_excavator", "รถขุด"),
    ("eq_sixwheel", "รถหกล้อ"),
    ("eq_crane", "Mobile Crane"),
    ("eq_roofframe", "ช่างโครงหลังคาเหล็ก"),
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    columns_sql = ", ".join(f"{f} TEXT" for f in FIELDS)
    db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            {columns_sql}
        )
        """
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Routes - หน้าเว็บ
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes - API
# ---------------------------------------------------------------------------
@app.route("/api/reports", methods=["GET"])
def list_reports():
    db = get_db()
    rows = db.execute(
        "SELECT id, created_at, reportDate, workLocation FROM reports ORDER BY id DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/reports/<int:report_id>", methods=["GET"])
def get_report(report_id):
    db = get_db()
    row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        abort(404, description="ไม่พบรายงานนี้")
    return jsonify(dict(row))


@app.route("/api/reports", methods=["POST"])
def create_report():
    data = request.get_json(silent=True) or {}
    values = [str(data.get(f, "") or "") for f in FIELDS]
    created_at = datetime.datetime.now().isoformat(timespec="seconds")

    db = get_db()
    placeholders = ", ".join("?" for _ in FIELDS)
    cols = ", ".join(FIELDS)
    cur = db.execute(
        f"INSERT INTO reports (created_at, {cols}) VALUES (?, {placeholders})",
        [created_at] + values,
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "created_at": created_at}), 201


@app.route("/api/reports/<int:report_id>", methods=["DELETE"])
def delete_report(report_id):
    db = get_db()
    db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    db.commit()
    return jsonify({"deleted": report_id})


@app.route("/api/reports/<int:report_id>/pdf", methods=["GET"])
def export_pdf(report_id):
    db = get_db()
    row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        abort(404, description="ไม่พบรายงานนี้")

    pdf_bytes = build_pdf(dict(row))
    filename = f"daily-report-{row['reportDate'] or row['id']}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/preview", methods=["POST"])
def preview_pdf():
    """
    สร้าง PDF จากข้อมูลในฟอร์มปัจจุบัน (ยังไม่ได้บันทึก) เพื่อดูตัวอย่างในเบราว์เซอร์ด้วย pdf.js
    ไฟล์ล่าสุดจะถูกเขียนทับไว้ที่ output/preview.pdf ด้วย
    """
    data = request.get_json(silent=True) or {}
    pdf_bytes = build_pdf(data)

    try:
        with open(PREVIEW_PDF_PATH, "wb") as f:
            f.write(pdf_bytes)
    except OSError:
        pass  # การพรีวิวยังทำงานได้แม้เขียนไฟล์ลงดิสก์ไม่สำเร็จ

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name="preview.pdf",
    )


# ---------------------------------------------------------------------------
# PDF generation - วาดเอกสารให้มีหน้าตาใกล้เคียงกับ template.pdf ต้นฉบับ
# ---------------------------------------------------------------------------
_FONTS_REGISTERED = False


def register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("Sarabun", FONT_REGULAR))
    pdfmetrics.registerFont(TTFont("Sarabun-Bold", FONT_BOLD))
    _FONTS_REGISTERED = True


def checkbox(c, x, y, checked, size=3.2 * mm):
    c.rect(x, y, size, size)
    if checked:
        c.setLineWidth(1.2)
        c.line(x, y, x + size, y + size)
        c.line(x, y + size, x + size, y)
        c.setLineWidth(0.5)


def wrap_text(c, text, font, size, max_width):
    """แบ่งข้อความยาวออกเป็นหลายบรรทัดให้พอดีกับความกว้างที่กำหนด"""
    if not text:
        return [""]
    lines = []
    for raw_line in str(text).split("\n"):
        words = list(raw_line)  # ภาษาไทยไม่มีช่องว่างระหว่างคำเสมอไป จึงตัดเป็นตัวอักษร
        current = ""
        for ch in words:
            trial = current + ch
            if pdfmetrics.stringWidth(trial, font, size) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = ch
        lines.append(current)
    return lines if lines else [""]


def build_pdf(data: dict) -> bytes:
    """
    วาดเอกสารเป็นกรอบใหญ่กรอบเดียว แบ่งเป็นแถบ (section) ด้วยเส้นแนวนอน
    และมีเส้นแบ่งกลางในส่วนที่เป็นสองคอลัมน์ (ตารางเจ้าหน้าที่/เครื่องจักร และลายเซ็น)
    ให้หน้าตาเหมือนกรอบ ASCII ที่ผู้ใช้ร่างไว้
    """
    register_fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 15 * mm
    x0 = margin
    x1 = width - margin
    mid_x = (x0 + x1) / 2
    PAD_X = 5 * mm
    y = height - margin

    sections = []  # เก็บ (top, bottom, vsplit_x หรือ None)

    def text(x, y_pos, s, font="Sarabun", size=11, align="left"):
        c.setFont(font, size)
        s = s or ""
        if align == "left":
            c.drawString(x, y_pos, s)
        elif align == "center":
            c.drawCentredString(x, y_pos, s)
        elif align == "right":
            c.drawRightString(x, y_pos, s)

    # ===================== หัวเรื่อง =====================
    top = y
    y -= 8 * mm
    text(width / 2, y, "รายงานการก่อสร้างประจำวัน", font="Sarabun-Bold", size=16, align="center")
    y -= 7 * mm
    text(width / 2, y, "ปรับปรุงห้องเรียน 2507, 2509, 2510, ห้องศรีตรัง, ห้อง Visiting Professor, ห้องวิเทศสัมพันธ์",
         size=10.5, align="center")
    y -= 5.5 * mm
    text(width / 2, y, "ณ มหาวิทยาลัยสงขลานครินทร์ วิทยาเขตภูเก็ต ตำบลกะทู้ อำเภอกะทู้ จังหวัดภูเก็ต",
         size=10.5, align="center")
    y -= 5.5 * mm
    text(width / 2, y, "สัญญาจ้างเลขที่ 7/2569 ลงวันที่ 5 พฤษภาคม 2569", size=10, align="center")
    y -= 6 * mm
    sections.append((top, y, None))

    # ===================== วันที่ / จำนวนวัน =====================
    top = y
    y -= 8 * mm
    text(x0 + PAD_X, y, f"วันที่: {data.get('reportDate') or '-'}")
    text(x0 + 68 * mm, y, f"จำนวนวันทำงาน: {data.get('totalDays') or '-'} วัน")
    text(x0 + 115 * mm, y, f"เหลือ: {data.get('remainingDays') or '-'} วัน")
    y -= 6.5 * mm
    text(x0 + PAD_X, y, f"ครบกำหนดส่งมอบภายใน: {data.get('dueDate') or '-'}")
    y -= 6 * mm
    sections.append((top, y, None))

    # ===================== สภาพอากาศ =====================
    top = y
    y -= 7 * mm
    text(x0 + PAD_X, y, "สภาพอากาศ", font="Sarabun-Bold", size=11.5)
    y -= 6.5 * mm
    weather_opts = ["แจ่มใส", "ฝนตก", "ฝนตกหนัก"]
    text(x0 + PAD_X, y, "เช้า:")
    cx = x0 + PAD_X + 15 * mm
    for opt in weather_opts:
        checkbox(c, cx, y - 2.4 * mm, data.get("weatherAM") == opt)
        text(cx + 5 * mm, y, opt, size=10)
        cx += 30 * mm
    y -= 6.5 * mm
    text(x0 + PAD_X, y, "บ่าย:")
    cx = x0 + PAD_X + 15 * mm
    for opt in weather_opts:
        checkbox(c, cx, y - 2.4 * mm, data.get("weatherPM") == opt)
        text(cx + 5 * mm, y, opt, size=10)
        cx += 30 * mm
    y -= 6 * mm
    sections.append((top, y, None))

    # ===================== ตารางเจ้าหน้าที่ | ตารางเครื่องจักร (สองคอลัมน์) =====================
    top = y
    y -= 7 * mm
    col1_x = x0 + PAD_X
    col2_x = mid_x + PAD_X
    text(col1_x, y, "จำนวนเจ้าหน้าที่ของผู้รับจ้าง", font="Sarabun-Bold", size=10.5)
    text(col2_x, y, "จำนวนเครื่องจักรของผู้รับจ้าง", font="Sarabun-Bold", size=10.5)
    y -= 6.5 * mm

    row_y = y
    row_h = 5.6 * mm
    max_rows = max(len(STAFF_ROWS) + 1, len(EQUIP_ROWS) + 1)
    for i in range(max_rows):
        ry = row_y - i * row_h
        if i < len(STAFF_ROWS):
            key, label = STAFF_ROWS[i]
            text(col1_x, ry, label, size=10)
            text(col1_x + 55 * mm, ry, f"{data.get(key) or '0'} คน", size=10)
        elif i == len(STAFF_ROWS):
            other_label = data.get("staff_other_label") or "อื่นๆ"
            text(col1_x, ry, other_label, size=10)
            text(col1_x + 55 * mm, ry, f"{data.get('staff_other_count') or '0'} คน", size=10)

        if i < len(EQUIP_ROWS):
            key, label = EQUIP_ROWS[i]
            text(col2_x, ry, label, size=10)
            text(col2_x + 50 * mm, ry, f"{data.get(key) or '0'} คัน", size=10)
        elif i == len(EQUIP_ROWS):
            other_label = data.get("eq_other_label") or "อื่นๆ"
            text(col2_x, ry, other_label, size=10)
            text(col2_x + 50 * mm, ry, f"{data.get('eq_other_count') or '0'} คัน", size=10)

    y = row_y - max_rows * row_h - 4 * mm
    sections.append((top, y, mid_x))

    # ===================== รายละเอียดงาน / ปัญหา (กล่องเดียว รวมกัน) =====================
    top = y
    y -= 7 * mm

    def block(label, key, size=10.5):
        nonlocal y
        text(x0 + PAD_X, y, label, font="Sarabun-Bold", size=11)
        y -= 5.5 * mm
        content = data.get(key) or "-"
        for line in wrap_text(c, content, "Sarabun", size, (x1 - PAD_X) - (x0 + PAD_X)):
            text(x0 + PAD_X, y, line, size=size)
            y -= 5 * mm
        y -= 3 * mm

    block("ตำแหน่งที่ดำเนินการ", "workLocation")
    block("งานที่ดำเนินการ (ทำอะไรบ้าง)", "workDone")
    block("ปริมาณงานที่ทำได้ (ถ้ามี เช่น จำนวน เมตร ตร.ม. งานแล้วเสร็จกี่ %)", "workQuantity")
    y -= 3 * mm
    sections.append((top, y, None))

    # ===================== ปัญหา / แนวทางแก้ไข =====================
    top = y
    y -= 7 * mm
    block("ปัญหา / อุปสรรค", "problems")
    block("แนวทางการแก้ไข", "solutions")
    y -= 3 * mm
    sections.append((top, y, None))

    # ===================== ผู้รับจ้าง =====================
    top = y
    y -= 8 * mm
    text(x0 + PAD_X, y, f"ผู้รับจ้าง: {data.get('contractor') or '-'}", size=10.5)
    y -= 6 * mm
    sections.append((top, y, None))

    # ===================== ผู้ควบคุมงาน | ผู้รับจ้าง (สองคอลัมน์) =====================
    top = y
    sig_block_h = 24 * mm
    y -= sig_block_h
    sig_y = y + 14 * mm
    c.setLineWidth(0.7)
    c.line(x0 + 12 * mm, sig_y, mid_x - 12 * mm, sig_y)
    text((x0 + mid_x) / 2, sig_y - 5 * mm, f"({data.get('supervisorName') or ''})", size=10, align="center")
    text((x0 + mid_x) / 2, sig_y - 10 * mm, "ผู้ควบคุมงาน", size=10, align="center")

    c.line(mid_x + 12 * mm, sig_y, x1 - 12 * mm, sig_y)
    text((mid_x + x1) / 2, sig_y - 5 * mm, f"({data.get('contractorRepName') or '.....................'})", size=10, align="center")
    text((mid_x + x1) / 2, sig_y - 10 * mm, "ผู้รับจ้าง / ตัวแทนผู้รับจ้าง", size=10, align="center")
    sections.append((top, y, mid_x))

    # ===================== วาดกรอบและเส้นแบ่งทั้งหมด =====================
    c.setLineWidth(1.2)
    c.rect(x0, sections[-1][1], x1 - x0, sections[0][0] - sections[-1][1])

    c.setLineWidth(0.6)
    for i in range(len(sections) - 1):
        boundary_y = sections[i][1]
        c.line(x0, boundary_y, x1, boundary_y)

    for top_y, bottom_y, vsplit in sections:
        if vsplit is not None:
            c.line(vsplit, top_y, vsplit, bottom_y)

    y -= 6 * mm
    text(x0, y, "หมายเหตุ: ครบกำหนดส่งมอบภายใน 2 กันยายน 2569", size=9.5)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
else:
    init_db()
