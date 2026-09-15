import json
import mimetypes
import os
import secrets
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from app import config, service
from app.security import validate_init_data, AuthError
from app.store import init, connect, now, session_for

class Handler(BaseHTTPRequestHandler):
    server_version = 'GroupBuy'

    def log_message(self, fmt, *args):
        # Never log tokens, initData or request bodies.
        print('%s %s' % (self.command, urlparse(self.path).path))

    def respond(self, status, value, cookie=None):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if cookie:
            self.send_header('Set-Cookie',cookie)
        self.end_headers()
        self.wfile.write(body)

    def user(self):
        jar=cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie',''))
            token=jar['session'].value
        except (KeyError,cookies.CookieError):
            raise service.Problem('Войдите через Telegram или демо-вход.',401)
        with connect() as db:
            entry=db.execute('SELECT user_id FROM sessions WHERE token=? AND expires_at>?',(token,now())).fetchone()
        if not entry:
            raise service.Problem('Сессия истекла. Перезагрузите приложение.',401)
        return entry['user_id']

    def do_GET(self):
        path=urlparse(self.path).path
        try:
            if path=='/api/config':
                return self.respond(200,{'mode':config.MODE,'real_payments_enabled':False})
            if path=='/api/state':
                return self.respond(200,service.snapshot(self.user()))
            if path=='/health':
                return self.respond(200,{'ok':True,'mode':config.MODE})
            allowed={'/':'index.html','/app.js':'app.js','/styles.css':'styles.css','/art.svg':'art.svg'}
            if path not in allowed:
                return self.respond(404,{'error':'Не найдено'})
            target=config.ROOT/'web'/allowed[path]
            content=target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            self.send_header('Cache-Control','no-cache')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)
        except service.Problem as e:
            self.respond(e.status,{'error':str(e)})

    def do_POST(self):
        try:
            # Non-simple header plus same-origin checks prevent cross-site mutations.
            if self.headers.get('X-Requested-With')!='GroupBuy':
                raise service.Problem('Недопустимый запрос.',403)
            origin=self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                raise service.Problem('Недопустимый источник запроса.',403)
            size=int(self.headers.get('Content-Length','0'))
            if size>32768 or size<0:
                raise service.Problem('Запрос слишком большой.',413)
            data=json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(data,dict):
                raise service.Problem('Ожидается объект JSON.',400)
            path=urlparse(self.path).path
            if path=='/api/auth':
                if config.MODE=='demo':
                    user={'id':1,'first_name':'Гость'}
                else:
                    user=validate_init_data(data.get('initData',''),config.TOKEN)
                token=session_for(user)
                secure='; Secure' if config.MODE=='telegram' else ''
                return self.respond(200,{'ok':True},'session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=3600%s' % (token,secure))
            if path.startswith('/api/actions/'):
                result=service.mutate(self.user(),path.rsplit('/',1)[-1],data,self.headers.get('Idempotency-Key',''))
                return self.respond(200,result)
            self.respond(404,{'error':'Не найдено'})
        except AuthError as e:
            self.respond(401,{'error':str(e)})
        except service.Problem as e:
            self.respond(e.status,{'error':str(e)})
        except (ValueError,TypeError,KeyError):
            self.respond(400,{'error':'Некорректные параметры запроса.'})
        except Exception as e:
            print('Request failed:',type(e).__name__)
            self.respond(500,{'error':'Ошибка сервера. Обновите страницу и проверьте состояние перед повтором.'})


def main():
    host=os.getenv('HOST','127.0.0.1')
    if config.MODE=='demo' and host not in ('127.0.0.1','localhost','::1'):
        raise RuntimeError('Demo mode must bind to loopback; it exposes simulator controls.')
    if config.MODE=='telegram' and not config.TOKEN:
        raise RuntimeError('BOT_TOKEN is required in Telegram mode.')
    init()
    port=int(os.getenv('PORT','8080'))
    print('GroupBuy: http://%s:%s (%s; real payments disabled)' % (host,port,config.MODE),flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()

if __name__=='__main__':
    main()
