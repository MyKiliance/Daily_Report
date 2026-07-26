# -*- coding: utf-8 -*-
"""
daily-report - รายงานการก่อสร้างประจำวัน
Flask app: บันทึกข้อมูลรายงานประจำวันลง SQLite, พรีวิว PDF ในเบราว์เซอร์ด้วย pdf.js,
และส่งออกเป็นไฟล์ PDF ที่มีโครงสร้าง/ตาราง ตรงกับไฟล์ต้นฉบับ template.pdf
"""
import os
import io
import re
import sqlite3
import datetime

from flask import Flask, request, jsonify, render_template, send_file, g, abort

from reportlab.lib.pagesizes import LETTER
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
    "totalDays", "remainingDays", "dueDate",
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
    "supervisorName": "นายจรูญ เกื้อชู",
    "contractorRepName": "บริษัท อินเตอร์เทค อิเลคทริคแอด์บิ้ลด์ จำกัด",
}

# ป้ายชื่อคงที่ในตารางกลุ่มที่ 1/2/3 (ต้องตรงกับ template.pdf)
GROUP1_ROWS = [  # จำนวนเจ้าหน้าที่ของผู้รับจ้าง (7 แถว)
    ("staff_office", "เจ้าหน้าที่ประจำสำนักงาน"),
    ("staff_supervisor", "นายช่างคุมงาน"),
    ("staff_carpenter", "ช่างไม้"),
    ("staff_steel", "ช่างเหล็ก"),
    ("staff_mason", "ช่างปูน"),
    ("staff_labourer", "กรรมกร"),
    ("staff_painter", "ช่างทาสี"),
]
GROUP2_ROWS = [  # จำนวนเครื่องจักรของผู้รับจ้าง (4 แถว + แถวว่าง)
    ("eq_excavator", "รถขุด"),
    ("eq_sixwheel", "รถหกล้อ"),
    ("eq_crane", "Mobile Crane"),
    (None, None),  # แถวนี้ใช้ eq_other_label / eq_other_count แทน (ป้ายแก้ไขได้)
]
GROUP3_ROWS = [  # ต่อจากตารางเครื่องจักร (2 แถว)
    ("eq_roofframe", "ช่างโครงหลังคาเหล็ก"),
    ("staff_electrician", "ช่างไฟฟ้า"),
]
TABLE1_ROW_COUNT = 7  # จำนวนแถวทั้งหมดของตาราง (เท่ากับกลุ่มที่ยาวที่สุด)


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
    return render_template("index.html", defaults=DEFAULTS, months=THAI_MONTHS)


