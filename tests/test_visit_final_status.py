import argparse
import contextlib
import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

VISIT_PATH = Path(__file__).resolve().parents[1] / "tools" / "visit" / "visit.py"


class FakePlaywrightError(Exception):
    pass


class FakePlaywrightTimeout(FakePlaywrightError):
    pass


def load_visit():
    package = types.ModuleType("playwright")
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = None
    api.Error = FakePlaywrightError
    api.TimeoutError = FakePlaywrightTimeout
    with patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": api}):
        spec = importlib.util.spec_from_file_location("visit_under_test", VISIT_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class FakePage:
    def __init__(self, navigate):
        self.main_frame = object()
        self.frames = [self.main_frame]
        self.handlers = {}
        self.navigate = navigate

    def on(self, event, handler):
        self.handlers[event] = handler

    def emit(self, status, *, url="http://site.test/", navigation=True, frame=None):
        response = SimpleNamespace(
            url=url, status=status, frame=frame or self.main_frame,
            request=SimpleNamespace(is_navigation_request=lambda: navigation,
                                    resource_type="document" if navigation else "image"),
        )
        self.handlers["response"](response)
        return response

    def goto(self, _url, wait_until=None, timeout=None):
        return self.navigate(self)

    def title(self):
        return "synthetic"

    def eval_on_selector_all(self, _selector, _script):
        return []

    def evaluate(self, _script):
        return None

    def wait_for_timeout(self, _milliseconds):
        return None


def fake_playwright(page):
    context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda **_kwargs: context, close=lambda: None)
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: browser))

    @contextlib.contextmanager
    def sync_playwright():
        yield pw

    return sync_playwright


def visit_args(**overrides):
    values = dict(
        url="http://site.test/", width=1280, height=800, user_agent="", login=False,
        auto_login=False, login_user="", login_password="", timeout=1, wait_until=None,
        check_iframes=False, iframe_settle=0, screenshot=None, full_page=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


class VisitFinalStatusTests(unittest.TestCase):
    def setUp(self):
        self.visit = load_visit()

    def run_visit(self, navigate=None, *, page=None, **overrides):
        if page is None:
            page = FakePage(navigate)
        out = io.StringIO()
        with patch.object(self.visit, "sync_playwright", fake_playwright(page)), \
                contextlib.redirect_stdout(out):
            code = self.visit.run(visit_args(**overrides))
        return code, json.loads(out.getvalue())

    @staticmethod
    def chain(*statuses):
        """Navigate through these statuses; goto returns the last response."""
        def navigate(page):
            response = None
            for status in statuses:
                response = page.emit(status)
            return response
        return navigate

    def test_direct_200(self):
        code, report = self.run_visit(self.chain(200))
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], 200)

    def test_302_to_200_reports_final_document(self):
        code, report = self.run_visit(self.chain(302, 200))
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], 200)

    def test_301_to_404_fails_without_console_errors(self):
        code, report = self.run_visit(self.chain(301, 404))
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], 404)
        self.assertEqual(report["console"], [])

    def test_multiple_redirects_report_final_document(self):
        code, report = self.run_visit(self.chain(301, 302, 307, 200))
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], 200)

    def test_subresource_failure_does_not_change_document_status(self):
        def navigate(page):
            page.emit(404, url="http://site.test/a.png", navigation=False)
            return page.emit(200)

        code, report = self.run_visit(navigate)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], 200)
        self.assertEqual(len(report["network_failures"]), 1)
        self.assertEqual(report["network_failures"][0]["status"], 404)

    def test_subframe_navigation_does_not_change_document_status(self):
        def navigate_with_resp(page):
            final = page.emit(200)
            page.emit(500, frame=object())
            return final

        code, report = self.run_visit(navigate_with_resp)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], 200)

        def navigate_none(page):
            page.emit(200)
            page.emit(500, frame=object())
            return None

        code2, report2 = self.run_visit(navigate_none)
        self.assertEqual(code2, 0)
        self.assertEqual(report2["status"], 200)

    def test_goto_without_response_uses_last_navigation_seen(self):
        def navigate(page):
            page.emit(302)
            page.emit(200)
            return None

        code, report = self.run_visit(navigate)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], 200)

    def test_timeout_keeps_last_navigation_status_and_fails(self):
        def navigate(page):
            page.emit(302)
            page.emit(200)
            raise FakePlaywrightTimeout()

        code, report = self.run_visit(navigate)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], 200)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("navigation timeout", report["errors"][0])

    def test_navigation_before_goto_is_not_counted(self):
        def fake_login(page, *args, **kwargs):
            page.emit(200)
            return None

        with patch.object(self.visit, "_do_login", side_effect=fake_login):
            code, report = self.run_visit(
                self.chain(404),
                login=True,
                login_password="synthetic",
            )
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], 404)

    def test_response_after_goto_is_not_counted(self):
        page = FakePage(self.chain(200))
        page.wait_for_timeout = lambda _ms: page.emit(500)
        code, report = self.run_visit(
            page=page,
            check_iframes=True,
            iframe_settle=1,
        )
        self.assertEqual(report["status"], 200)


if __name__ == "__main__":
    unittest.main()
