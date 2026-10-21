"""Verify read-only day filters while crawling, without submitting any jobs."""
import json
import tempfile
import threading
from pathlib import Path

from flask import Flask, render_template
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from werkzeug.serving import make_server


root = Path(__file__).resolve().parent
test_app = Flask(__name__, template_folder=str(root / "templates"),
                 static_folder=str(root / "static"))


@test_app.get("/")
def index():
    return render_template("index.html", display_columns=[])


server = make_server("127.0.0.1", 0, test_app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
options = Options()
options.add_argument("--headless=new")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1440,1000")
profile = tempfile.TemporaryDirectory(prefix="day-metric-ui-")
options.add_argument(f"--user-data-dir={profile.name}")
driver = None
try:
    driver = webdriver.Chrome(service=Service(str(root / ".browser_drivers/chrome/chromedriver.exe")),
                              options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
        window.testRequests = [];
        window.fetch = async (url, options = {}) => {
            if (options.method && options.method !== 'GET') {
                throw new Error('Unexpected write request: ' + url);
            }
            window.testRequests.push(String(url));
            await new Promise(resolve => setTimeout(resolve, 80));
            return {ok: true, json: async () => ({
                success: true, rows: [], summary: {risk_count: 2, attention_count: 3},
                total: 0, total_pages: 1, operations: [], batch: null,
                job: {id: 'test-running-job', status: 'running', kind: 'crawl',
                      phase: 'primary', processed: 1, total: 10}
            })};
        };
    """})
    driver.get(f"http://127.0.0.1:{server.server_port}/")
    wait = WebDriverWait(driver, 15)
    wait.until(lambda d: d.execute_script("return !!document.querySelector('#app')._vnode?.component?.proxy"))
    driver.execute_script("window.testVm = document.querySelector('#app')._vnode.component.proxy; window.testVm.selectMenu({key:'day-data'})")
    wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".day-metric-board .metric-card")) == 2)
    wait.until(lambda d: d.execute_script("return window.testRequests.some(u => u.startsWith('/api/day-data?')) && !window.testVm.dayLoading && !window.testVm.overviewLoading"))
    results = []
    for mode in ("crawl", "compensate"):
        driver.execute_script("""
            const vm = window.testVm;
            clearTimeout(vm.crawlJobTimer); clearTimeout(vm.scheduledTimer);
            vm.crawling = arguments[0] === 'crawl';
            vm.compensating = arguments[0] === 'compensate';
        """, mode)
        for index, metric in enumerate(("risk", "attention")):
            wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, ".day-metric-board .metric-card")[index].is_enabled())
            driver.find_elements(By.CSS_SELECTOR, ".day-metric-board .metric-card")[index].click()
            wait.until(lambda d: d.execute_script("return !window.testVm.dayLoading"))
            assert driver.execute_script("return window.testVm.dayMetric") == metric, driver.execute_script("return {metric:window.testVm.dayMetric, loading:window.testVm.dayLoading, requests:window.testRequests}")
            assert driver.execute_script("return window.testRequests.some(u => u.startsWith('/api/day-data?') && new URLSearchParams(u.split('?')[1]).get('metric') === arguments[0])", metric)
        clear = driver.find_element(By.CSS_SELECTOR, ".metric-clear")
        assert clear.is_enabled()
        clear.click()
        wait.until(lambda d: d.execute_script("return !window.testVm.dayLoading"))
        assert driver.execute_script("return window.testVm.dayMetric") == "all"
        refresh = driver.find_element(By.CSS_SELECTOR, '[title="刷新日数据列表"]')
        assert refresh.is_enabled()
        refresh.click()
        wait.until(lambda d: d.execute_script("return !window.testVm.dayLoading"))
        assert not driver.find_element(By.CSS_SELECTOR, '[title="拉取 Amazon 日数据"]').is_enabled()
        assert not driver.find_element(By.XPATH, "//button[span[contains(text(), '补偿中') or contains(text(), '补偿今天数据')]]").is_enabled()
        results.append({"mode": mode, "filters_clickable": True, "refresh_clickable": True,
                        "duplicate_job_buttons_disabled": True})
    print(json.dumps(results))
finally:
    if driver:
        driver.quit()
    server.shutdown()
    profile.cleanup()
