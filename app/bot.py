"""Telegram bot: private commands, Mini App launch, persistent update offset/outbox."""
import json
import os
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse
from app import config
from app.store import connect, init, now


def api(method, payload):
    request=Request('https://api.telegram.org/bot'+config.TOKEN+'/'+method,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=40) as response:
        result=json.load(response)
    if not result.get('ok'):
        raise RuntimeError('Telegram API rejected request')
    return result['result']


def main():
    url=os.getenv('WEBAPP_URL','')
    if config.MODE!='telegram' or not config.TOKEN or urlparse(url).scheme!='https':
        raise SystemExit('Set APP_MODE=telegram, BOT_TOKEN and public HTTPS WEBAPP_URL in .env. Use a separate database.')
    init()
    if api('getWebhookInfo',{}).get('url'):
        raise SystemExit('This bot already has a webhook. Remove it deliberately before starting polling; existing setup was not changed.')
    api('setMyCommands',{'commands':[{'command':'start','description':'Открыть совместные покупки'},{'command':'orders','description':'Мои покупки'},{'command':'help','description':'Как это работает'},{'command':'support','description':'Поддержка'}]})
    api('setChatMenuButton',{'menu_button':{'type':'web_app','text':'Открыть покупки','web_app':{'url':url}}})
    print('Bot polling started. No real payments enabled.',flush=True)
    while True:
        try:
            with connect() as db:
                saved=db.execute("SELECT value FROM settings WHERE key='bot_offset'").fetchone()
            updates=api('getUpdates',{'offset':int(saved['value']) if saved else 0,'timeout':20,'allowed_updates':['message']})
            for update in updates:
                message=update.get('message',{})
                chat=message.get('chat',{})
                if chat.get('type')=='private' and message.get('text'):
                    command=message['text'].split()[0].split('@')[0]
                    text={
                        '/start':'Добро пожаловать в Birga! Объединяем покупателей для общей цены. Сейчас сервис готовится к пилоту: реальные платежи выключены.',
                        '/orders':'Ваши группы и покупки — в приложении.',
                        '/help':'1. Выберите группу и прочитайте полный пакет.\n2. После запуска пилота внесите возвратный платеж участия.\n3. Подтвердите предложение поставщика.\n4. Оплатите товар поставщику полностью; платеж участия вернется отдельно.\n\nСейчас доступен только предварительный режим.',
                        '/support':os.getenv('SUPPORT_CONTACT','Поддержка пока не подключена. Обратитесь к организатору пилота.')
                    }.get(command,'Откройте приложение или используйте /help.')
                    app_url=url+('#orders' if command=='/orders' else '')
                    try:
                        api('sendMessage',{'chat_id':chat['id'],'text':text,'reply_markup':{'inline_keyboard':[[{'text':'Открыть Birga','web_app':{'url':app_url}}]]}})
                    except HTTPError as error:
                        if error.code not in (400,403):
                            raise
                with connect(True) as db:
                    db.execute("INSERT INTO settings VALUES('bot_offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(update['update_id']+1),))
            with connect() as db:
                pending=[dict(r) for r in db.execute('SELECT * FROM notifications WHERE sent_at IS NULL AND attempts<5 AND next_attempt<=? ORDER BY id LIMIT 20',(now(),))]
            for event in pending:
                try:
                    api('sendMessage',{'chat_id':event['user_id'],'text':event['message']})
                    with connect(True) as db:
                        db.execute('UPDATE notifications SET sent_at=? WHERE id=?',(now(),event['id']))
                except Exception:
                    with connect(True) as db:
                        db.execute('UPDATE notifications SET attempts=attempts+1,next_attempt=? WHERE id=?',(now()+min(3600,30*2**event['attempts']),event['id']))
        except KeyboardInterrupt:
            break
        except Exception as error:
            # Do not print request URL: it contains the bot token.
            print('Bot connection issue:',type(error).__name__,flush=True)
            time.sleep(5)

if __name__=='__main__':
    main()
