#!/usr/bin/env python3
"""
Capture all thesis-report screenshots automatically and verify each one.

Uses Selenium + headless Microsoft Edge (preinstalled on Windows; Selenium
Manager auto-downloads the matching EdgeDriver). Avoids the Playwright +
greenlet compatibility issues on Python 3.14.

Captures:
  images/01_dashboard_live_detection_overview.png
  images/02_dashboard_live_detection_heatmap.png
  images/03_dashboard_threshold_vs_heuristic.png
  images/04_dashboard_energy_and_cost.png
  images/05_dashboard_experimental_design.png
  images/06_dashboard_failure_modes.png
  images/07_prometheus_targets.png
  images/artifacts/kubectl_get_pods.txt
  images/artifacts/kubectl_get_nodes.txt
  images/artifacts/detector_logs.txt
  images/artifacts/evaluation_results.csv

Verification (per screenshot):
  - file exists, size >= 30 KB
  - PNG signature valid
  - PIL can open it, dimensions >= 800x600

Pre-reqs (run before this script):
  python -m pip install selenium pillow
  kubectl port-forward -n monitoring svc/prometheus-server 9090:9090
  PROMETHEUS_URL=http://localhost:9090 streamlit run dashboard/app.py --server.headless=true
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    webdriver = None

try:
    from PIL import Image
except ImportError:
    Image = None


REPO_ROOT = Path(__file__).resolve().parent
IMAGES_DIR = REPO_ROOT / "images"
ARTIFACTS_DIR = IMAGES_DIR / "artifacts"

DASHBOARD_URL = "http://localhost:8501"
PROMETHEUS_URL = "http://localhost:9090"

VIEWPORT_W = 1600
VIEWPORT_H = 1100
MIN_FILE_BYTES = 30 * 1024
MIN_WIDTH = 800
MIN_HEIGHT = 600
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass
class TabSpec:
    filename: str
    label: str
    description: str


TABS = [
    TabSpec("01_dashboard_live_detection_overview.png", "Live Detection",
            "Tab 1 — score bar chart for all 12 pods (top of page)"),
    TabSpec("02_dashboard_live_detection_heatmap.png", "Live Detection",
            "Tab 1 — rule activation heatmap (scrolled)"),
    TabSpec("03_dashboard_threshold_vs_heuristic.png", "Threshold vs Heuristic",
            "Tab 2 — naive baseline comparison"),
    TabSpec("04_dashboard_energy_and_cost.png", "Energy & Cost Impact",
            "Tab 3 — Li et al. energy model applied per-zombie"),
    TabSpec("05_dashboard_experimental_design.png", "Experimental Design",
            "Tab 4 — why 7 + 5 = 12 containers"),
    TabSpec("06_dashboard_failure_modes.png", "Failure Modes (Adversarial)",
            "Tab 5 — failure-mode probes (professor-mandated)"),
]


# ── Pre-flight ────────────────────────────────────────────────────────────────

def http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def preflight() -> list[str]:
    issues: list[str] = []
    print("=" * 70)
    print("PRE-FLIGHT")
    print("=" * 70)
    if webdriver is None:
        issues.append("selenium not installed. Run: python -m pip install selenium pillow")
    if Image is None:
        issues.append("Pillow not installed. Run: python -m pip install pillow")
    dash = http_ok(DASHBOARD_URL + "/_stcore/health")
    prom = http_ok(PROMETHEUS_URL + "/-/ready")
    print(f"  Streamlit dashboard {DASHBOARD_URL:35s} {'UP' if dash else 'DOWN'}")
    print(f"  Prometheus          {PROMETHEUS_URL:35s} {'UP' if prom else 'DOWN'}")
    if not dash:
        issues.append(
            f"Streamlit not reachable. Start it:\n"
            f"    PROMETHEUS_URL=http://localhost:9090 streamlit run dashboard/app.py --server.headless=true"
        )
    if not prom:
        issues.append(
            f"Prometheus not reachable. Start the port-forward:\n"
            f"    kubectl port-forward -n monitoring svc/prometheus-server 9090:9090"
        )
    return issues


# ── Browser setup ─────────────────────────────────────────────────────────────

def make_driver() -> webdriver.Edge:
    opts = EdgeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument(f"--window-size={VIEWPORT_W},{VIEWPORT_H}")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--hide-scrollbars")
    opts.add_argument("--no-sandbox")
    return webdriver.Edge(options=opts)


def full_page_screenshot(driver, path: Path) -> None:
    """Resize the window to fit the full document so every section is captured."""
    height = driver.execute_script(
        "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);"
    )
    height = max(height, VIEWPORT_H)
    driver.set_window_size(VIEWPORT_W, height)
    time.sleep(0.4)
    driver.save_screenshot(str(path))


def click_streamlit_tab(driver, label: str) -> bool:
    """Click the tab whose visible text matches `label`."""
    try:
        # Streamlit renders tab labels inside <button role="tab"> ... </button>
        xpath = f"//button[@role='tab' and normalize-space(.)='{label}']"
        button = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
        time.sleep(0.2)
        button.click()
        time.sleep(2.5)  # let charts redraw
        return True
    except Exception as e:
        print(f"    [warn] tab '{label}' click failed: {e}")
        return False


def scroll_to_text(driver, text: str) -> bool:
    try:
        xpath = f"//*[contains(normalize-space(.), '{text}')]"
        el = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'start'});", el)
        time.sleep(1.0)
        return True
    except Exception:
        return False


# ── Capture flows ────────────────────────────────────────────────────────────

def capture_dashboard() -> list[Path]:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    captured: list[Path] = []

    print()
    print("Opening dashboard...")
    driver = make_driver()
    try:
        driver.get(DASHBOARD_URL)
        # Wait for the Streamlit app to render (banner present)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH,
                "//*[contains(text(), 'Zombie Container Detection')]"))
        )
        time.sleep(4.0)  # let initial Plotly charts draw

        seen_tab1 = False
        for spec in TABS:
            target = IMAGES_DIR / spec.filename
            print(f"  capturing {target.name}")
            print(f"            {spec.description}")

            if spec.label == "Live Detection" and seen_tab1:
                # second tab1 image: scroll to heatmap
                scroll_to_text(driver, "Rule Activation Heatmap")
            else:
                click_streamlit_tab(driver, spec.label)
                # scroll to top so the page header is visible
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.5)
                if spec.label == "Live Detection":
                    seen_tab1 = True

            full_page_screenshot(driver, target)
            captured.append(target)

    finally:
        driver.quit()
    return captured


def capture_prometheus_targets() -> Path:
    target = IMAGES_DIR / "07_prometheus_targets.png"
    print(f"  capturing {target.name}")
    driver = make_driver()
    try:
        driver.get(PROMETHEUS_URL + "/targets?search=")
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH,
                    "//*[contains(text(), 'zombie-detector')]"))
            )
        except Exception:
            print(f"    [warn] zombie-detector target not found in render")
        time.sleep(2.0)
        full_page_screenshot(driver, target)
    finally:
        driver.quit()
    return target


def capture_cli_artifacts() -> list[Path]:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []

    items = [
        (ARTIFACTS_DIR / "kubectl_get_pods.txt",
         ["kubectl", "get", "pods", "-A"]),
        (ARTIFACTS_DIR / "kubectl_get_nodes.txt",
         ["kubectl", "get", "nodes", "-o", "wide"]),
        (ARTIFACTS_DIR / "detector_logs.txt",
         ["kubectl", "logs", "-n", "zombie-detector",
          "deployment/zombie-detector", "--tail=200"]),
    ]
    for path, cmd in items:
        print(f"  capturing {path.name}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=30, check=False)
            text = (r.stdout or "") + (r.stderr or "")
            # strip cosmetic "File association not found" warnings on Windows
            text = "\n".join(
                l for l in text.splitlines()
                if "File association not found" not in l
            )
            path.write_text(text, encoding="utf-8")
            out.append(path)
        except Exception as e:
            print(f"    [warn] {' '.join(cmd)}: {e}")

    csv_src = REPO_ROOT / "evaluation_results.csv"
    if csv_src.exists():
        dst = ARTIFACTS_DIR / "evaluation_results.csv"
        shutil.copy2(csv_src, dst)
        print(f"  copied    {dst.name}")
        out.append(dst)
    return out


# ── Verification ─────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    path: Path
    ok: bool
    detail: str


def verify_screenshot(path: Path) -> Verdict:
    if not path.exists():
        return Verdict(path, False, "missing")
    size = path.stat().st_size
    if size < MIN_FILE_BYTES:
        return Verdict(path, False, f"too small ({size} bytes < {MIN_FILE_BYTES})")
    if path.read_bytes()[:8] != PNG_SIGNATURE:
        return Verdict(path, False, "not a valid PNG")
    if Image is not None:
        try:
            with Image.open(path) as im:
                w, h = im.size
                if w < MIN_WIDTH or h < MIN_HEIGHT:
                    return Verdict(path, False, f"too small ({w}x{h})")
                return Verdict(path, True, f"{size//1024} KB · {w}x{h}")
        except Exception as e:
            return Verdict(path, False, f"PIL open failed: {e}")
    return Verdict(path, True, f"{size//1024} KB")


def verify_artifact(path: Path) -> Verdict:
    if not path.exists():
        return Verdict(path, False, "missing")
    size = path.stat().st_size
    if size == 0:
        return Verdict(path, False, "empty")
    lines = sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
    return Verdict(path, True, f"{size} bytes · {lines} lines")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-cli", action="store_true")
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--skip-prometheus", action="store_true")
    args = parser.parse_args()

    blockers = preflight()
    if blockers and not (args.skip_dashboard and args.skip_prometheus):
        print()
        for b in blockers:
            print(f"  ! {b}")
        return 1

    captured: list[Path] = []
    artifacts: list[Path] = []

    if not args.skip_dashboard:
        print()
        print("=" * 70)
        print("CAPTURING DASHBOARD TABS")
        print("=" * 70)
        captured.extend(capture_dashboard())

    if not args.skip_prometheus:
        print()
        print("=" * 70)
        print("CAPTURING PROMETHEUS TARGETS")
        print("=" * 70)
        captured.append(capture_prometheus_targets())

    if not args.skip_cli:
        print()
        print("=" * 70)
        print("CAPTURING CLI ARTEFACTS")
        print("=" * 70)
        artifacts = capture_cli_artifacts()

    # Verification
    print()
    print("=" * 70)
    print("VERIFICATION")
    print("=" * 70)
    print()
    print("Screenshots:")
    all_ok = True
    for p in captured:
        v = verify_screenshot(p)
        flag = "PASS" if v.ok else "FAIL"
        print(f"  [{flag}]  {p.name:55s} {v.detail}")
        all_ok = all_ok and v.ok
    print()
    print("CLI artefacts:")
    for p in artifacts:
        v = verify_artifact(p)
        flag = "PASS" if v.ok else "FAIL"
        print(f"  [{flag}]  {p.name:32s} {v.detail}")
        all_ok = all_ok and v.ok

    print()
    print("=" * 70)
    print(f"OVERALL: {'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
    print("=" * 70)
    print()
    print(f"All output is under: {IMAGES_DIR}")
    print()
    print("Files:")
    for p in sorted(captured + artifacts):
        print(f"  {p.relative_to(REPO_ROOT)}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
