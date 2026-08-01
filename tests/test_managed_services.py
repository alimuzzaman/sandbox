import unittest


class Policy:
    machine_id = "sb-0123456789ab"; digest = "a" * 64
    network = {"guest_address": "10.203.0.2/30"}
    resources = {"connections": 128}


class TestManagedServices(unittest.TestCase):
    def test_nginx_stack_is_wholly_inside_guest_and_database_has_no_network(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler
        result = ManagedServiceCompiler().compile(Policy(), web_server="nginx")
        web = result["files"]["/etc/nginx/sites-enabled/sandbox.conf"]
        database = result["files"]["/etc/mysql/mariadb.conf.d/90-sandbox.cnf"]
        self.assertIn("listen 10.203.0.2:8080", web)
        self.assertIn("fastcgi_pass unix:/run/php/sandbox.sock", web)
        self.assertIn("skip-networking=1", database)
        self.assertIn("mariadb.service", result["units"])

    def test_apache_variant_uses_same_php_database_and_backend_contract(self):
        from sandbox.runtimes.managed.services import ManagedServiceCompiler
        result = ManagedServiceCompiler().compile(Policy(), web_server="apache")
        web = result["files"]["/etc/apache2/sites-enabled/000-sandbox.conf"]
        self.assertIn("VirtualHost 10.203.0.2:8080", web)
        self.assertIn("proxy:unix:/run/php/sandbox.sock", web)
        self.assertIn("apache2.service", result["units"])
        self.assertNotIn("nginx.service", result["units"])


if __name__ == "__main__": unittest.main()
