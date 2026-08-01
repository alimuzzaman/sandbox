from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class TestIngressRepository(unittest.TestCase):
    def route(self):
        from sandbox.ingress.models import RouteRecord
        return RouteRecord.create(
            owner="/tmp/project::default", hostname="demo.test",
            backend={"address": "127.0.0.1", "port": 8123}, adapter_id="caddy",
            protocols={"http"}, desired={"route": "demo"},
        ).with_applied({"route": "demo"})

    def test_atomic_roundtrip_and_attribution(self):
        from sandbox.ingress.repository import IngressRepository
        with tempfile.TemporaryDirectory() as tmp:
            repository = IngressRepository(Path(tmp) / "ingress.json")
            route = self.route(); repository.put_route(route)
            self.assertEqual(repository.route(route.route_id).owner, route.owner)
            self.assertEqual((Path(tmp) / "ingress.json").stat().st_mode & 0o777, 0o600)

    def test_drift_retains_route_and_recovery(self):
        from sandbox.ingress.repository import IngressRepository
        with tempfile.TemporaryDirectory() as tmp:
            repository = IngressRepository(Path(tmp) / "ingress.json")
            route = self.route(); repository.put_route(route)
            self.assertEqual(repository.remove_route_if_unchanged(
                route.route_id, {"route": "foreign"}), "drifted")
            self.assertIsNotNone(repository.route(route.route_id))
            self.assertIn(route.route_id, repository.snapshot()["recovery"])

    def test_version_zero_migrates_without_losing_routes(self):
        from sandbox.ingress.repository import IngressRepository
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ingress.json"; route = self.route()
            path.write_text(json.dumps({"version": 0, "routes": {route.route_id: route.to_dict()}}))
            repository = IngressRepository(path)
            self.assertIsNotNone(repository.route(route.route_id))
            self.assertEqual(repository.snapshot()["version"], 1)
            self.assertEqual(json.loads(path.read_text())["version"], 1)

    def test_threaded_transactions_do_not_lose_routes(self):
        from sandbox.ingress.models import RouteRecord
        from sandbox.ingress.repository import IngressRepository
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as tmp:
            repository = IngressRepository(Path(tmp) / "ingress.json")
            def write(index):
                route = RouteRecord.create(
                    owner=f"/tmp/project-{index}::default",
                    hostname=f"demo-{index}.test",
                    backend={"address": "127.0.0.1", "port": 8000 + index},
                    adapter_id="caddy", protocols={"http"}, desired={"index": index},
                )
                repository.put_route(route)
            with ThreadPoolExecutor(max_workers=8) as pool:
                tuple(pool.map(write, range(20)))
            self.assertEqual(len(repository.snapshot()["routes"]), 20)


if __name__ == "__main__": unittest.main()
