import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

class AuthError(Exception):
    pass

def validate_init_data(raw, token, now=None):
    if not token or not raw or len(raw) > 16384:
        raise AuthError('Откройте приложение через Telegram-бота.')
    pairs = parse_qsl(raw, keep_blank_values=True)
    if len({key for key, _ in pairs}) != len(pairs):
        raise AuthError('Некорректная подпись Telegram.')
    fields = dict(pairs)
    supplied = fields.pop('hash', '')
    check = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise AuthError('Некорректная подпись Telegram.')
    try:
        age = (now if now is not None else time.time()) - int(fields['auth_date'])
        user = json.loads(fields['user'])
        if age < -30 or age > 300 or not isinstance(user['id'], int) or user['id'] <= 0:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise AuthError('Сессия Telegram устарела. Откройте приложение заново.')
    return user
