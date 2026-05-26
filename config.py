# -*- coding: utf-8 -*-
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SHOP_NAME = os.getenv("SHOP_NAME", "Key Bot Store").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support").strip()

ADMIN_IDS = [
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
]

BANK_CODE = os.getenv("BANK_CODE", "BIDV").strip()
BANK_ACCOUNT = os.getenv("BANK_ACCOUNT", "").strip()
BANK_NAME = os.getenv("BANK_NAME", "").strip()
DB_PATH = os.getenv("DB_PATH", "shop.db").strip()
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0").strip()
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "9000").strip())
SEPAY_WEBHOOK_SECRET = os.getenv("SEPAY_WEBHOOK_SECRET", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Thiếu BOT_TOKEN trong file .env")
