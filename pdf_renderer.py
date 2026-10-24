"""Letter report laid out against template.pdf, with lossless continuation pages."""
import io
import json
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from report_schema import GROUPS, captions, label_key


def render_report(data, defaults, register_fonts, wrap_text):
    register_fonts()
    stream = io.BytesIO()
    c = canvas.Canvas(stream, pagesize=(612, 792))
    c.setTitle(str(data.get('projectTitle', defaults['projectTitle'])))
    labels = captions(data)
    overflow = []

    def value(key):
        # Empty text is intentional; only missing/null legacy values use defaults.
        return str(data[key]) if data.get(key) is not None else str(defaults.get(key, ''))

    def line(x, top, right, bottom):
        c.setLineWidth(.5)
        c.line(x, 792-top, right, 792-bottom)

    def text(x, top, content, size=9, width=None, center=False, bold=False, record_overflow=True):
        content = str(content)
        font = 'Sarabun-Bold' if bold else 'Sarabun'
        if width and pdfmetrics.stringWidth(content, font, size) > width:
            size = max(7, size * width / pdfmetrics.stringWidth(content, font, size))
        if width and pdfmetrics.stringWidth(content, font, size) > width:
            pieces = wrap_text(content, font, size, width)
            content = pieces[0]
            if record_overflow:
                overflow.append(('ข้อความประกอบเอกสาร', pieces[1:]))
        c.setFont(font, size)
        if center:
            c.drawCentredString(x, 792-top, content)
        else:
            c.drawString(x, 792-top, content)

    def box_text(key, x, top, width, height, title=None, size=9, center=False, prefix="", suffix="", step=12):
        content = prefix + value(key) + suffix
        lines = wrap_text(content, 'Sarabun', size, width-8)
        capacity = max(1, int((height-11)//step)+1)
        for i, item in enumerate(lines[:capacity]):
            text(x+width/2 if center else x+4, top+11+i*step, item, size, center=center)
        if len(lines) > capacity:
            overflow.append((title or key, lines[capacity:]))

    # Four editable header lines occupy the original template positions.
    for i, key in enumerate(['projectTitle', 'projectSubtitle', 'projectAddress', 'contractLine']):
        box_text(key, 82, 14+i*18.25, 453, 18.25, key, 10 if i == 0 else 9, center=True)
    text(208, 101, f"{labels['totalDaysLabel']}  {value('totalDays')}  {labels['dayUnit']}     "
         f"{labels['remainingLabel']}  {value('remainingDays')}  {labels['dayUnit']}", width=325)
    text(252, 119, f"{labels['dateLabel']}  {value('reportDay')}   {labels['monthLabel']}  "
         f"{value('reportMonth')}   {labels['yearLabel']}  {value('reportYear')}", width=280)
    text(222, 137, labels['weatherLabel'], width=70)
    for i, (key, title) in enumerate([('weatherAM', 'morningLabel'), ('weatherPM', 'afternoonLabel')]):
        y = 137+i*18.25
        text(286, y, labels[title], width=32)
        for j, option in enumerate(['แจ่มใส', 'ฝนตก', 'ฝนตกหนัก']):
            x = 324+j*72
            c.rect(x, 792-y-1, 8, 8)
            if value(key) == option:
                line(x, y-7, x+8, y+1); line(x, y+1, x+8, y-7)
            text(x+13, y, labels[['weatherClearLabel','weatherRainLabel','weatherHeavyLabel'][j]], 8.5, width=55)
    text(118, 174, f"{labels['staffTotalLabel']}  {value('totalStaffCount')} {labels['countUnit']}", width=210)
    text(335, 174, f"{labels['equipmentTotalLabel']}  {value('totalEquipmentCount')} {labels['countUnit']}", width=198)

    edges = [82, 184.5, 228.3, 315.9, 359.8, 491.2, 535]
    top, row_h = 178.9, 18.25
    for x in edges:
        line(x, top, x, top+row_h*8)
    for i in range(9):
        line(82, top+i*row_h, 535, top+i*row_h)
    extra = json.loads(value('extraRows') or '[]')
    groups = [[(value(label_key(k)) if data.get(label_key(k)) is not None else title, value(k))
               for k, title in group] for group in GROUPS]
    for row in extra:
        groups[row['group']].append((row['label'], row['count']))
    for g, rows in enumerate(groups):
        left, split, right = edges[g*2:g*2+3]
        text((left+split)/2, top+13, labels['typeLabel'], center=True, width=split-left-6)
        text((split+right)/2, top+13, labels['countUnit'], center=True, width=right-split-6)
        for i, (title, count) in enumerate(rows[:7]):
            lines = wrap_text(title, 'Sarabun', 8, split-left-6)
            # Long custom labels continue in the appendix instead of crossing columns.
            text(left+3, top+(i+1)*row_h+12, lines[0], 8)
            if len(lines)>1: overflow.append(('รายละเอียดประเภท (ต่อ)', lines[1:]+['จำนวน: '+count]))
            text((split+right)/2, top+(i+1)*row_h+12, count, 8, width=right-split-6, center=True)
        if len(rows)>7:
            overflow.append(('รายการเพิ่มเติม '+str(g+1), [title+' : '+count for title,count in rows[7:]]))

    # Work table: exact outer edges and ruled rows from the supplied PDF.
    for x in [82, 272.2, 535]: line(x, 343, x, 544)
    line(184.5, 361.3, 184.5, 544)
    for y in [343, 397.8, 416, 434.3, 452.5, 470.8, 489, 507.2, 525.5, 544]: line(82,y,535,y)
    line(82, 361.3, 272.2, 361.3)
    text(177, 355.5, labels['workHeading'], 8, width=182, center=True)
    text(133, 380, labels['workDoneLabel'], 8, width=95, center=True)
    text(228, 386, labels['workLocationLabel'], 8, width=81, center=True)
    text(404, 364, labels['workQuantityLabel'], 9, width=250, center=True)
    text(404, 381, labels['quantityHint'], 8, width=250, center=True)
    for key, x, width in [('workDone',82,102.5),('workLocation',184.5,87.7),('workQuantity',272.2,262.8)]:
        box_text(key,x,398,width,146,labels[key+'Label'],step=18.25)
    text(175,575,labels['problemsLabel'],center=True,width=180)
    text(404,575,labels['solutionsLabel'],center=True,width=250)
    for x in [82,272.2,535]: line(x,580.2,x,635)
    for y in [580.2,598.5,616.7,635]: line(82,y,535,y)
    box_text('problems',82,580.2,190.2,54.8,labels['problemsLabel'],step=18.25)
    box_text('solutions',272.2,580.2,262.8,54.8,labels['solutionsLabel'],step=18.25)
    line(169,685,265,685); line(375,685,477,685)
    box_text('supervisorName',148,690,135,18,'ชื่อผู้ควบคุมงาน',9,center=True,prefix='(',suffix=')')
    text(216,721,labels['supervisorRole'],center=True,width=170)
    text(426,703,labels['contractorRole'],center=True,width=205)
    box_text('contractorRepName',333,708,202,18,'ชื่อผู้รับจ้าง',9,center=True)
    text(83,756,labels['noteLabel'],8.5,width=38)
    box_text('dueDate',125,745,410,18,labels['dueLabel'],8.5, prefix=labels['dueLabel']+' ')

    # Extra pages have a repeated heading and page numbers. Never drop overflow.
    page = 1
    if overflow:
        text(535,778,'มีรายละเอียดต่อหน้าถัดไป',8,width=180,center=True)
    c.showPage()
    y = 65
    def continuation():
        nonlocal page, y
        page += 1
        text(306,30,'รายละเอียดเพิ่มเติม — '+value('projectTitle'),10,center=True,width=480,record_overflow=False)
        text(82,48,f"{value('reportDay')} {value('reportMonth')} {value('reportYear')}",9)
        text(520,778,str(page),8)
        y = 70
    if overflow: continuation()
    for title, lines in overflow:
        if y > 715:
            c.showPage(); continuation()
        for heading in wrap_text(title, 'Sarabun-Bold', 10, 445):
            if y > 740: c.showPage(); continuation()
            text(82,y,heading,10,bold=True); y += 14
        for item in lines:
            for fragment in wrap_text(item,'Sarabun',9,445):
                if y > 740:
                    c.showPage(); continuation()
                text(86,y,fragment); y += 12
        y += 16
    if overflow: c.showPage()
    c.save()
    return stream.getvalue()
