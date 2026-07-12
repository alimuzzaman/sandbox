import unittest
from sandbox.hermes.routing import recommended_route


class TestHermesRouting(unittest.TestCase):
    def test_recommended_boundaries(self):
        self.assertEqual(recommended_route("inventory").profile, "luna")
        self.assertEqual(recommended_route("implementation").profile, "terra")
        self.assertEqual(recommended_route("architecture").profile, "sol")
        self.assertEqual(recommended_route("implementation", failures=2).profile, "sol")


if __name__ == "__main__": unittest.main()
