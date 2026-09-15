import hashlib
import hmac
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch
from app import config, service, store
from app.security import AuthError, validate_init_data

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(config,'DB_PATH',str(Path(self.tmp.name)/'test.sqlite3'))
        self.modepatch=patch.object(config,'MODE','demo')
        self.dbpatch.start();self.modepatch.start();store.init();store.session_for({'id':1,'first_name':'Tester'})
        self.seq=0
    def tearDown(self):
        self.dbpatch.stop();self.modepatch.stop();self.tmp.cleanup()
    def act(self,action,uid=1,**data):
        self.seq+=1
        return service.mutate(uid,action,{'pool_id':1,**data},'test-%s-%s'%(uid,self.seq))
    def join(self,uid=1):
        return self.act('join',uid,consent=True,eligible=True,terms_version='pilot-v1')
    def pool(self):
        return service.snapshot(1)['pools'][0]
    def offers(self):
        self.join();self.act('admin_advance');self.act('admin_offers');self.act('admin_advance')
    def payment_open(self):
        self.offers();self.act('accept',offer_id=self.pool()['winner']['id']);self.act('admin_fixtures');self.act('admin_advance')
    def test_full_flow_and_separate_refund(self):
        self.payment_open();self.act('pay')
        p=self.pool();self.assertEqual(p['participation']['order']['amount'],432000000)
        self.assertEqual(p['participation']['order']['status'],'PAID_WAITING_BATCH')
        self.assertEqual(p['participation']['payments'][0]['refund_status'],'SUCCEEDED')
        self.act('admin_fixtures');self.act('admin_advance');self.act('admin_advance');self.act('receive')
        self.assertEqual(self.pool()['participation']['order']['status'],'COMPLETED')
    def test_idempotency_no_double_payment(self):
        data={'pool_id':1,'consent':True,'eligible':True,'terms_version':'pilot-v1'}
        first=service.mutate(1,'join',data,'fixed')
        self.assertEqual(first,service.mutate(1,'join',data,'fixed'))
        self.assertEqual(self.pool()['count'],20)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM payments WHERE participation_id=?',(self.pool()['participation']['id'],)).fetchone()[0],1)
    def test_idempotency_payload_conflict(self):
        service.mutate(1,'waitlist',{'pool_id':1},'fixed')
        with self.assertRaises(service.Problem):service.mutate(1,'waitlist',{'pool_id':2},'fixed')
    def test_consent_and_terms_required(self):
        with self.assertRaises(service.Problem):self.act('join',consent=True,eligible=True,terms_version='old')
        self.assertEqual(self.pool()['count'],19)
    def test_minimum_enforced(self):
        with self.assertRaises(service.Problem):self.act('admin_advance')
    def test_competition_required(self):
        self.join();self.act('admin_advance')
        with self.assertRaises(service.Problem):self.act('admin_advance')
    def test_expired_offer_rejected(self):
        self.offers()
        with store.connect(True) as db:db.execute('UPDATE offers SET valid_until=0')
        with self.assertRaises(service.Problem):self.act('accept',offer_id=self.pool()['winner']['id'])
    def test_withdraw_full_refund(self):
        self.join();self.act('withdraw')
        p=self.pool();self.assertEqual(p['count'],19)
        self.assertEqual(p['participation']['payments'][0]['refund_status'],'SUCCEEDED')
        with self.assertRaises(service.Problem):self.act('withdraw')
    def test_cancellation_refunds_both_payments(self):
        self.payment_open();self.act('pay');self.act('admin_cancel')
        p=self.pool();self.assertEqual(p['status'],'CANCELLED')
        self.assertTrue(all(x['refund_status']=='SUCCEEDED' for x in p['participation']['payments']))
    def test_release_requires_paid_minimum(self):
        self.payment_open();self.act('pay')
        with self.assertRaises(service.Problem):self.act('admin_advance')
    def test_cannot_receive_before_installation(self):
        self.payment_open();self.act('pay')
        with self.assertRaises(service.Problem):self.act('receive')
    def test_other_user_cannot_access_order(self):
        self.payment_open();order=self.pool()['participation']['order']
        with self.assertRaises(service.Problem):self.act('case',uid=1001,order_id=order['id'],message='Чужое обращение по заказу')
    def test_concurrent_capacity(self):
        for uid in range(30,40):store.session_for({'id':uid,'first_name':'Test'})
        def attempt(uid):
            try:
                service.mutate(uid,'join',{'pool_id':1,'consent':True,'eligible':True,'terms_version':'pilot-v1'},'join')
                return True
            except service.Problem:return False
        with ThreadPoolExecutor(max_workers=10) as executor:success=list(executor.map(attempt,range(30,40)))
        self.assertEqual(sum(success),6);self.assertEqual(self.pool()['count'],25)
    def test_real_mode_money_operations_blocked(self):
        with patch.object(config,'MODE','telegram'):
            with self.assertRaises(service.Problem):self.join()
    def test_mode_database_isolation(self):
        with patch.object(config,'MODE','telegram'):
            with self.assertRaises(RuntimeError):store.init()
    def test_expired_pool_cannot_join(self):
        with store.connect(True) as db:db.execute('UPDATE pools SET deadline=0 WHERE id=1')
        with self.assertRaises(service.Problem):self.join()

class AuthTests(unittest.TestCase):
    def signed(self,stamp=1000):
        fields={'auth_date':str(stamp),'user':json.dumps({'id':123,'first_name':'Test'}),'query_id':'abc'}
        check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
        secret=hmac.new(b'WebAppData',b'fake-token',hashlib.sha256).digest()
        fields['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        return urlencode(fields)
    def test_valid(self):self.assertEqual(validate_init_data(self.signed(),'fake-token',now=1001)['id'],123)
    def test_forged(self):
        with self.assertRaises(AuthError):validate_init_data(self.signed(),'wrong-token',now=1001)
    def test_expired(self):
        with self.assertRaises(AuthError):validate_init_data(self.signed(),'fake-token',now=1400)
    def test_duplicate_fields(self):
        with self.assertRaises(AuthError):validate_init_data(self.signed()+'&auth_date=1000','fake-token',now=1001)
    def test_future(self):
        with self.assertRaises(AuthError):validate_init_data(self.signed(1100),'fake-token',now=1000)

if __name__=='__main__':unittest.main()
