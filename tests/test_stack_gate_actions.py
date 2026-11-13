"""Spec 088 runtime contracts. Definitions only in the implementation shard."""
import copy
import hashlib
import hmac
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

from sandbox.hosting import login_service as login
from sandbox.hosting.login_service import runtime
from sandbox.hosting.share import stack_page
from tests.test_share_gate_runtime import StackPageTests, GATE, HUB, WP_KEY


class _ObservedExchangeLock:
    """Expose a deterministic signal when the second exchange queues."""

    def __init__(self):
        self._lock = threading.Lock()
        self._count_lock = threading.Lock()
        self._attempts = 0
        self.second_waiting = threading.Event()

    def acquire(self, timeout=None):
        with self._count_lock:
            self._attempts += 1
            if self._attempts == 2:
                self.second_waiting.set()
        return self._lock.acquire(timeout=timeout)

    def release(self):
        self._lock.release()


class StackActionsTests(StackPageTests):
    def v2(self):
        cookie, link_id = self.stack_cookie()
        store = self.public.gate.sites["share"]
        old = store.stack(link_id, self.config)
        page = {**old, "schema": runtime.STACK_SCHEMA_V2, "notes": ["Shared development stack"],
                "inbox": None, "companions": [],
                "hosts": [{"hostname": HUB, "role": "hub", "probe": "internal", "commit": None,
                           "state": "serving", "access": "qa"}],
                "wordpress": {**old["wordpress"], "claim_origin": "https://" + GATE}}
        runtime._write_private(store.stacks / (link_id + ".json"), json.dumps(page))
        return cookie, link_id, page

    def post_action(self, cookie, action="qa", **changes):
        _, body = self.view(cookie)
        csrf = re.search(rb'name="stack-csrf" content="([0-9a-f]+)"', body).group(1).decode()
        headers = {"Cookie": cookie, "Origin": "https://" + GATE, "X-Sandbox-Stack-Action": "1",
                   "Content-Type": "application/json"}
        headers.update(changes.pop("headers", {}))
        return self.request("POST", runtime.STACK_ACTION_PATH,
                            body=json.dumps({"action": action, "csrf": csrf, **changes}), headers=headers)

    def test_capability_wire_and_safe_builder(self):
        reply = self.manage("capabilities", "share")
        self.assertEqual(reply, {
            "ok": True, "capabilities": ["stack-page-v2", "stack-signin-pool-v1", "wordpress-claim-v1", "companion-status-v1", "owner-panel-gate-v1"],
            "config_digest": hashlib.sha256(json.dumps(self.config, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "panel_access_digest": self.config.get("panel_access_digest"),
            "panel_target_digest": self.config.get("panel_target_digest"),
            "panel_agent_mint": True,
            "stack_schemas": [runtime.STACK_SCHEMA, runtime.STACK_SCHEMA_V2],
            "link_schemas": [runtime.SITE_LINK_SCHEMA]})
        self.assertIn("capabilities share", login.site_command("compose", "capabilities", "share"))
        with self.assertRaises(login.LoginServiceError):
            login.site_command("compose", "bogus", "share")

    def test_exact_validator_parity_fixtures(self):
        _, _, page = self.v2()
        child_token = "A" * 22
        child = {"slug": "child", "target": "remote/child/dev", "hostname": "child.dev.example.test",
                 "link_id": "l-" + hashlib.sha256(child_token.encode()).hexdigest()[:12],
                 "expires_at": int(self.now) + 3600, "token": child_token, "title": "child",
                 "hosts": copy.deepcopy(page["hosts"]), "notes": []}
        good_child = {**page, "companions": [child]}
        fixtures = [page, good_child, {**page, "unexpected": 1}, {**page, "title": "a" * 64},
                    {**page, "notes": ["x" * 513]}, {**page, "sign_in": [{"entry": "qa", "email": "bad"}]},
                    {**page, "companions": [{**child, "companions": []}]},
                    {**page, "companions": [{**child, "link_id": "l-" + "0" * 12}]},
                    {**page, "companions": [{**child, "page": page}]},
                    {**page, "wordpress": {**page["wordpress"], "claim_origin": "https://bad.test/path"}},
                    {**page, "hosts": page["hosts"] * 2}]
        for fixture in fixtures:
            with self.subTest(fixture=fixtures.index(fixture)):
                self.assertEqual(runtime.valid_stack_v2(fixture), stack_page.valid_stack_v2(fixture))
        self.assertTrue(runtime.valid_stack_v2(page))
        self.assertTrue(runtime.valid_stack_v2(good_child))
        foreign = {**page, "wordpress": {**page["wordpress"], "claim_origin": "https://foreign.test"}}
        self.assertIsNone(runtime.valid_stack(foreign, "share", page["link"], self.config))

    def test_authentication_body_and_csrf_refusals_do_not_mint(self):
        cookie, _, _ = self.v2()
        for change in ({"csrf": "0" * 64}, {"headers": {"Origin": "null"}},
                       {"headers": {"Origin": "https://foreign.test"}},
                       {"headers": {"X-Sandbox-Stack-Action": "0"}}, {"extra": True},
                       {"headers": {"Content-Type": "text/plain"}}):
            self.assertEqual(self.post_action(cookie, **change)[0].status, 404)
        self.assertFalse(self.public.gate.is_live("qa"))
        response, _ = self.request("POST", runtime.STACK_ACTION_PATH, body='{"action":"qa","csrf":"x"}',
                                  headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 404)

    def test_eight_pool_across_visitors_eviction_and_singleton_independence(self):
        first_cookie, _, _ = self.v2()
        second_cookie, _, _ = self.v2()
        gate = self.public.gate
        singleton = {"schema": runtime.RECORD_SCHEMA, "entry": "qa", "token_sha256": "f" * 64,
                     "issued_at": int(self.now), "expires_at": int(self.now) + 600, "email": "qa@dev.example.test"}
        runtime._write_private(self.state_dir / "qa.json", json.dumps(singleton))
        tokens = [self.action(first_cookie if i % 2 else second_cookie) for i in range(9)]
        self.assertEqual(gate.record("qa"), singleton)
        self.assertIsNone(gate.pooled_record("qa", hashlib.sha256(tokens[0].encode()).hexdigest()))
        self.assertEqual(len(gate._pending["qa"]), 8)
        for token in tokens[1:]:
            self.assertIsNotNone(gate.pooled_record("qa", hashlib.sha256(token.encode()).hexdigest()))
        self.now += 601
        self.assertIsNone(gate.pooled_record("qa", hashlib.sha256(tokens[-1].encode()).hexdigest()))

    def test_entry_shorter_ttl_bounds_per_click_token_consumption(self):
        self.config["entries"]["qa"]["ttl_seconds"] = 60
        cookie, _, _ = self.v2()
        token = self.action(cookie)
        digest = hashlib.sha256(token.encode()).hexdigest()
        record = self.public.gate.pooled_record("qa", digest)
        self.assertLessEqual(record["expires_at"], int(self.now) + 60)
        self.now += 61
        self.assertFalse(self.public.gate.claim_pending("qa", record))

    def test_mapped_route_renders_its_action_instead_of_anonymous_anchor(self):
        cookie, _, _ = self.v2()
        response, body = self.view(cookie)
        self.assertEqual(response.status, 200)
        self.assertIn(('data-action="qa">' + HUB).encode(), body)
        self.assertNotIn(('href="https://' + HUB + '/"').encode(), body)
        self.assertFalse(self.public.gate.is_live("qa"))

    def wp_request(self, cookie):
        response, body = self.post_action(cookie, "$wordpress")
        self.assertEqual(response.status, 200, body)
        url = json.loads(body)["url"]
        token = url.partition("#")[2]
        return url, {"kid": re.search(r"sandbox_share_login=([0-9a-f]+)", url).group(1),
                     "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "nonce": "b" * 32,
                     "at": int(self.now)}

    def callback(self, payload):
        message = "sandbox-share-wp-claim-request.v1|{kid}|{token_sha256}|{nonce}|{at}".format(**payload)
        data = {**payload, "mac": hmac.new(bytes.fromhex(WP_KEY), message.encode(), hashlib.sha256).hexdigest()}
        return self.request("POST", runtime.WP_CLAIM_PATH, body=json.dumps(data), headers={"Content-Type": "application/json"})

    def test_wp_receipt_decoded_key_and_single_use(self):
        cookie, _, page = self.v2()
        url, payload = self.wp_request(cookie)
        response, body = self.callback(payload)
        self.assertEqual(response.status, 200, body)
        receipt = json.loads(body)
        self.assertEqual(set(receipt), {"ok", "kid", "token_sha256", "nonce", "at", "expires_at", "mac"})
        message = "sandbox-share-wp-claim-response.v1|{kid}|{token_sha256}|{nonce}|{at}|{expires_at}".format(**receipt)
        self.assertEqual(receipt["mac"], hmac.new(bytes.fromhex(WP_KEY), message.encode(), hashlib.sha256).hexdigest())
        self.assertEqual(receipt["expires_at"], int(url.partition("#")[2].split(".")[0]))
        self.assertEqual(self.callback(payload)[0].status, 404)
        self.assertNotIn(page["wordpress"]["key"], " ".join(self.logs))
        self.assertNotIn(payload["token_sha256"], " ".join(self.logs))

    def test_wp_unknown_evicted_expired_revoked_and_stale_denied(self):
        cookie, link_id, _ = self.v2()
        _, first = self.wp_request(cookie)
        self.assertEqual(self.callback({**first, "token_sha256": "0" * 64})[0].status, 404)
        self.assertEqual(self.callback({**first, "at": first["at"] - 31})[0].status, 404)
        for _ in range(8):
            _, newest = self.wp_request(cookie)
        self.assertEqual(self.callback(first)[0].status, 404)
        self.manage("revoke", "share", link_id)
        self.assertEqual(self.callback(newest)[0].status, 404)

    def test_atomic_wp_claim_only_one_winner(self):
        cookie, _, _ = self.v2()
        _, payload = self.wp_request(cookie)
        message = "sandbox-share-wp-claim-request.v1|{kid}|{token_sha256}|{nonce}|{at}".format(**payload)
        payload["mac"] = hmac.new(bytes.fromhex(WP_KEY), message.encode(), hashlib.sha256).hexdigest()
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda _: self.public.gate.wordpress_claim("share", payload), range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_companion_probe_requires_child_token_and_does_not_consume(self):
        _, link_id, page = self.v2()
        store = self.public.gate.sites["share"]
        link = next(row for _, row in store._links() if row["id"] == link_id)
        # A known token is required, not a parent cookie or guessed link id.
        response, _ = self.request("POST", runtime.COMPANION_STATUS_PATH, body=json.dumps({"link_id": link_id}),
            headers={"Content-Type": "application/json", "X-Sandbox-Companion-Probe": "1"})
        self.assertEqual(response.status, 404)
        self.assertEqual(store.spent(link), 0)

    def test_script_opens_blank_before_fetch_and_has_no_storage(self):
        self.assertLess(runtime._STACK_ACTION_SCRIPT.index("window.open"), runtime._STACK_ACTION_SCRIPT.index("fetch("))
        for forbidden in ("localStorage", "sessionStorage", "innerHTML"):
            self.assertNotIn(forbidden, runtime._STACK_ACTION_SCRIPT)

    def test_child_proof_matches_descriptor_and_does_not_spend(self):
        token = "C" * 22
        digest = hashlib.sha256(token.encode()).hexdigest()
        link_id = "l-" + digest[:12]
        issued = int(self.now)
        link = {"schema": runtime.SITE_LINK_SCHEMA, "entry": "share", "id": link_id,
                "token_sha256": digest, "issued_at": issued, "expires_at": issued + 3600,
                "uses": 1, "scope": runtime.STACK_PAGE_PATH, "kind": "stack"}
        _, _, base = self.v2()
        page = {**base, "link": link_id, "wordpress": None}
        self.assertTrue(self.manage("mint", "share", stdin=json.dumps({"link": link, "stack": page}))["ok"])
        body = {"token": token, "link_id": link_id, "expires_at": link["expires_at"],
                "title": page["title"], "hosts": page["hosts"], "notes": page["notes"]}
        def probe(payload):
            return self.request("POST", runtime.COMPANION_STATUS_PATH, body=json.dumps(payload),
                headers={"Content-Type": "application/json", "X-Sandbox-Companion-Probe": "1"})[0].status
        self.assertEqual(probe(body), 200)
        self.assertEqual(probe(body), 200)
        self.assertEqual(probe({**body, "title": "wrong"}), 404)
        self.assertEqual(self.public.gate.sites["share"].spent(link), 0)
        self.manage("revoke", "share", link_id)
        self.assertEqual(probe(body), 404)

    def test_wp_wrong_mac_and_nonce_replay_do_not_accept_second_hash(self):
        cookie, _, _ = self.v2()
        _, first = self.wp_request(cookie)
        _, second = self.wp_request(cookie)
        bad = {**first, "mac": "0" * 64}
        response, _ = self.request("POST", runtime.WP_CLAIM_PATH, body=json.dumps(bad),
                                  headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 404)
        self.assertEqual(self.callback(first)[0].status, 200)
        self.assertEqual(self.callback(second)[0].status, 404)
        self.assertEqual(self.callback({**second, "nonce": "c" * 32})[0].status, 200)

    def test_revoke_during_action_mint_sends_no_bearer(self):
        cookie, link_id, _ = self.v2()
        original = self.public.gate.mint_sign_in
        def revoke_after_mint(*args):
            value = original(*args)
            self.manage("revoke", "share", link_id)
            return value
        with patch.object(self.public.gate, "mint_sign_in", side_effect=revoke_after_mint):
            response, body = self.post_action(cookie)
        self.assertEqual(response.status, 404)
        self.assertNotIn(b"sandbox_autologin", body)

    def _claimed_magic_sources(self):
        cookie, link_id, _ = self.v2()
        tokens = [self.action(cookie), self.action(cookie)]
        gate = self.public.gate
        records = [gate.pooled_record("qa", hashlib.sha256(token.encode()).hexdigest())
                   for token in tokens]
        self.assertTrue(all(record is not None for record in records))
        self.assertTrue(all(gate.claim_pending("qa", record) for record in records))
        return gate, link_id, records

    def test_concurrent_magic_exchanges_capture_distinct_messages_and_leave_pool_independent(self):
        gate, link_id, records = self._claimed_magic_sources()
        lock = _ObservedExchangeLock()
        gate._capture_exchange_lock = lock
        release_first = threading.Event()
        requests = []
        messages = {}
        settings = {**self.config["entries"]["qa"]["magic_link"],
                    "link_pattern": rf"https://{re.escape(HUB)}/verify\?token=[A-Za-z0-9_-]+"}

        request_entered = threading.Event()
        def request_mail(_settings, email):
            identifier = f"mail-{len(requests) + 1}"
            requests.append(identifier)
            messages[identifier] = {"To": [{"Address": email}],
                "Created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "Text": f"https://{HUB}/verify?token={identifier}"}
            request_entered.set()
            if identifier == "mail-1":
                self.assertTrue(release_first.wait(5))

        def capture_json(path):
            if path == "/api/v1/messages?limit=200":
                return {"messages": [{"ID": identifier} for identifier in messages]}
            return messages[path.rsplit("/", 1)[1]]

        source_is_current = lambda record: gate.enabled() and gate.current("qa", record)
        with patch.object(gate, "_request_mail", side_effect=request_mail), \
                patch.object(gate, "_capture_json", side_effect=capture_json):
            with ThreadPoolExecutor(max_workers=2) as workers:
                first = workers.submit(gate.sign_in, settings, records[0]["email"],
                                       source_is_current=lambda: source_is_current(records[0]))
                self.assertTrue(request_entered.wait(5))
                second = workers.submit(gate.sign_in, settings, records[1]["email"],
                                        source_is_current=lambda: source_is_current(records[1]))
                self.assertTrue(lock.second_waiting.wait(5))

                # Issuing and evicting pending tokens stays available during capture.
                pending_urls = [gate.mint_sign_in("qa", records[0]["email"], int(self.now) + 300,
                                                  ("share", link_id)) for _ in range(9)]
                pending_digests = [hashlib.sha256(url.partition("#sandbox_autologin=")[2].encode()).hexdigest()
                                   for url in pending_urls]
                self.assertIsNone(gate.pooled_record("qa", pending_digests[0]))
                self.assertIsNotNone(gate.pooled_record("qa", pending_digests[-1]))
                release_first.set()
                urls = [first.result(timeout=5), second.result(timeout=5)]

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(set(urls)), 2)
        self.assertEqual(set(urls), {messages[identifier]["Text"] for identifier in requests})

    def test_queued_magic_exchange_checks_revoked_source_before_request(self):
        gate, link_id, records = self._claimed_magic_sources()
        lock = _ObservedExchangeLock()
        gate._capture_exchange_lock = lock
        release_first = threading.Event()
        request_entered = threading.Event()
        requests = []
        settings = {**self.config["entries"]["qa"]["magic_link"], "poll_timeout_seconds": 0}

        def request_mail(_settings, _email):
            requests.append("sent")
            request_entered.set()
            if len(requests) == 1:
                self.assertTrue(release_first.wait(5))

        source_is_current = lambda record: gate.enabled() and gate.current("qa", record)
        with patch.object(gate, "_request_mail", side_effect=request_mail), \
                patch.object(gate, "_message_ids", return_value=[]):
            with ThreadPoolExecutor(max_workers=2) as workers:
                first = workers.submit(gate.sign_in, settings, records[0]["email"],
                                       source_is_current=lambda: source_is_current(records[0]))
                self.assertTrue(request_entered.wait(5))
                second = workers.submit(gate.sign_in, settings, records[1]["email"],
                                        source_is_current=lambda: source_is_current(records[1]))
                self.assertTrue(lock.second_waiting.wait(5))
                self.manage("revoke", "share", link_id)
                release_first.set()
                with self.assertRaises(runtime.SignInFailure):
                    second.result(timeout=5)
                with self.assertRaises(runtime.SignInFailure):
                    first.result(timeout=5)

        self.assertEqual(requests, ["sent"])

    def test_duplicate_json_fields_are_refused(self):
        cookie, _, _ = self.v2()
        response, _ = self.request("POST", runtime.STACK_ACTION_PATH,
            body='{"action":"qa","action":"qa","csrf":"' + "0" * 64 + '"}',
            headers={"Cookie": cookie, "Origin": "https://" + GATE,
                     "X-Sandbox-Stack-Action": "1", "Content-Type": "application/json"})
        self.assertEqual(response.status, 404)
