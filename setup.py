#!/usr/bin/env python3
"""Setup script for Local Automation Agent v3.
Installs all dependencies and verifies the environment."""

import os
import platform
import subprocess
import sys


def run(cmd, check=False):
    print(f"  > {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0 and result.stderr.strip():
        print(result.stderr.strip())
    if check and result.returncode != 0:
        return False
    return True


def main():
    system = platform.system()
    print(f"OS: {system} {platform.release()}")
    print(f"Python: {sys.version}")
    print()

    pip_cmd = "pip3" if system != "Windows" else "pip"
    extra = "--break-system-packages" if system == "Linux" else ""

    # Install pip requirements
    print("[1/4] Installing pip packages...")
    run(f"{pip_cmd} install --upgrade {extra} -r requirements.txt")

    # Install Playwright browser
    print("\n[2/4] Installing Playwright Chromium...")
    run(f"{sys.executable} -m playwright install chromium")

    # Linux system packages
    if system == "Linux":
        print("\n[3/4] Installing system packages (requires sudo)...")
        run("sudo apt update -y")
        run("sudo apt install -y tesseract-ocr tesseract-ocr-eng ffmpeg "
            "python3-tk python3-dev scrot xvfb")
    else:
        print("\n[3/4] System packages — check that Tesseract is installed manually on Windows.")

    # Verify imports
    print("\n[4/4] Verifying imports...")
    libs = {
        "requests": "import requests",
        "Pillow": "from PIL import Image",
        "playwright": "import playwright",
        "pyautogui": "import pyautogui",
        "mss": "import mss",
        "pytesseract": "import pytesseract",
        "opencv": "import cv2",
        "numpy": "import numpy",
    }
    passed = 0
    failed = 0
    for name, stmt in libs.items():
        try:
            exec(stmt)
            print(f"  {name}: OK")
            passed += 1
        except ImportError:
            print(f"  {name}: MISSING")
            failed += 1

    print(f"\nResults: {passed}/{passed + failed} libraries installed")
    if failed:
        print("Some libraries failed to install. Check errors above.")
        return 1
    print("Setup complete. Edit rdp_agent_v3.py to set your API keys, then run it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
