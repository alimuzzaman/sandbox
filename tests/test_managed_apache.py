import unittest

from tests.test_managed_services import Policy


class TestManagedApache(unittest.TestCase):
    def test_apache_php_database_lifecycle_is_container_scoped(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler
        result = ManagedServiceCompiler().compile(Policy(), web_server="apache")
        self.assertEqual(result["backend"], {"address": "10.203.0.2", "port": 8080})
        self.assertEqual(result["units"], (
            "mariadb.service", "php8.3-fpm.service", "apache2.service", "cron.service",
        ))
        self.assertTrue(all(path.startswith("/etc/") for path in result["files"]))


if __name__ == "__main__": unittest.main()
