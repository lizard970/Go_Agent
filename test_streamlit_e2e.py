"""Opt-in rendered browser test against a running local Streamlit server.

Start start.bat, then set GO_REVIEW_E2E_URL=http://127.0.0.1:8501.
No engine mocking: the server must have real KataGo configured.
"""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(
    not os.getenv('GO_REVIEW_E2E_URL'), reason='Set GO_REVIEW_E2E_URL for real browser/engine verification')]


def test_upload_select_analyze_real_engine(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    url = os.environ['GO_REVIEW_E2E_URL']
    assert urlparse(url).hostname in ('127.0.0.1','localhost')
    artifact_dir = Path(os.getenv('KATAGO_TEST_ARTIFACT_DIR',str(tmp_path)))
    artifact_dir.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as playwright:
        channel = os.getenv('GO_REVIEW_BROWSER_CHANNEL')
        browser = playwright.chromium.launch(headless=True, **({'channel':channel} if channel else {}))
        try:
            page = browser.new_page(viewport={'width':1280,'height':1100})
            page.goto(url)
            expect(page.get_by_text('上传 SGF 棋谱',exact=True)).to_be_visible()
            expect(page.get_by_text('上传棋局截图',exact=True)).to_be_visible()
            page.locator('input[type=file]').nth(0).set_input_files({
                'name':'capture-pass.sgf','mimeType':'application/x-go-sgf',
                'buffer':b'(;SZ[9]KM[6.5];B[ba];W[aa];B[ab];W[])'})
            selector = page.get_by_role('spinbutton',name='分析手数')
            expect(selector).to_have_value('4')
            report = []
            for move in (2,4):
                selector.fill(str(move)); selector.press('Enter')
                visits_panel = page.get_by_text('搜索次数（visits）',exact=False)
                expect(visits_panel).to_have_count(0)
                page.get_by_role('button',name='分析此局面（KataGo）').click()
                expect(visits_panel).to_be_visible(timeout=130000)
                expect(page.get_by_test_id('stException')).to_have_count(0)
                summary = page.get_by_text('黑棋胜率：',exact=False).inner_text()
                visits = visits_panel.inner_text()
                pv = page.get_by_text('最佳变化（PV）：',exact=False).inner_text()
                assert re.search(r'visits.*[1-9]\d*',visits)
                assert '最佳着：' in summary and '→' in pv
                expect(page.get_by_test_id('stDataFrame')).to_be_visible()
                expect(page.get_by_text('上传棋局截图',exact=True)).to_be_visible()
                page.get_by_text('完整分析结果',exact=True).click()
                evidence = page.get_by_test_id('stJson').last.inner_text()
                assert re.search(r'"status"\s*:\s*"ok"',evidence)
                assert re.search(r'"move_number"\s*:\s*'+str(move),evidence)
                (artifact_dir/f'browser-move-{move}.txt').write_text(evidence,encoding='utf-8')
                page.get_by_text('完整分析结果',exact=True).click()
                page.screenshot(path=str(artifact_dir/f'browser-move-{move}.png'),full_page=True)
                report.append({'move':move,'summary':summary,'visits':visits,'pv':pv})
            # A different input must not show the previous position's evidence.
            page.locator('input[type=file]').nth(0).set_input_files({
                'name':'other.sgf','mimeType':'application/x-go-sgf','buffer':b'(;SZ[9];B[cc])'})
            expect(selector).to_have_value('1')
            expect(page.get_by_text('搜索次数（visits）',exact=False)).to_have_count(0)
            expect(page.get_by_test_id('stException')).to_have_count(0)
            (artifact_dir/'browser-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=True))
        finally:
            browser.close()
