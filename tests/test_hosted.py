import unittest
from app.hosted import validate_environment

class HostedTests(unittest.TestCase):
    def environment(self,**changes):
        return {'APP_MODE':'telegram','BOT_TOKEN':'test-only','WEBAPP_URL':'https://example.up.railway.app','DATABASE_PATH':'/data/birga.sqlite3',**changes}
    def test_valid_configuration(self):
        self.assertEqual(validate_environment(self.environment()),'https://example.up.railway.app')
    def test_reject_local_demo(self):
        with self.assertRaises(ValueError):validate_environment(self.environment(APP_MODE='demo'))
    def test_reject_missing_token(self):
        with self.assertRaises(ValueError):validate_environment(self.environment(BOT_TOKEN=''))
    def test_reject_http(self):
        with self.assertRaises(ValueError):validate_environment(self.environment(WEBAPP_URL='http://example.com'))
    def test_persistent_database_path(self):
        with self.assertRaises(ValueError):validate_environment(self.environment(DATABASE_PATH='data/local.sqlite3'))
    def test_railway_generated_domain(self):
        self.assertEqual(validate_environment(self.environment(WEBAPP_URL='',RAILWAY_PUBLIC_DOMAIN='demo.up.railway.app')),'https://demo.up.railway.app')
    def test_port_collision(self):
        with self.assertRaises(ValueError):validate_environment(self.environment(PORT='8081'))
    def test_no_credentials_in_url(self):
        with self.assertRaises(ValueError):validate_environment(self.environment(WEBAPP_URL='https://user:secret@example.com'))
