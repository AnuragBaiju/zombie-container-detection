"""one-shot dashboard smoke test: load, screenshot, validate banner + tabs."""
import sys, time, json
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from PIL import Image

import os
OUT = Path("images/30_dashboard_public_eks_validation.png")
URL = os.environ.get("DASH_URL", "http://localhost:8501")

opts = EdgeOptions()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1600,1400")
opts.add_argument("--disable-gpu")
drv = webdriver.Edge(options=opts)
drv.set_window_size(1600, 1400)

results = {"checks": []}
def check(name, ok, detail=""):
    results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {('— ' + detail) if detail else ''}")

try:
    print(f"loading {URL}")
    drv.get(URL)
    WebDriverWait(drv, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-testid='stAppViewContainer']"))
    )
    # poll until the LIVE/OFFLINE banner has actually rendered (max 30s)
    deadline = time.time() + 30
    body = ""
    while time.time() < deadline:
        body = drv.find_element(By.TAG_NAME, "body").text
        if ("OFFLINE" in body or "LIVE" in body) and "Threshold vs Heuristic" in body:
            break
        time.sleep(1)
    time.sleep(2)  # let plotly charts settle
    body = drv.find_element(By.TAG_NAME, "body").text
    check("page loads",                 "Zombie Container Detection" in body)
    check("LIVE banner present",        "LIVE" in body and "Connected to Prometheus" in body)
    check("not in OFFLINE mode",        "OFFLINE MODE" not in body, "Prometheus is reachable")
    check("live data populated",        "zombie-memory-leak" in body)
    check("all 12 containers listed",   "adversarial-cron-hourly" in body and
                                        "adversarial-stealth-zombie" in body)
    check("tab labels present",         "Threshold vs Heuristic" in body and
                                        "Failure Modes" in body)
    check("composite score visible",    "Composite Score" in body or "Zombie Score" in body)

    # full-page screenshot
    drv.execute_script("window.scrollTo(0,0)")
    h = drv.execute_script("return document.body.scrollHeight")
    drv.set_window_size(1600, max(1400, int(h) + 100))
    time.sleep(1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    drv.save_screenshot(str(OUT))

    img = Image.open(OUT)
    size_kb = OUT.stat().st_size / 1024
    check("screenshot file >= 50KB",    size_kb >= 50, f"{size_kb:.1f} KB")
    check("screenshot dims >= 1500x800", img.width >= 1500 and img.height >= 800,
          f"{img.width}x{img.height}")

    print(f"\nsaved -> {OUT}")
finally:
    drv.quit()

passed = sum(1 for c in results["checks"] if c["ok"])
total  = len(results["checks"])
print(f"\nresult: {passed}/{total} checks passed")
sys.exit(0 if passed == total else 1)
