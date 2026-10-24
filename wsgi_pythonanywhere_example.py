# ============================================================================
# ไฟล์ตัวอย่างสำหรับ PythonAnywhere โดยเฉพาะ
#
# วิธีใช้:
# 1. ไปที่แท็บ "Web" ในบัญชี PythonAnywhere ของคุณ
# 2. คลิกลิงก์ไฟล์ WSGI configuration file (ชื่อประมาณ
#    /var/www/<your-username>_pythonanywhere_com_wsgi.py)
# 3. ลบเนื้อหาเดิมทั้งหมด แล้ววางเนื้อหาด้านล่างนี้แทน
# 4. แก้ "yourusername" ให้เป็นชื่อผู้ใช้ PythonAnywhere ของคุณ
# 5. กด Save แล้วกลับไปกด "Reload" ที่แท็บ Web
# ============================================================================
import sys

# แก้ yourusername ให้เป็นชื่อผู้ใช้ PythonAnywhere ของคุณ
project_home = '/home/yourusername/daily-report'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import app as application  # noqa
