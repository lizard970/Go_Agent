"""Opt-in browser test against a running Streamlit server and real KataGo."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.skipif(
    not os.getenv("GO_REVIEW_E2E_URL"), reason="Set GO_REVIEW_E2E_URL for real browser/engine verification")]


def test_navigation_and_real_sgf_flow(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    url = os.environ["GO_REVIEW_E2E_URL"]
    assert urlparse(url).hostname in ("127.0.0.1", "localhost")
    artifact_dir = Path(os.getenv("KATAGO_TEST_ARTIFACT_DIR", str(tmp_path)))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        channel = os.getenv("GO_REVIEW_BROWSER_CHANNEL")
        browser = playwright.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(url)
            expect(page.get_by_role("heading", name="开始一次复盘")).to_be_visible()
            sidebar = page.get_by_test_id("stSidebar")
            for link, heading in (("错题本", "错题本"), ("成长看板", "成长看板"), ("首页", "开始一次复盘")):
                sidebar.get_by_text(link, exact=True).click()
                expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()

            sidebar.get_by_text("新建复盘 / SGF复盘", exact=True).click()
            expect(page.get_by_role("heading", name="新建复盘", exact=True)).to_be_visible()
            expect(page.get_by_text("上传 SGF 棋谱", exact=True)).to_be_visible()
            page.locator('input[type=file]').set_input_files({
                "name": "capture-pass.sgf", "mimeType": "application/x-go-sgf",
                "buffer": b'(;SZ[9]KM[6.5];B[ba];W[aa];B[ab];W[])'})
            selector = page.get_by_role("slider", name="局面手数")
            expect(selector).to_have_value("4")
            report = []
            current_move = 4
            for move in (2, 4):
                while current_move != move:
                    key = "ArrowRight" if current_move < move else "ArrowLeft"
                    current_move += 1 if current_move < move else -1
                    page.get_by_role("slider", name="局面手数").press(key)
                    expect(page.get_by_text(f"第 {current_move}/4 手", exact=False)).to_be_visible()
                visits_panel = page.get_by_text("visits：", exact=False)
                expect(visits_panel).to_have_count(0)
                page.get_by_role("button", name="开始分析", exact=False).click()
                expect(visits_panel).to_be_visible(timeout=130000)
                expect(page.get_by_test_id("stException")).to_have_count(0)
                summary = " · ".join(metric.inner_text() for metric in page.get_by_test_id("stMetric").all())
                pv = page.locator('[data-testid="stMarkdownContainer"]').filter(has_text="最佳变化").inner_text()
                visits = visits_panel.inner_text()
                assert re.search(r"visits：[1-9]\d*", visits)
                assert "你的胜率" in summary and "推荐着" in summary and "→" in pv
                expect(page.get_by_role("heading", name="LLM 解释")).to_be_visible()
                report.append({"move": move, "summary": summary, "visits": visits, "pv": pv})
                page.screenshot(path=str(artifact_dir / f"prototype-ui-move-{move}.png"), full_page=True)
            (artifact_dir / "prototype-ui-summary.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=True))
        finally:
            browser.close()