# ---------------------------------------------------------------------------
# Routes - API
# ---------------------------------------------------------------------------
@app.route("/api/reports", methods=["GET"])
def list_reports():
    db = get_db()
    rows = db.execute(
        "SELECT id, created_at, reportDay, reportMonth, reportYear, workLocation "
        "FROM reports ORDER BY id DESC"
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
    date_part = "-".join(
        str(row[k] or "") for k in ("reportYear", "reportMonth", "reportDay")
    ).strip("-") or str(row["id"])
    filename = f"daily-report-{date_part}.pdf"
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
        tokens = _TOKEN_RE.findall(raw_line)
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
    register_fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER  # 612 x 792 pt เท่ากับ template.pdf ต้นฉบับ

    # กรอบเนื้อหา (ตรงกับ x=82..535 ของต้นฉบับ)
    x0 = 82
    x1 = 535

    def g(key, default=""):
        return data.get(key) or default

    def text(x, y, s, font="Sarabun", size=11, align="left"):
        c.setFont(font, size)
        s = s if s is not None else ""
        if align == "left":
            c.drawString(x, y, s)
        elif align == "center":
            c.drawCentredString(x, y, s)
        elif align == "right":
            c.drawRightString(x, y, s)

    def checkbox(x, y, checked, size=9):
        c.rect(x, y, size, size)
        if checked:
            c.setLineWidth(1.1)
            c.line(x, y, x + size, y + size)
            c.line(x, y + size, x + size, y)
            c.setLineWidth(0.6)

    # ===================== หัวเอกสาร (ไม่มีกรอบ เหมือนต้นฉบับ) =====================
    text(width / 2, 760, g("projectTitle", DEFAULTS["projectTitle"]), font="Sarabun-Bold", size=15.5, align="center")
    text(width / 2, 742, g("projectSubtitle", DEFAULTS["projectSubtitle"]), size=11, align="center")
    text(width / 2, 724, g("projectAddress", DEFAULTS["projectAddress"]), size=11, align="center")
    text(width / 2, 706, g("contractLine", DEFAULTS["contractLine"]), size=11, align="center")

    # จำนวนวันทำงาน / เหลือ
    text(x0, 687,
         f"จำนวนวันทำงาน {g('totalDays', DEFAULTS['totalDays'])} วัน.   เหลือ {g('remainingDays', '.......')} วัน.",
         size=11)

    # วันที่ / เดือน / พ.ศ.
    text(x0, 669,
         f"วันที่ {g('reportDay', '....')}   เดือน {g('reportMonth', '....................')}   พ.ศ. {g('reportYear', '.......')}",
         size=11)

    # สภาพอากาศ
    text(x0, 651, "สภาพอากาศ", font="Sarabun-Bold", size=11)
    weather_opts = ["แจ่มใส", "ฝนตก", "ฝนตกหนัก"]
    text(x0 + 62, 651, "เช้า", size=10.5)
    cx = x0 + 90
    for opt in weather_opts:
        checkbox(cx, 646.5, g("weatherAM") == opt)
        text(cx + 13, 651, opt, size=10.5)
        cx += 78
    text(x0 + 62, 633, "บ่าย", size=10.5)
    cx = x0 + 90
    for opt in weather_opts:
        checkbox(cx, 628.5, g("weatherPM") == opt)
        text(cx + 13, 633, opt, size=10.5)
        cx += 78

    # จำนวนรวม เจ้าหน้าที่ / เครื่องจักร (บรรทัดเหนือตาราง 1)
    total_y = 615
    text(x0, total_y, "จำนวนเจ้าหน้าที่ของผู้รับจ้าง", font="Sarabun-Bold", size=10.5)
    text(x0 + 148, total_y, f"{g('totalStaffCount', '.......')} คน", size=10.5)
    text(x0 + 253, total_y, "จำนวนเครื่องจักรของผู้รับจ้าง", font="Sarabun-Bold", size=10.5)
    text(x0 + 405, total_y, f"{g('totalEquipmentCount', '.......')} คน", size=10.5)

    # ===================== ตาราง 1: เจ้าหน้าที่ / เครื่องจักร (3 คู่คอลัมน์ x 7 แถว) =====================
    t1_top = total_y - 9
    header_h = 18
    row_h = 18.3
    t1_bottom = t1_top - header_h - TABLE1_ROW_COUNT * row_h

    col_edges = [x0, 184, 228, 316, 360, 491, x1]
    group_defs = [
        (col_edges[0], col_edges[1], col_edges[2], GROUP1_ROWS),
        (col_edges[2], col_edges[3], col_edges[4], GROUP2_ROWS),
        (col_edges[4], col_edges[5], col_edges[6], GROUP3_ROWS),
    ]

    c.setLineWidth(1)
    c.rect(x0, t1_bottom, x1 - x0, t1_top - t1_bottom)
    c.setLineWidth(0.5)
    for x in col_edges[1:-1]:
        c.line(x, t1_bottom, x, t1_top)
    c.line(x0, t1_top - header_h, x1, t1_top - header_h)
    for i in range(1, TABLE1_ROW_COUNT):
        ry = t1_top - header_h - i * row_h
        c.line(x0, ry, x1, ry)

    header_y = t1_top - header_h + 5.5
    for label_x, count_x, col_end, _rows in group_defs:
        text((label_x + count_x) / 2, header_y, "ประเภท", font="Sarabun-Bold", size=10, align="center")
        text((count_x + col_end) / 2, header_y, "คน", font="Sarabun-Bold", size=10, align="center")

    for row_i in range(TABLE1_ROW_COUNT):
        ry = t1_top - header_h - row_i * row_h - row_h + 6
        for label_x, count_x, col_end, rows in group_defs:
            if row_i < len(rows):
                key, fixed_label = rows[row_i]
                if key is None:
                    label_val = g("eq_other_label", "อื่นๆ")
                    count_val = g("eq_other_count", "")
                    text(label_x + 4, ry, label_val, size=9.5)
                    text((count_x + col_end) / 2, ry, count_val, size=9.5, align="center")
                else:
                    text(label_x + 4, ry, fixed_label, size=9.5)
                    count_val = g(key, "")
                    text((count_x + col_end) / 2, ry, count_val, size=9.5, align="center")

    # ===================== ตาราง 2: รายละเอียดงาน (3 คอลัมน์) =====================
    t2_top = t1_bottom - 12
    col2_edges = [x0, 184, 272.6, x1]
    header2_h = 33
    body2_h = 130
    t2_bottom = t2_top - header2_h - body2_h

    c.setLineWidth(1)
    c.rect(x0, t2_bottom, x1 - x0, t2_top - t2_bottom)
    c.setLineWidth(0.5)
    c.line(col2_edges[1], t2_top - header2_h, col2_edges[1], t2_bottom)
    c.line(col2_edges[2], t2_top, col2_edges[2], t2_bottom)
    c.line(x0, t2_top - 15.5, col2_edges[2], t2_top - 15.5)
    c.line(x0, t2_top - header2_h, x1, t2_top - header2_h)

    text((x0 + col2_edges[2]) / 2, t2_top - 11, "รายละเอียดของงานที่ผู้รับจ้างปฏิบัติงาน",
         font="Sarabun-Bold", size=10.5, align="center")
    text((col2_edges[2] + x1) / 2, t2_top - 20, "ปริมาณงานที่ทำได้", font="Sarabun-Bold", size=10.5, align="center")
    text((col2_edges[2] + x1) / 2, t2_top - 30, "(ถ้ามี เช่น จำนวน เมตร ตร.ม. งานแล้วเสร็จกี่ %)", size=8.5, align="center")

    text((x0 + col2_edges[1]) / 2, t2_top - 25, "งานที่ดำเนินการ", font="Sarabun-Bold", size=10, align="center")
    text((x0 + col2_edges[1]) / 2, t2_top - 33, "(ทำอะไรบ้าง)", size=9, align="center")
    text((col2_edges[1] + col2_edges[2]) / 2, t2_top - 29, "ตำแหน่งที่ดำเนินการ", font="Sarabun-Bold", size=10, align="center")

    body_top = t2_top - header2_h - 12
    for line in wrap_text(g("workDone"), "Sarabun", 9.5, col2_edges[1] - x0 - 8):
        text(x0 + 4, body_top, line, size=9.5)
        body_top -= 12
    body_top2 = t2_top - header2_h - 12
    for line in wrap_text(g("workLocation"), "Sarabun", 9.5, col2_edges[2] - col2_edges[1] - 8):
        text(col2_edges[1] + 4, body_top2, line, size=9.5)
        body_top2 -= 12
    body_top3 = t2_top - header2_h - 12
    for line in wrap_text(g("workQuantity"), "Sarabun", 9.5, x1 - col2_edges[2] - 8):
        text(col2_edges[2] + 4, body_top3, line, size=9.5)
        body_top3 -= 12

    # ===================== ตาราง 3: ปัญหา / แนวทางแก้ไข (2 คอลัมน์) =====================
    t3_top = t2_bottom - 22
    t3_body_h = 70
    t3_bottom = t3_top - t3_body_h
    mid3 = (x0 + x1) / 2

    text((x0 + mid3) / 2, t3_top + 10, "ปัญหา / อุปสรรค", font="Sarabun-Bold", size=10.5, align="center")
    text((mid3 + x1) / 2, t3_top + 10, "แนวทางการแก้ไข", font="Sarabun-Bold", size=10.5, align="center")

    c.setLineWidth(1)
    c.rect(x0, t3_bottom, x1 - x0, t3_top - t3_bottom)
    c.setLineWidth(0.5)
    c.line(mid3, t3_top, mid3, t3_bottom)
    row3_h = t3_body_h / 3
    for i in range(1, 3):
        ry = t3_top - i * row3_h
        c.line(x0, ry, x1, ry)

    py = t3_top - 12
    for line in wrap_text(g("problems"), "Sarabun", 9.5, mid3 - x0 - 8):
        text(x0 + 4, py, line, size=9.5)
        py -= 12
    sy = t3_top - 12
    for line in wrap_text(g("solutions"), "Sarabun", 9.5, x1 - mid3 - 8):
        text(mid3 + 4, sy, line, size=9.5)
        sy -= 12

    # ===================== ลายเซ็น (ไม่มีกรอบ) =====================
    sig_y = t3_bottom - 45
    c.setLineWidth(0.7)
    c.line(x0 + 45, sig_y, mid3 - 25, sig_y)
    text((x0 + 45 + mid3 - 25) / 2, sig_y - 14, f"({g('supervisorName', DEFAULTS['supervisorName'])})", size=10.5, align="center")
    text((x0 + 45 + mid3 - 25) / 2, sig_y - 28, "ผู้ควบคุมงาน", size=10.5, align="center")

    c.line(mid3 + 25, sig_y, x1 - 30, sig_y)
    text((mid3 + 25 + x1 - 30) / 2, sig_y - 14, f"{g('contractorRepName', DEFAULTS['contractorRepName'])}", size=10.5, align="center")
    text((mid3 + 25 + x1 - 30) / 2, sig_y - 28, "ผู้รับจ้าง/ตัวแทนผู้รับจ้าง", size=10.5, align="center")

    # ===================== หมายเหตุ =====================
    note_y = max(sig_y - 46, 40)
    c.setFont("Sarabun-Bold", 9.5)
    c.drawString(x0, note_y, "หมายเหตุ")
    underline_w = pdfmetrics.stringWidth("หมายเหตุ", "Sarabun-Bold", 9.5)
    c.setLineWidth(0.6)
    c.line(x0, note_y - 1.5, x0 + underline_w, note_y - 1.5)
    text(x0 + underline_w + 4, note_y, f"ครบกำหนดส่งมอบภายใน {g('dueDate', DEFAULTS['dueDate'])}", size=9.5)

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
