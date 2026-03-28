#!/usr/bin/env python3
"""Interactive login flow for birdseed using real Chrome + CDP.

Flow:
- Launch a real Google Chrome with remote debugging enabled
- User logs in manually (supports Google SSO, MFA, etc.)
- Connect via CDP using Playwright to capture cookies
- Save storage state to data/storage_state.json

Supports: macOS, Linux, Windows
"""

from pathlib import Path
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Playwright is required for login. Install it:")
    print("  pip install playwright && python -m playwright install chromium")
    sys.exit(1)


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_STATE_PATH = DATA_DIR / "storage_state.json"
CHROME_PROFILE_DIR = DATA_DIR / "chrome-profile"
CDP_PORT = 9223


def find_chrome() -> Path:
    """Find Chrome/Chromium binary on the current platform."""
    system = platform.system()

    candidates = []
    if system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    elif system == "Linux":
        candidates = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/snap/bin/chromium"),
        ]
    elif system == "Windows":
        for base in [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]:
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")

    for c in candidates:
        if c.exists():
            return c

    raise SystemExit(
        f"Chrome/Chromium not found on {system}.\n"
        "Please install Google Chrome or set CHROME_BIN environment variable."
    )


def wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket() as sock:
            sock.settimeout(1.0)
            try:
                sock.connect((host, port))
                return True
            except OSError:
                time.sleep(0.5)
    return False


def wait_for_cdp_json(timeout: float = 20.0) -> bool:
    start = time.time()
    url = f"http://127.0.0.1:{CDP_PORT}/json/version"
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def launch_real_chrome() -> subprocess.Popen:
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        chrome_path = Path(chrome_bin)
        if not chrome_path.exists():
            raise SystemExit(f"CHROME_BIN points to non-existent path: {chrome_bin}")
    else:
        chrome_path = find_chrome()

    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(chrome_path),
        f"--user-data-dir={CHROME_PROFILE_DIR}",
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        "https://x.com/i/bookmarks",
    ]

    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if platform.system() != "Windows":
        kwargs["preexec_fn"] = os.setsid

    return subprocess.Popen(cmd, **kwargs)


def main() -> int:
    print("🐦 birdseed login")
    print(f"   Chrome profile: {CHROME_PROFILE_DIR}")
    print(f"   Storage state → {STORAGE_STATE_PATH}")
    print()

    proc = launch_real_chrome()
    try:
        if not wait_for_port("127.0.0.1", CDP_PORT) or not wait_for_cdp_json():
            raise SystemExit("Chrome remote debugging endpoint did not become ready.")

        print("Chrome is ready. Please log in to X manually.")
        print("(Google sign-in and MFA are supported.)")
        input("\n→ After your bookmarks page is fully visible, press Enter here…\n")

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            context.storage_state(path=str(STORAGE_STATE_PATH))
            browser.close()

        if STORAGE_STATE_PATH.exists():
            STORAGE_STATE_PATH.chmod(0o600)
            try:
                payload = json.loads(STORAGE_STATE_PATH.read_text(encoding="utf-8"))
                cookies = len(payload.get("cookies", []))
                print(f"\n✅ Login saved! ({cookies} cookies captured)")
                print(f"   You can now run: python3 sync.py")
            except Exception:
                print("\n✅ Login saved!")
            return 0

        print("❌ Failed to save storage state.", file=sys.stderr)
        return 1
    finally:
        try:
            if platform.system() == "Windows":
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
