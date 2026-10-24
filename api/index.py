# -*- coding: utf-8 -*-
"""
Entry point สำหรับ Vercel (@vercel/python)
Vercel จะมองหาตัวแปรชื่อ `app` ที่เป็น WSGI application ในไฟล์นี้โดยอัตโนมัติ
โค้ดจริงของแอปทั้งหมดยังอยู่ที่ app.py ที่ระดับบนสุดของโปรเจกต์ตามเดิม
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402  (ต้อง import หลังแก้ sys.path)
