"""End-to-end test of the MURCLOUD readiness criteria through the real web UI.

Run against a live server:
    MUR_E2E_URL=http://127.0.0.1:8000 MUR_E2E_SHOTS=./shots pytest e2e -q

The backend must run with MUR_AI_ALLOW_SCRIPTED=1 so the AI step can use the
scripted test model (every tool still executes for real).
"""

import os
import re
import time
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

BASE = os.environ.get("MUR_E2E_URL", "http://127.0.0.1:8000")
SHOTS = Path(os.environ.get("MUR_E2E_SHOTS", "e2e-shots"))
CHROMIUM = os.environ.get("MUR_CHROMIUM_PATH") or None


@pytest.fixture(scope="module")
def page():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM)
        # MUR_E2E_IGNORE_TLS=1 only for a local Caddy test CA (https://localhost).
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU",
                                  ignore_https_errors=os.environ.get("MUR_E2E_IGNORE_TLS") == "1")
        p = ctx.new_page()
        p.set_default_timeout(20_000)
        yield p
        browser.close()


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(SHOTS / f"{name}.png"))


USER = "musya" + uuid.uuid4().hex[:5]
PASSWORD = "murmur-secret-1"


def test_01_register_and_desktop(page: Page):
    page.goto(BASE)
    page.get_by_role("button", name="Регистрация").click()
    page.locator("input[name=username]").fill(USER)
    page.locator("input[name=display_name]").fill("Муся")
    page.locator("input[name=password]").fill(PASSWORD)
    shot(page, "01-register")
    page.get_by_role("button", name="Создать аккаунт").click()
    expect(page.get_by_test_id("studio")).to_be_visible()
    expect(page.get_by_label("Панель задач")).to_be_visible()


def test_02_studio_run_mysya(page: Page):
    page.get_by_test_id("project-select").select_option(label="Musya Hello")
    expect(page.get_by_test_id("file-main.mysya")).to_be_visible()
    page.get_by_test_id("file-main.mysya").click()
    editor = page.locator(".cm-content").first
    expect(editor).to_contain_text("МУРГОТОВО")
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.type('app "E2E"\nlet n = 6 * 7\nprint "e2e ok", n\n')
    page.keyboard.press("Control+S")
    page.get_by_test_id("btn-run").click()
    out = page.get_by_test_id("run-output")
    expect(out).to_contain_text("e2e ok 42", timeout=30_000)
    expect(out).to_contain_text("завершено: done")
    shot(page, "02-studio-run")


def test_03_terminal_and_service_engine(page: Page):
    page.get_by_test_id("tab-terminal").click()
    term = page.locator("[id^=term-]").first
    term.fill("let x = 21 * 2")
    term.press("Enter")
    term.fill("x")
    term.press("Enter")
    expect(page.get_by_test_id("terminal")).to_contain_text("42")
    term.fill("service status")
    term.press("Enter")
    expect(page.get_by_test_id("terminal")).to_contain_text('"running"')
    shot(page, "03-terminal")


def test_04_mur_ai_builds_and_opens_site(page: Page):
    panel = page.get_by_test_id("ai-panel")
    panel.get_by_label("Новый диалог").click()
    provider = panel.get_by_label("Провайдер")
    expect(provider).to_be_enabled()
    provider.select_option("scripted")
    panel.get_by_test_id("ai-mode").select_option("agent")
    panel.get_by_test_id("ai-input").fill("Создай красивый сайт для Муси, запусти и открой его")
    panel.get_by_test_id("ai-input").press("Enter")
    deadline = time.time() + 120
    approvals = 0
    while time.time() < deadline:
        if panel.get_by_test_id("ai-message").filter(has_text="МУРГОТОВО").count():
            break
        a = panel.get_by_test_id("approval")
        if a.count():
            shot(page, f"04-approval-{approvals}")
            btn = a.first.get_by_role("button", name="Всегда в этом диалоге")
            (btn if btn.count() else a.first.get_by_role("button", name="Разрешить")).click()
            approvals += 1
        time.sleep(0.5)
    expect(panel.get_by_test_id("ai-message").filter(has_text="МУРГОТОВО")).to_be_visible()
    assert approvals >= 1
    cards = panel.get_by_test_id("tool-card")
    assert cards.count() >= 6
    assert panel.locator("[data-testid=tool-card][data-status=failed]").count() == 0
    expect(panel.locator("img[alt=screenshot]").first).to_be_visible()
    expect(page.get_by_test_id("file-site")).to_be_visible()
    shot(page, "04-ai-done")


def test_05_preview_and_browser(page: Page):
    page.get_by_test_id("file-site").click()
    page.get_by_test_id("tab-browser").click()
    expect(page.get_by_test_id("browser-view").locator("img").first).to_be_visible(timeout=20_000)
    shot(page, "05-browser")


def test_06_drive_has_screenshots(page: Page):
    page.get_by_role("button", name="Меню приложений").click()
    page.get_by_test_id("launcher").get_by_role("button", name="MUR Drive", exact=True).click()
    drive = page.get_by_test_id("drive")
    drive.get_by_role("button", name="Screenshots").click()
    expect(drive).to_contain_text(re.compile(r"musya-site.*\.png"))
    shot(page, "06-drive")


def test_07_reload_keeps_everything(page: Page):
    page.reload()
    expect(page.get_by_test_id("studio")).to_be_visible()
    page.get_by_test_id("project-select").select_option(label="Musya Hello")
    page.get_by_test_id("file-main.mysya").click()
    expect(page.locator(".cm-content").first).to_contain_text("e2e ok")
    shot(page, "07-after-reload")


def test_08_other_apps_open(page: Page):
    for app, testid in [("MUR RTX Lab", "rtx"), ("MUR Gaming", "gaming"), ("System Monitor", "monitor"), ("MUR Soup", "soup")]:
        page.get_by_role("button", name="Меню приложений").click()
        page.get_by_test_id("launcher").get_by_role("button", name=app, exact=True).click()
        expect(page.get_by_test_id(testid)).to_be_visible()
    page.get_by_test_id("ing-fish").click()
    shot(page, "08-apps")


def test_09_mobile_layout(page: Page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    expect(page.get_by_label("Панель задач")).to_be_visible()
    expect(page.get_by_test_id("soup")).to_be_visible()
    page.wait_for_timeout(800)
    shot(page, "09-mobile")
