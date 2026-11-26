"""The demo page in a real browser against a stub API (no database, no model): guided tour and loading states.

Needs ``playwright install chromium``; set RELAY_BROWSER_CHANNEL=chrome to use an installed Chrome instead.
"""
from __future__ import annotations

import functools
import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

UI = Path(__file__).parents[1] / "ui"
STATUS = {"engines": ["live", "rules"], "slack": "dry_run", "articles": 73, "retention_hours": 24,
          "customers": [{"email": "maya.chen@example.com", "name": "Maya Chen", "orders": ["FW-10482"]}],
          "suggestions": [{"label": "Billing dispute", "turns": ["I was charged twice"]}],
          "live_allowance": {"daily_usd": 0.05, "remaining_usd": 0.05, "visitor_per_day": 8, "visitor_remaining": 8,
                             "resets": "00:00 UTC"}}
DONE = {"type": "done", "text": "A billing problem needs a person. Your ticket is FWT-1001.", "outcome": "escalated",
        "state": "escalated", "ticket": "FWT-1001", "engine": "recorded", "failed_answers": 0,
        "handoff": {"delivery": "dry_run"}}


class Stub(SimpleHTTPRequestHandler):
    behaviour: dict = {}

    def log_message(self, *args):
        pass

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            time.sleep(self.behaviour.get("status_delay", 0))
            if self.behaviour.get("status_fails", 0) > 0:
                self.behaviour["status_fails"] -= 1
                return self._json(503, {"detail": "down"})
            return self._json(200, STATUS)
        if self.path.startswith("/api/sessions/"):
            return self._json(200, {"tickets": [], "handoffs": []})
        return super().do_GET()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/api/sessions":
            return self._json(201, {"session_id": "s1"})
        if self.behaviour.get("message_fails", 0) > 0:
            self.behaviour["message_fails"] -= 1
            return self._json(500, {"detail": "The server had a problem."})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.wfile.write(b'data: {"type": "status", "text": "Reading your message"}\n\n')
            self.wfile.flush()
            time.sleep(self.behaviour.get("message_delay", 0))
            for event in ({"type": "token", "text": DONE["text"]}, DONE):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Stub, directory=str(UI)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        channel = os.environ.get("RELAY_BROWSER_CHANNEL") or None
        try:
            launched = p.chromium.launch(channel=channel, args=["--no-sandbox"])
        except sync_api.Error as exc:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no browser available: {str(exc)[:100]}")
        yield launched
        launched.close()


@pytest.fixture
def open_page(browser, base_url):
    Stub.behaviour = {}
    contexts, errors = [], []

    def build(tour_seen=True, width=1280, height=800, init="", **behaviour):
        Stub.behaviour = dict(behaviour)
        context = browser.new_context(viewport={"width": width, "height": height})
        if tour_seen:
            context.add_init_script("localStorage.setItem('relayTourSeen', '1')")
        if init:
            context.add_init_script(init)
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base_url)
        return page

    yield build
    for context in contexts:
        context.close()
    assert errors == []


# -- guided tour ----------------------------------------------------------------------------------------------------
def test_first_visit_starts_the_tour_and_it_can_be_stepped_skipped_and_restarted(open_page):
    page = open_page(tour_seen=False)
    dialog = page.get_by_role("dialog", name="A support agent that knows when to stop")
    expect(dialog).to_be_visible()
    count = page.locator(".tour-count")
    expect(count).to_have_text("Step 1 of 9")
    expect(page.locator(".tour-back")).to_be_disabled()
    page.locator(".tour-next").click()
    page.locator(".tour-next").click()
    expect(count).to_have_text("Step 3 of 9")
    expect(page.locator("section.panel")).to_be_visible()  # the chat box is opened to be shown
    page.locator(".tour-back").click()
    expect(count).to_have_text("Step 2 of 9")
    expect(page.locator("section.panel")).to_be_hidden()
    page.get_by_role("button", name="Skip tour").click()
    expect(page.locator(".tour-root")).to_have_count(0)
    assert page.evaluate("localStorage.getItem('relayTourSeen')") == "1"

    page.reload()
    expect(page.get_by_role("button", name="Billing dispute")).to_be_enabled()
    expect(page.locator(".tour-root")).to_have_count(0)  # remembered
    page.get_by_role("button", name="Take the tour").click()
    expect(count).to_have_text("Step 1 of 9")


def test_tour_works_from_the_keyboard(open_page):
    page = open_page(tour_seen=False)
    expect(page.locator(".tour-next")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator(".tour-count")).to_have_text("Step 2 of 9")
    page.keyboard.press("ArrowLeft")
    expect(page.locator(".tour-count")).to_have_text("Step 1 of 9")
    for _ in range(5):
        page.keyboard.press("Tab")
        assert page.evaluate("!!document.activeElement.closest('.tour-card')")  # focus cannot leave the dialog
    page.keyboard.press("Escape")
    expect(page.locator(".tour-root")).to_have_count(0)
    expect(page.get_by_role("button", name="Take the tour")).to_be_focused()
    assert page.evaluate("[...document.body.children].every(n => !n.inert)")


