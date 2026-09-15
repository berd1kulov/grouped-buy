import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def load_env():
    path = ROOT / '.env'
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))

load_env()
MODE = os.getenv('APP_MODE', 'demo')
if MODE not in ('demo', 'telegram'):
    raise RuntimeError('APP_MODE must be demo or telegram')
DB_PATH = os.getenv('DATABASE_PATH', str(ROOT / 'data/groupbuy.sqlite3'))
TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()}
