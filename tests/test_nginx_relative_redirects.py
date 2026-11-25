"""The nginx server block keeps its own redirects relative.

nginx listens on 80 inside the container while an instance is published on
localhost:<port>. With absolute redirects, a folder asked for without its slash
(/wp-admin) was answered with `Location: http://localhost/wp-admin/`: the port
was gone and the browser left the instance. Observed on a live instance and with
this file in nginx:1.27-alpine before the directive was added.
"""
import unittest
from pathlib import Path

NGINX_CONF = Path(__file__).resolve().parents[1] / "config" / "nginx-sandbox.conf"


def _directives(text):
    """Config lines with comments and surrounding space removed."""
    return [line.split("#", 1)[0].strip() for line in text.splitlines()]


class TestNginxRelativeRedirects(unittest.TestCase):
    def test_absolute_redirects_are_off_in_the_server_block(self):
        directives = _directives(NGINX_CONF.read_text(encoding="utf-8"))
        self.assertIn("server {", directives)
        self.assertIn(
            "absolute_redirect off;",
            directives[directives.index("server {"):],
            "config/nginx-sandbox.conf must keep `absolute_redirect off;` in its server "
            "block, or /wp-admin on localhost:<port> redirects to a URL without the port",
        )

    def test_a_commented_out_directive_does_not_count(self):
        self.assertNotIn("absolute_redirect off;", _directives("    # absolute_redirect off;\n"))


if __name__ == "__main__":
    unittest.main()