def test_every_step_fits_a_phone_screen_and_finishes(open_page):
    page = open_page(tour_seen=False, width=360, height=700)
    for step in range(1, 10):
        expect(page.locator(".tour-count")).to_have_text(f"Step {step} of 9")
        page.wait_for_timeout(300)  # the highlight transition
        card, spot = page.locator(".tour-card").bounding_box(), page.locator(".tour-spot").bounding_box()
        assert card["x"] >= 0 and card["x"] + card["width"] <= 360 and card["y"] >= 0 and card["y"] + card["height"] <= 700
        assert spot["width"] > 20 and spot["height"] > 20, f"step {step} highlights nothing"
        page.locator(".tour-next").click()
    expect(page.locator(".tour-root")).to_have_count(0)


def test_blocked_storage_does_not_break_the_page(open_page):
    page = open_page(tour_seen=False, init="Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked') }})")
    expect(page.locator(".tour-count")).to_have_text("Step 1 of 9")
    page.get_by_role("button", name="Skip tour").click()
    expect(page.get_by_role("button", name="Billing dispute")).to_be_enabled()


def test_reduced_motion_stops_the_animations(open_page):
    page = open_page(tour_seen=False)
    page.emulate_media(reduced_motion="reduce")
    assert page.evaluate("getComputedStyle(document.querySelector('.tour-spot')).transitionDuration") == "0s"


# -- loading states -------------------------------------------------------------------------------------------------
def test_page_load_shows_a_spinner_then_a_slow_notice(open_page):
    page = open_page(status_delay=6.5)
    loading = page.locator("#page-loading")
    expect(loading).to_contain_text("Loading the demo")
    expect(loading.locator(".spin")).to_be_visible()
    expect(page.locator("#chips .skeleton")).to_have_count(3)
    expect(loading).to_contain_text("longer than usual", timeout=6000)
    expect(loading).to_be_hidden(timeout=5000)
    expect(page.locator("#chips .skeleton")).to_have_count(0)
    expect(page.locator("#engine")).to_be_enabled()
    expect(page.locator("#customer")).to_be_enabled()
    expect(page.locator("#page-loading.card")).to_have_count(1)  # the loading box keeps its card styling
    expect(page.locator("#allowance.muted")).to_have_count(1)


def test_failed_page_load_explains_and_retries(open_page):
    page = open_page(status_fails=1)
    expect(page.get_by_role("alert")).to_contain_text("could not reach its server")
    page.get_by_role("button", name="Try again").click()
    expect(page.get_by_role("button", name="Billing dispute")).to_be_enabled()
    expect(page.locator("#page-loading")).to_be_hidden()


def test_waiting_for_a_reply_is_visible_and_blocks_double_sends(open_page):
    page = open_page(message_delay=6.5)
    chip = page.get_by_role("button", name="Billing dispute")
    chip.click()
    send = page.locator("form button")
    expect(page.locator(".typing")).to_be_visible()
    expect(send).to_be_disabled()
    expect(send.locator(".spin")).to_be_visible()
    expect(chip).to_be_disabled()
    expect(chip).to_have_attribute("aria-busy", "true")
    expect(page.locator("#decision .spin")).to_be_visible()
    expect(page.locator(".slow")).to_contain_text("longer than usual", timeout=6000)
    expect(page.locator(".msg.bot").last).to_contain_text("FWT-1001", timeout=5000)
    expect(page.locator(".typing")).to_have_count(0)
    expect(send).to_be_enabled()
    expect(send).to_have_text("Send")
    expect(chip).to_be_enabled()
    expect(page.locator("#state-badge")).to_contain_text("With a human")


def test_a_failed_message_offers_a_retry(open_page):
    page = open_page(message_fails=1)
    page.get_by_role("button", name="Billing dispute").click()
    expect(page.locator(".msg.error")).to_contain_text("The server had a problem")
    expect(page.locator("#decision")).to_contain_text("did not go through")
    expect(page.locator("form button")).to_be_enabled()
    page.locator(".msg.error .retry").click()
    expect(page.locator(".msg.bot").last).to_contain_text("FWT-1001")
    expect(page.locator(".msg.me")).to_have_count(1)


def test_new_chat_cancels_a_reply_in_flight(open_page):
    page = open_page(message_delay=3)
    page.get_by_role("button", name="Billing dispute").click()
    expect(page.locator(".typing")).to_be_visible()
    page.get_by_role("button", name="New chat").click()
    expect(page.locator("form button")).to_be_enabled()
    page.wait_for_timeout(3500)
    expect(page.locator(".msg")).to_have_count(1)  # only the greeting: the old reply never lands
    expect(page.locator("#state-badge")).to_contain_text("bot is handling")
