import json
from app import config
from app.store import connect, now, notify, audit

class Problem(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def row(db, table, ident):
    result = db.execute(f'SELECT * FROM {table} WHERE id=?', (ident,)).fetchone()
    if not result:
        raise Problem('Запись не найдена.', 404)
    return dict(result)


def active_count(db, pool_id):
    return db.execute("SELECT count(*) FROM participations WHERE pool_id=? AND status IN ('RESERVED','ACCEPTED','ORDERED')", (pool_id,)).fetchone()[0]


def refund(db, participation_id, purpose, reason):
    payment = db.execute("SELECT * FROM payments WHERE participation_id=? AND purpose=? AND status='SUCCEEDED'", (participation_id,purpose)).fetchone()
    if payment:
        db.execute('INSERT OR IGNORE INTO refunds(payment_id,amount,status,reason,created_at) VALUES(?,?,?,?,?)', (payment['id'],payment['amount'],'SUCCEEDED',reason,now()))
        # Simulator only; the service never executes real money operations.


def snapshot(uid):
    with connect() as db:
        pools = []
        for item in db.execute('SELECT * FROM pools ORDER BY id'):
            p = dict(item)
            p['count'] = active_count(db,p['id'])
            p['winner'] = row(db,'offers',p['winner_id']) if p['winner_id'] else None
            p['participation'] = None
            part = db.execute('SELECT * FROM participations WHERE pool_id=? AND user_id=?', (p['id'],uid)).fetchone()
            if part:
                part = dict(part)
                part['payments'] = [dict(r) for r in db.execute('SELECT p.*,r.status AS refund_status FROM payments p LEFT JOIN refunds r ON r.payment_id=p.id WHERE participation_id=?', (part['id'],))]
                order = db.execute('SELECT * FROM orders WHERE participation_id=?', (part['id'],)).fetchone()
                part['order'] = dict(order) if order else None
                if order:
                    part['order']['cases'] = [dict(r) for r in db.execute('SELECT * FROM cases WHERE order_id=?', (order['id'],))]
                p['participation'] = part
            p['waitlisted'] = bool(db.execute('SELECT 1 FROM waitlist WHERE pool_id=? AND user_id=?', (p['id'],uid)).fetchone())
            pools.append(p)
        user = row(db,'users',uid)
        messages = [dict(r) for r in db.execute('SELECT id,message,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 30', (uid,))]
        return {'pools':pools,'user':user,'notifications':messages,'mode':config.MODE,'real_payments_enabled':False,'admin':config.MODE=='demo' or uid in config.ADMIN_IDS}


def mutate(uid, action, data, key):
    if not key or len(key)>128:
        raise Problem('Не указан ключ запроса.',400)
    payload = json.dumps({'action':action,'data':data},sort_keys=True,ensure_ascii=False)
    with connect(True) as db:
        previous = db.execute('SELECT * FROM requests WHERE user_id=? AND key=?',(uid,key)).fetchone()
        if previous:
            if previous['payload'] != payload:
                raise Problem('Ключ запроса уже использован для другого действия.')
            return json.loads(previous['response'])
        if config.MODE != 'demo':
            raise Problem('Денежные операции и группы пока не запущены. Сейчас доступен только просмотр.',403)
        result = perform(db,uid,action,data)
        audit(db,uid,action,data)
        db.execute('INSERT INTO requests VALUES(?,?,?,?)',(uid,key,payload,json.dumps(result,ensure_ascii=False)))
        return result


def perform(db,uid,action,data):
    if action == 'case':
        order = row(db,'orders',int(data.get('order_id',0)))
        part = row(db,'participations',order['participation_id'])
        if part['user_id'] != uid:
            raise Problem('Заказ не найден.',404)
        message = str(data.get('message','')).strip()
        if len(message)<10 or len(message)>2000:
            raise Problem('Опишите вопрос: от 10 до 2000 символов.',400)
        db.execute('INSERT INTO cases(order_id,user_id,message,status,created_at) VALUES(?,?,?,?,?)',(order['id'],uid,message,'OPEN',now()))
        notify(db,uid,'Обращение по заказу №%s зарегистрировано. В демо обращения никуда не отправляются.' % order['id'])
        return {'message':'Обращение сохранено в демо.'}
    pool = row(db,'pools',int(data.get('pool_id',0)))
    pid = pool['id']
    part = db.execute('SELECT * FROM participations WHERE pool_id=? AND user_id=?',(pid,uid)).fetchone()
    if action == 'join':
        if pool['status']!='COLLECTING' or pool['deadline']<=now():
            raise Problem('Набор в эту группу закрыт.')
        if not data.get('consent') or not data.get('eligible') or data.get('terms_version') != pool['terms_version']:
            raise Problem('Подтвердите условия и соответствие стандартному пакету.',400)
        if part:
            raise Problem('Участие в этой группе уже зарегистрировано. Повторное вступление в демо недоступно.')
        if active_count(db,pid)>=pool['capacity']:
            raise Problem('Все места заняты. Можно записаться в резерв.')
        ident = db.execute('INSERT INTO participations(pool_id,user_id,status,terms_version,created_at) VALUES(?,?,?,?,?)',(pid,uid,'RESERVED',pool['terms_version'],now())).lastrowid
        db.execute('INSERT INTO payments(participation_id,purpose,amount,status,created_at) VALUES(?,?,?,?,?)',(ident,'DEPOSIT',pool['deposit'],'SUCCEEDED',now()))
        notify(db,uid,'Тестовое участие подтверждено: %s. Реальные деньги не списывались.' % pool['title'])
        return {'message':'Вы в группе! Тестовый платеж подтвержден.'}
    if action == 'waitlist':
        if pool['status'] not in ('COLLECTING','SOURCING','OFFER_CONFIRMATION'):
            raise Problem('Резерв закрыт.')
        if part and part['status'] in ('RESERVED','ACCEPTED','ORDERED'):
            raise Problem('Вы уже участвуете в группе.')
        db.execute('INSERT OR IGNORE INTO waitlist(pool_id,user_id,created_at) VALUES(?,?,?)',(pid,uid,now()))
        return {'message':'Вы в бесплатном резерве. Деньги не требуются.'}
    if action == 'withdraw':
        if not part or part['status'] not in ('RESERVED','ACCEPTED'):
            raise Problem('Для оплаченного заказа используйте обращение по заказу.')
        db.execute("UPDATE participations SET status='WITHDRAWN' WHERE id=?",(part['id'],))
        refund(db,part['id'],'DEPOSIT','BUYER_WITHDRAWAL')
        db.execute("UPDATE orders SET status='CANCELLED' WHERE participation_id=? AND status='AWAITING_PAYMENT'",(part['id'],))
        notify(db,uid,'Участие отменено. Тестовый платеж участия возвращен полностью.')
        return {'message':'Участие отменено, тестовый возврат завершен.'}
    if action == 'accept':
        if not part or part['status']!='RESERVED' or pool['status']!='OFFER_CONFIRMATION':
            raise Problem('Сейчас нельзя подтвердить предложение.')
        offer = row(db,'offers',pool['winner_id'])
        if offer['valid_until']<=now():
            raise Problem('Срок предложения истек.')
        if data.get('offer_id') != offer['id']:
            raise Problem('Предложение изменилось. Обновите страницу.')
        db.execute("UPDATE participations SET status='ACCEPTED' WHERE id=?",(part['id'],))
        db.execute('INSERT INTO orders(participation_id,offer_id,amount,status,created_at) VALUES(?,?,?,?,?)',(part['id'],offer['id'],offer['price'],'AWAITING_PAYMENT',now()))
        return {'message':'Предложение подтверждено. Ждем открытия оплаты.'}
    if action == 'pay':
        if not part or part['status']!='ACCEPTED' or pool['status']!='PAYMENT_OPEN':
            raise Problem('Оплата еще не открыта.')
        offer = row(db,'offers',pool['winner_id'])
        if offer['valid_until']<=now():
            raise Problem('Срок предложения истек.')
        order = db.execute('SELECT * FROM orders WHERE participation_id=?',(part['id'],)).fetchone()
        if not order or order['status']!='AWAITING_PAYMENT':
            raise Problem('Счет уже обработан.')
        db.execute('INSERT INTO payments(participation_id,purpose,amount,status,created_at) VALUES(?,?,?,?,?)',(part['id'],'GOODS',order['amount'],'SUCCEEDED',now()))
        db.execute("UPDATE orders SET status='PAID_WAITING_BATCH' WHERE id=?",(order['id'],))
        db.execute("UPDATE participations SET status='ORDERED' WHERE id=?",(part['id'],))
        refund(db,part['id'],'DEPOSIT','GOODS_PAID')
        notify(db,uid,'Тестовая оплата поставщику подтверждена. Платеж участия возвращен отдельно; ожидаем фиксации партии.')
        return {'message':'Тестовая оплата учтена. Платеж участия возвращен.'}
    if action == 'receive':
        if not part:
            raise Problem('Заказ не найден.',404)
        order = db.execute('SELECT * FROM orders WHERE participation_id=?',(part['id'],)).fetchone()
        if not order or order['status']!='INSTALLATION_DONE':
            raise Problem('Сначала должны завершиться доставка и монтаж.')
        db.execute("UPDATE orders SET status='COMPLETED' WHERE id=?",(order['id'],))
        notify(db,uid,'Заказ завершен. Спасибо за участие в совместной покупке!')
        return {'message':'Получение и монтаж подтверждены.'}
    if action.startswith('admin_'):
        return admin_action(db,uid,action,pool,data)
    raise Problem('Неизвестное действие.',404)


def admin_action(db,uid,action,pool,data):
    # All these controls exist exclusively in demo mode, enforced in mutate().
    pid=pool['id']
    if action=='admin_advance':
        state=pool['status']
        if state=='COLLECTING':
            if active_count(db,pid)<pool['target']:
                raise Problem('Сначала нужно набрать целевое количество участников.')
            db.execute("UPDATE pools SET status='SOURCING' WHERE id=?",(pid,))
            return {'message':'Сбор предложений открыт.'}
        if state=='SOURCING':
            offers=list(db.execute('SELECT * FROM offers WHERE pool_id=? AND price<=? AND min_qty<=? AND max_qty>=? AND valid_until>? ORDER BY price,created_at,id',(pid,pool['cap'],pool['min_qty'],pool['capacity'],now()+86400)))
            if len(offers)<2:
                raise Problem('Нужно минимум два действительных предложения. Добавьте тестовые предложения.')
            db.execute("UPDATE pools SET status='OFFER_CONFIRMATION',winner_id=? WHERE id=?",(offers[0]['id'],pid))
            for p in db.execute("SELECT user_id FROM participations WHERE pool_id=? AND status='RESERVED'",(pid,)):
                notify(db,p['user_id'],'Выбрано тестовое предложение. Откройте группу и подтвердите итоговые условия.')
            return {'message':'Победитель выбран по минимальной полной цене.'}
        if state=='OFFER_CONFIRMATION':
            count=db.execute("SELECT count(*) FROM participations WHERE pool_id=? AND status='ACCEPTED'",(pid,)).fetchone()[0]
            if count<pool['min_qty']:
                raise Problem('Недостаточно подтверждений. В демо можно подтвердить тестовых участников.')
            db.execute("UPDATE pools SET status='PAYMENT_OPEN' WHERE id=?",(pid,))
            return {'message':'Оплата поставщику открыта.'}
        if state=='PAYMENT_OPEN':
            count=db.execute("SELECT count(*) FROM orders o JOIN participations p ON p.id=o.participation_id WHERE p.pool_id=? AND o.status='PAID_WAITING_BATCH'",(pid,)).fetchone()[0]
            if count<pool['min_qty']:
                raise Problem('Не достигнут минимум оплаченных заказов.')
            db.execute("UPDATE pools SET status='FULFILLMENT' WHERE id=?",(pid,))
            db.execute("UPDATE orders SET status='RELEASED' WHERE status='PAID_WAITING_BATCH' AND participation_id IN (SELECT id FROM participations WHERE pool_id=?)",(pid,))
            # Unpaid orders are cancelled and their participation payments refunded.
            for p in db.execute("SELECT * FROM participations WHERE pool_id=? AND status IN ('RESERVED','ACCEPTED')",(pid,)).fetchall():
                refund(db,p['id'],'DEPOSIT','PAYMENT_DEADLINE')
                db.execute("UPDATE participations SET status='EXPIRED' WHERE id=?",(p['id'],))
                db.execute("UPDATE orders SET status='CANCELLED' WHERE participation_id=?",(p['id'],))
            return {'message':'Партия зафиксирована. Неоплаченные участия закрыты.'}
        if state=='FULFILLMENT':
            db.execute("UPDATE orders SET status='INSTALLATION_DONE' WHERE status='RELEASED' AND participation_id IN (SELECT id FROM participations WHERE pool_id=?)",(pid,))
            for p in db.execute("SELECT user_id FROM participations WHERE pool_id=? AND status='ORDERED'",(pid,)):
                notify(db,p['user_id'],'Тестовая доставка и монтаж завершены. Подтвердите получение или откройте обращение.')
            return {'message':'Тестовая доставка и монтаж завершены.'}
        raise Problem('Для этой стадии переход не предусмотрен.')
    if action=='admin_offers':
        if pool['status']!='SOURCING':
            raise Problem('Сначала откройте сбор предложений.')
        for name,factor in [('Demo Climate',98),('Demo Comfort',96),('Demo Home',97)]:
            db.execute('INSERT OR IGNORE INTO offers(pool_id,supplier,price,min_qty,max_qty,warranty,delivery,valid_until,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(pid,name,pool['cap']*factor//100,pool['min_qty'],pool['capacity'],'2 года','Доставка и стандартный монтаж в течение 7 дней',now()+3*86400,now()))
        return {'message':'Добавлены 3 явно тестовых предложения.'}
    if action=='admin_fixtures':
        if pool['status'] not in ('OFFER_CONFIRMATION','PAYMENT_OPEN'):
            raise Problem('Тестовые участники действуют на этапе подтверждения или оплаты.')
        # Only seed user IDs are simulated, never the real demo visitor.
        for p in db.execute('SELECT * FROM participations WHERE pool_id=? AND user_id BETWEEN 1001 AND 1019',(pid,)).fetchall():
            if pool['status']=='OFFER_CONFIRMATION' and p['status']=='RESERVED':
                perform(db,p['user_id'],'accept',{'pool_id':pid,'offer_id':pool['winner_id']})
            if pool['status']=='PAYMENT_OPEN' and p['status']=='ACCEPTED':
                perform(db,p['user_id'],'pay',{'pool_id':pid})
        return {'message':'Тестовые участники выполнили текущий шаг.'}
    if action=='admin_cancel':
        if pool['status'] not in ('COLLECTING','SOURCING','OFFER_CONFIRMATION','PAYMENT_OPEN'):
            raise Problem('После фиксации партии нужен индивидуальный процесс возврата.')
        db.execute("UPDATE pools SET status='CANCELLED' WHERE id=?",(pid,))
        for p in db.execute('SELECT * FROM participations WHERE pool_id=?',(pid,)).fetchall():
            refund(db,p['id'],'DEPOSIT','POOL_CANCELLED')
            refund(db,p['id'],'GOODS','POOL_CANCELLED')
            db.execute("UPDATE participations SET status='CANCELLED' WHERE id=?",(p['id'],))
            db.execute("UPDATE orders SET status='CANCELLED' WHERE participation_id=?",(p['id'],))
            notify(db,p['user_id'],'Группа отменена. Все тестовые оплаты возвращены.')
        return {'message':'Группа отменена, тестовые оплаты возвращены.'}
    raise Problem('Неизвестное действие оператора.',404)
