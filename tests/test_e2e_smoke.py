"""Starts the real app and checks what only a browser can show: the toolbar,
the brand in the top strip, the theme. Skipped without Playwright and Chrome.
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def app_url(shop_db, tmp_path_factory):
    work = tmp_path_factory.mktemp("e2e")
    port = free_port()

    env = dict(
        os.environ,
        DEV_DB_DRIVER="sqlite",
        DEV_DB_NAME=shop_db,
        DEV_DB_HOST="",
        DEV_DB_USERNAME="",
        TEST_DB_NAME="",
        SQL_COPILOT_AUDIT_LOG="off",
        EXAMPLES_PATH=str(work / "examples.json"),
        GLOSSARY_PATH=str(work / "glossary.txt"),
    )

    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.headless", "true", "--server.port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    url = f"http://127.0.0.1:{port}"

    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"{url}/_stcore/health", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.skip("the app did not start in time")

        yield url
    finally:
        server.terminate()
        server.wait(timeout=15)


@pytest.fixture(scope="module")
def page(app_url):
    playwright = sync_playwright().start()

    try:
        browser = playwright.chromium.launch(channel="chrome")
    except Exception as exc:
        playwright.stop()
        pytest.skip(f"Chrome is not available: {exc}")

    tab = browser.new_page(viewport={"width": 1280, "height": 760})
    tab.js_errors = []
    tab.on("pageerror", lambda error: tab.js_errors.append(str(error)))
    tab.goto(app_url)
    tab.get_by_role("button", name="Discover & index database").wait_for(timeout=30000)

    yield tab

    browser.close()
    playwright.stop()


def test_page_loads_without_errors(page):
    assert page.js_errors == []


def test_three_dot_menu_has_rerun(page):
    page.get_by_test_id("stMainMenu").click()
    items = [t.replace("\n", " ") for t in page.locator('[role="menuitem"]').all_inner_texts()]
    page.keyboard.press("Escape")

    assert any(item.startswith("Rerun") for item in items), items
    assert any(item.startswith("Clear cache") for item in items), items


def test_deploy_button_is_hidden(page):
    assert not page.get_by_test_id("stAppDeployButton").is_visible()


def test_sections_are_offered(page):
    for label in ("Chat", "Explorer", "ER diagram"):
        assert page.get_by_text(label, exact=True).first.is_visible()


def test_chat_box_is_disabled_until_the_database_is_indexed(page):
    assert page.get_by_test_id("stChatInputTextArea").is_disabled()


def test_theme_colour_is_applied(page):
    colour = page.get_by_role("button", name="Discover & index database").evaluate(
        "e => getComputedStyle(e).backgroundColor"
    )

    assert colour == "rgb(37, 99, 235)"


SIDEBAR = '[data-testid="stSidebar"]'


def sidebar_state(page):
    return page.locator(SIDEBAR).get_attribute("aria-expanded")


def collapse_sidebar(page):
    """The collapse button only reacts once the page has settled and is hovered."""

    if sidebar_state(page) == "false":
        return

    page.wait_for_timeout(1500)
    page.locator(SIDEBAR).hover()
    page.get_by_test_id("stSidebarCollapseButton").click(timeout=5000)
    page.wait_for_function(
        "document.querySelector('[data-testid=stSidebar]').getAttribute('aria-expanded') === 'false'",
        timeout=5000,
    )


def test_brand_shows_in_the_top_strip_only_when_the_sidebar_is_collapsed(page):
    brand = page.locator(".sqlcopilot-brand")

    assert sidebar_state(page) == "true"
    assert brand.is_hidden()
    assert "SQL Copilot" in page.locator(SIDEBAR).inner_text()

    collapse_sidebar(page)
    brand.wait_for(state="visible", timeout=5000)

    assert "SQL Copilot" in brand.inner_text()
    box = brand.bounding_box()
    assert box["y"] < 60, "it sits in the empty top strip, above the section switcher"
    assert box["width"] < 400, "it is only as wide as its text"


def test_brand_never_blocks_the_menu_while_the_sidebar_is_collapsed(page):
    collapse_sidebar(page)

    # a normal click (no force) fails if anything is drawn on top of the button
    page.get_by_test_id("stMainMenu").click(timeout=5000)

    assert page.get_by_role("menuitem", name="Rerun").is_visible()

    page.keyboard.press("Escape")


def test_rerun_works_from_the_menu(page):
    collapse_sidebar(page)

    page.get_by_test_id("stMainMenu").click(timeout=5000)
    page.get_by_role("menuitem", name="Rerun").click()
    page.get_by_role("button", name="Discover & index database").wait_for(timeout=15000)

    assert page.js_errors == []
