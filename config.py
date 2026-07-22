import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

# Список Telegram ID администраторов (твой ID: 340174720)
ADMIN_IDS = [340174720]
admin_raw = os.getenv("ADMIN_IDS", "")
if admin_raw:
    for a_id in admin_raw.split(","):
        try:
            val = int(a_id.strip())
            if val not in ADMIN_IDS:
                ADMIN_IDS.append(val)
        except ValueError:
            pass

required_channels_raw = os.getenv("REQUIRED_CHANNELS", "")
REQUIRED_CHANNELS = []
if required_channels_raw:
    for c_id in required_channels_raw.split(","):
        c_id = c_id.strip()
        if c_id:
            try:
                REQUIRED_CHANNELS.append(int(c_id))
            except ValueError:
                if not c_id.startswith("@"):
                    c_id = "@" + c_id
                REQUIRED_CHANNELS.append(c_id)

DOWNLOAD_TEMP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "downloads"))
os.makedirs(DOWNLOAD_TEMP_DIR, exist_ok=True)
