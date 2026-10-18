"""Real-browser check of the dashboard (Playwright + Chromium). Skipped automatically if Playwright is missing.

Run only these with:  pytest -m browser
"""

from __future__ import annotations

import pytest

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    from itgc_uar.cli import main
    base = tmp_path_factory.mktemp("browser")
    assert main(["all", "--profile", "baseline", "--data", str(base / "d"), "--out", str(base / "o")]) == 0
    return (base / "o" / "ITGC_UAR_Dashboard.html").as_uri()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binary not installed
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


def test_dashboard_loads_without_errors_and_filters_work(browser, page_url):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(page_url)
    assert "SIMULATED DATA" in page.inner_text("header")
    assert page.locator("#reg tbody tr.row").count() == 82
    assert "82 of 82" in page.inner_text("#count")
    page.select_option("#fSev", "High")
    assert page.locator("#reg tbody tr.row").count() == 29
    page.select_option("#fControl", "T3")
    assert page.locator("#reg tbody tr.row").count() == 10
    page.click("#reset")
    page.fill("#fText", "no-such-text-xyz")
    assert page.locator("#reg tbody tr.row").count() == 0 and "No exceptions match" in page.inner_text("#reg tbody")
    page.click("#reset")
    page.locator("#reg tbody tr.row").first.click()
    assert page.locator("#detail.on").count() == 1 and "Evidence" in page.inner_text("#detail")
    assert page.locator("#kpis .kpi").count() == 6
    assert errors == []
    page.close()


def test_dashboard_sort_and_dark_mode_and_mobile(browser, page_url):
    page = browser.new_page(viewport={"width": 390, "height": 800}, color_scheme="dark")
    page.goto(page_url)
    first = page.locator("#reg tbody tr.row td").nth(4).inner_text()
    assert int(first) == max(int(x) for x in page.locator("#reg tbody tr.row td:nth-child(5)").all_inner_texts())
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(18, 18, 17)"
    page.close()
