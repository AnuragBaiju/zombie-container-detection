"""capture browser-side errors + visible page errors from the dashboard."""
import time, json
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

opts = EdgeOptions()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1600,1400")
opts.add_argument("--disable-gpu")
opts.set_capability("ms:loggingPrefs", {"browser": "ALL"})

drv = webdriver.Edge(options=opts)
drv.get("http://localhost:8501")
WebDriverWait(drv, 30).until(
    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-testid='stAppViewContainer']"))
)
deadline = time.time() + 25
while time.time() < deadline:
    body = drv.find_element(By.TAG_NAME, "body").text
    if ("OFFLINE" in body or "LIVE" in body) and "Threshold vs Heuristic" in body:
        break
    time.sleep(1)
time.sleep(3)

print("--- streamlit-rendered errors / exceptions on page ---")
for sel in ["div[data-testid='stException']",
            "div[data-testid='stAlert']",
            "div.stException",
            "code"]:
    for el in drv.find_elements(By.CSS_SELECTOR, sel):
        txt = (el.text or "").strip()
        if txt and ("Error" in txt or "Traceback" in txt or "Exception" in txt):
            print(f"[{sel}] {txt[:500]}")

print("\n--- browser console log ---")
try:
    for entry in drv.get_log("browser"):
        lvl = entry.get("level", "")
        msg = entry.get("message", "")
        if lvl in ("SEVERE", "WARNING") or "error" in msg.lower():
            print(f"[{lvl}] {msg[:300]}")
except Exception as e:
    print(f"(could not read browser log: {e})")

print("\n--- visible body text (first 1200 chars) ---")
print(drv.find_element(By.TAG_NAME, "body").text[:1200])

drv.quit()
