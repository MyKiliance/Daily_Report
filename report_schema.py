"""Editable captions shared by the form and PDF renderer."""
LABELS = {
    'totalDaysLabel': 'จำนวนวันทำงาน', 'remainingLabel': 'เหลือ',
    'dayUnit': 'วัน.', 'dateLabel': 'วันที่', 'monthLabel': 'เดือน', 'yearLabel': 'พ.ศ.',
    'weatherClearLabel': 'แจ่มใส', 'weatherRainLabel': 'ฝนตก', 'weatherHeavyLabel': 'ฝนตกหนัก',
    'weatherLabel': 'สภาพอากาศ', 'morningLabel': 'เช้า', 'afternoonLabel': 'บ่าย',
    'staffTotalLabel': 'จำนวนเจ้าหน้าที่ของผู้รับจ้าง',
    'equipmentTotalLabel': 'จำนวนเครื่องจักรของผู้รับจ้าง',
    'typeLabel': 'ประเภท', 'countUnit': 'คน',
    'workHeading': 'รายละเอียดของงานที่ผู้รับจ้างปฏิบัติงาน',
    'workDoneLabel': 'งานที่ดำเนินการ (ทำอะไรบ้าง)',
    'workLocationLabel': 'ตำแหน่งที่ดำเนินการ',
    'workQuantityLabel': 'ปริมาณงานที่ทำได้',
    'quantityHint': '(ถ้ามี เช่น จำนวน เมตร ตร.ม. งานแล้วเสร็จกี่ %)',
    'problemsLabel': 'ปัญหา / อุปสรรค', 'solutionsLabel': 'แนวทางการแก้ไข',
    'supervisorRole': 'ผู้ควบคุมงาน', 'contractorRole': 'ผู้รับจ้าง/ตัวแทนผู้รับจ้าง',
    'noteLabel': 'หมายเหตุ', 'dueLabel': 'ครบกำหนดส่งมอบภายใน',
}

GROUPS = [
    [('staff_office', 'เจ้าหน้าที่ประจำสำนักงาน'), ('staff_supervisor', 'นายช่างคุมงาน'),
     ('staff_carpenter', 'ช่างไม้'), ('staff_steel', 'ช่างเหล็ก'), ('staff_mason', 'ช่างปูน'),
     ('staff_labourer', 'กรรมกร'), ('staff_painter', 'ช่างทาสี')],
    [('eq_excavator', 'รถขุด'), ('eq_sixwheel', 'รถหกล้อ'), ('eq_crane', 'Mobile Crane'),
     ('eq_other_count', 'อื่นๆ(...........................)')],
    [('eq_roofframe', 'ช่างโครงหลังคาเหล็ก'), ('staff_electrician', 'ช่างไฟฟ้า')],
]

def label_key(key):
    return 'eq_other_label' if key == 'eq_other_count' else key + '_label'

EXTRA_FIELDS = list(LABELS) + [label_key(k) for group in GROUPS for k, _ in group
                             if label_key(k) != 'eq_other_label'] + ['extraRows', 'remainingMode', 'updated_at']

def captions(data):
    return {key: str(data[key]) if data.get(key) is not None else value
            for key, value in LABELS.items()}
