import unittest


VERSIONS = {"systemd-container": "255.4", "bubblewrap": "0.9.0", "nftables": "1.0.9",
            "debootstrap": "1.0", "e2fsprogs": "1.47", "php8.3-fpm": "8.3.6",
            "php8.3-cli": "8.3.6", "php8.3-mysql": "8.3.6", "php8.3-curl": "8.3.6",
            "php8.3-gd": "8.3.6", "php8.3-mbstring": "8.3.6", "php8.3-xml": "8.3.6",
            "php8.3-zip": "8.3.6", "php8.3-intl": "8.3.6", "php8.3-opcache": "8.3.6",
            "mariadb-server": "1:10.11.8", "mariadb-client": "1:10.11.8",
            "cron": "3.0", "ca-certificates": "2024", "curl": "8.5", "unzip": "6.0",
            "git": "2.43", "composer": "2.7", "nginx": "1.24.0", "apache2": "2.4.58"}


class TestManagedPackagePlan(unittest.TestCase):
    def planner(self, *, versions=None, sources=None):
        from sandbox.runtimes.managed.packages import ManagedPackagePlanner
        rows = versions or VERSIONS
        return ManagedPackagePlanner(
            simulate=lambda scope, names: ({"name": name, "version": rows.get(name),
                                            "action": "install", "scope": scope}
                                           for name in names),
            sources=lambda: sources or ({"uri": "http://archive.ubuntu.com/ubuntu",
                                         "suite": "noble", "signed": True},),
        )

    def test_exact_nginx_and_apache_plans_are_digest_bound(self):
        nginx = self.planner().plan(web_server="nginx")
        apache = self.planner().plan(web_server="apache")
        self.assertNotEqual(nginx.simulation_digest, apache.simulation_digest)
        self.assertIn("nginx", {item["name"] for item in nginx.image_packages})
        self.assertIn("apache2", {item["name"] for item in apache.image_packages})
        self.assertEqual(nginx.service_effects[0]["policy_rc_d"], "deny-service-start")

    def test_unavailable_or_wrong_version_fails_before_confirmation(self):
        for changes in ({"php8.3-fpm": None}, {"mariadb-server": "1:11.4"}):
            versions = {**VERSIONS, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.planner(versions=versions).plan()

    def test_unsigned_ppa_remote_script_and_source_build_are_forbidden(self):
        for source in (
            {"uri": "http://example", "signed": False},
            {"uri": "http://ppa.launchpad.net/x", "signed": True, "kind": "ppa"},
            {"uri": "https://example/install.sh", "signed": True, "kind": "remote_script"},
            {"uri": "file:///src", "signed": True, "kind": "source_build"},
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.planner(sources=(source,)).plan()


if __name__ == "__main__": unittest.main()
