import os
import sys
import logging
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("AutoProtectionBot")

API_ID = int(os.getenv("API_ID", "32397550"))
API_HASH = os.getenv("API_HASH", "1888c6fb45d30c4a0e46e999ba26e051")
BOT_TOKEN = os.getenv("BOT_TOKEN", "9176238914:AAEpcJ4VLe4JRWtBt64l4cPyZs3g5YwhRtU")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "7802809841"))
SUPER_ADMIN_IDS = {7802809841, 1988538065, SUPER_ADMIN_ID}
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0")) if os.getenv("LOG_CHANNEL_ID") else 0

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://notimepass367_db_user:abbsy%40123@cluster0.wof2cvr.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
MONGO_DB_URI = MONGO_URI
DB_NAME = os.getenv("DB_NAME", "auto3_db")

WELCOME_IMAGE = os.getenv("WELCOME_IMAGE", "https://files.catbox.moe/gxdqrp.jpg")

DEFAULT_SCAMMER_DM = os.getenv(
    "SCAMMER_DM_MSG",
    "You are a scammer get out from my channel REG - @abbsyDurov"
)

UNAUTHORIZED_DM = os.getenv(
    "UNAUTHORIZED_DM_MSG",
    "YOU HAVE NO ACCESS con - @abbsyDurov"
)

PREMIUM_EMOJIS = {
    "mic": ("🎤", "6109651730872408665"),
    "diamond": ("💎", "5201914481671682382"),
    "crown": ("👑", "6165733687246720248"),
    "phone": ("📱", "5375388726153191850"),
    "selfie": ("🤳", "5233455639161750788"),
    "fire": ("🔥", "6165899765042122114"),
    "speech": ("💬", "5443038326535759644"),
    "write": ("✍️", "5197269100878907942"),
    "dizzy": ("💫", "6165894413512871182"),
    "finger_up": ("☝️", "5345986836038694332"),
    "check_box": ("✅", "6158935325247805180"),
    "zap": ("⚡️", "6165582852290252628"),
    "gear": ("⚙️", "5467687036473336725"),
    "studio_mic": ("🎙", "5474279751143136434"),
    "sparkles": ("✨", "6154310409974190743"),
    "thumbs_up": ("👍", "6183943695746732556"),
    "smile": ("😊", "6161213251347551709"),
    "owl": ("🦉", "6186045884669628967"),
    "duck": ("🦆", "6186020952384475802"),
    "money_face": ("🤑", "6186175669991379791"),
    "t_rex": ("🦖", "6186233896863011865"),
    "ninja": ("🥷", "6233527959307688784"),
    "sweat": ("😓", "6260478625687018336"),
    "check_mark": ("✔️", "6336628528989083013"),
    "eye": ("👁", "6336887317948536124"),
    "point_up": ("👆", "6161121450716566360"),
    "glowing_star": ("🌟", "6163600858027135317"),
    "chart_up": ("📈", "6109418703126796249"),
    "snowflake": ("❄️", "6109347707317393978"),
    "question": ("❔", "6163186994978492950"),
    "blue_circle": ("🔵", "6163336970941501725"),
    "blossom": ("🌸", "6165618590713122679"),
    "arrow_right": ("➡️", "6158868637290601091"),
    "yin_yang": ("☯️", "6163667490149765932"),
}

DEFAULT_PREMIUM_EMOJIS = PREMIUM_EMOJIS

def get_p_emoji(name: str) -> str:
    if name in PREMIUM_EMOJIS:
        char, custom_id = PREMIUM_EMOJIS[name]
        return f'<tg-emoji emoji-id="{custom_id}"></tg-emoji>'
    return name

def get_plain_emoji(name: str) -> str:
    if name in PREMIUM_EMOJIS:
        return PREMIUM_EMOJIS[name][0]
    return ""

def get_btn_emoji(name: str) -> str:
    if name in PREMIUM_EMOJIS:
        return PREMIUM_EMOJIS[name][0]
    return ""

def get_btn_emoji_id(name: str) -> Optional[str]:
    if name in PREMIUM_EMOJIS:
        return PREMIUM_EMOJIS[name][1] or None
    return None

def update_emoji_cache(name: str, char: str, emoji_id: str):
    PREMIUM_EMOJIS[name] = (char, str(emoji_id))

