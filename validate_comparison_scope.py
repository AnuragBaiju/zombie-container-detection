"""Validate the new Comparison Scope section is rendered on Tab 2.
Clicks Tab 2, waits, captures the body text + a screenshot.
"""
import time, sys
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from PIL import Image

OUT = Path("images/29_dashboard_comparison_scope.png")
URL = "http://localhost:8501"

opts = EdgeOptions()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1600,2400")
opts.add_argument("--disable-gpu")
drv = webdriver.Edge(options=opts)
drv.set_window_size(1600, 2400)

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")

try:
    drv.get(URL)
    WebDriverWait(drv, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-testid='stAppViewContainer']"))
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        body = drv.find_element(By.TAG_NAME, "body").text
        if "Threshold vs Heuristic" in body and ("LIVE" in body or "OFFLINE" in body):
            break
        time.sleep(1)

    # click Tab 2
    tabs = drv.find_elements(By.CSS_SELECTOR, "button[role='tab']")
    for t in tabs:
        if "Threshold" in (t.text or ""):
            t.click()
            break
    time.sleep(3)

    body = drv.find_element(By.TAG_NAME, "body").text
    check("Tab 2 selected",                  "Naive Threshold vs. Heuristic Detection" in body)
    check("Comparison Scope header",         "Comparison Scope and Limitations" in body)
    check("Q1 about Li et al.",              "anchor paper (Li et al. 2025)" in body)
    check("Q2 naive baseline rationale",     "naive static threshold" in body)
    check("Q3 ML baseline question",         "machine-learning baseline" in body)
    check("Q4 FPR question",                 "Is 50% FPR too high" in body)
    check("Lead-with-recall reframe",        "Lead with recall" in body)
    check("100% recall callout",             "100% of real zombies" in body)

    # expand all expanders so the screenshot shows them
    expanders = drv.find_elements(By.CSS_SELECTOR, "details summary, button[data-testid='stExpanderToggle']")
    for ex in expanders:
        try:
            drv.execute_script("arguments[0].scrollIntoView();", ex)
            ex.click()
            time.sleep(0.3)
        except Exception:
            pass
    time.sleep(2)

    h = drv.execute_script("return document.body.scrollHeight")
    drv.set_window_size(1600, max(2400, int(h) + 100))
    time.sleep(1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    drv.save_screenshot(str(OUT))

    img = Image.open(OUT)
    size_kb = OUT.stat().st_size / 1024
    check("screenshot saved",       size_kb >= 80, f"{size_kb:.1f} KB")
    check("screenshot dims valid",  img.width >= 1500 and img.height >= 1500,
          f"{img.width}x{img.height}")

    print(f"\nsaved -> {OUT}")
finally:
    drv.quit()

passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)
print(f"\nresult: {passed}/{total} checks passed")
sys.exit(0 if passed == total else 1)
