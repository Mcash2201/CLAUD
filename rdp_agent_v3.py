"""
RDP Human-Like Agent v3 — Local Automation Bot

A Telegram-controlled local machine automation agent that:
  - Receives text instructions via Telegram bot
  - Takes screenshots of the local machine screen
  - Sends screenshots to Claude Vision AI for analysis
  - AI decides the next action (click, type, scroll, navigate, etc.)
  - Executes actions and sends result screenshots back to Telegram
  - Loops until task is complete
  - Supports: OCR text extraction, OpenCV element detection,
    scheduled tasks, task queue, persistent memory, activity logging

Setup:
  1. pip install -r requirements.txt
  2. python3 -m playwright install chromium
  3. Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY below
  4. python3 rdp_agent_v3.py

Requirements: Python 3.10+, see requirements.txt
"""

# ─────────────────────────────────────────────────────────────
# SECTION 2: Configuration
# ─────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY")

MAX_TASK_STEPS = 20
HEADLESS_BROWSER = False
TYPING_MIN = 0.04
TYPING_MAX = 0.13
MOUSE_MIN = 0.25
MOUSE_MAX = 0.75
POLL_INTERVAL = 1
LOG_FILE = "agent.log"
ACTIVITY_LOG_FILE = "activity_log.json"
MEMORY_FILE = "agent_memory.json"
TEMPLATES_DIR = "templates"

# ─────────────────────────────────────────────────────────────
# SECTION 3: Imports
# ─────────────────────────────────────────────────────────────

import asyncio
import base64
import collections
import datetime
import io
import json
import logging
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
from PIL import Image

HAS_MSS = False
try:
    import mss
    HAS_MSS = True
except ImportError:
    pass

HAS_PYAUTOGUI = False
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    HAS_PYAUTOGUI = True
except ImportError:
    pass

HAS_PLAYWRIGHT = False
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    pass

HAS_CV2 = False
try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    pass

HAS_PYTESSERACT = False
try:
    import pytesseract
    HAS_PYTESSERACT = True
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────
# SECTION 3b: Load environment variables from .env file
# ─────────────────────────────────────────────────────────────

def _load_env_file():
    env_file = ".env"
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()

_load_env_file()

# ─────────────────────────────────────────────────────────────
# SECTION 4: Logging setup
# ─────────────────────────────────────────────────────────────

logger = logging.getLogger("agent")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")

_sh = logging.StreamHandler(sys.stdout)
_sh.setLevel(logging.INFO)
_sh.setFormatter(_fmt)
logger.addHandler(_sh)

_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

# ─────────────────────────────────────────────────────────────
# SECTION 5: Startup validation
# ─────────────────────────────────────────────────────────────

def validate_config():
    errors = []
    if TELEGRAM_BOT_TOKEN.startswith("YOUR_"):
        errors.append("TELEGRAM_BOT_TOKEN is not set")
    if TELEGRAM_CHAT_ID.startswith("YOUR_"):
        errors.append("TELEGRAM_CHAT_ID is not set")
    if ANTHROPIC_API_KEY.startswith("YOUR_"):
        errors.append("ANTHROPIC_API_KEY is not set")
    if not HAS_PLAYWRIGHT:
        errors.append("Playwright is not installed (pip install playwright)")
    if not HAS_PYAUTOGUI:
        logger.warning("PyAutoGUI not available — desktop click/type actions will be mocked")
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    if errors:
        print("Configuration errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    logger.info("Config validated OK")

# ─────────────────────────────────────────────────────────────
# SECTION 6: Agent State
# ─────────────────────────────────────────────────────────────

class AgentState:
    def __init__(self):
        self.task_queue = collections.deque()
        self.current_task = None
        self.schedules = []
        self.schedule_thread = None
        self.schedule_stop = threading.Event()

state = AgentState()

# ─────────────────────────────────────────────────────────────
# SECTION 7: Activity log
# ─────────────────────────────────────────────────────────────

def log_activity(event_type, detail):
    entry = {
        "time": datetime.datetime.now().isoformat(),
        "type": event_type,
        "detail": detail,
    }
    try:
        entries = []
        if os.path.exists(ACTIVITY_LOG_FILE):
            with open(ACTIVITY_LOG_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
        entries.append(entry)
        entries = entries[-1000:]
        with open(ACTIVITY_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception as exc:
        logger.debug("log_activity error: %s", exc)

# ─────────────────────────────────────────────────────────────
# SECTION 8: Memory system
# ─────────────────────────────────────────────────────────────

def _memory_read():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def _memory_write(data):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def memory_save(key, value):
    data = _memory_read()
    data[key] = {"value": value, "ts": datetime.datetime.now().isoformat()}
    _memory_write(data)
    logger.info("Memory saved: %s", key)

def memory_get(key, default=None):
    data = _memory_read()
    entry = data.get(key)
    if entry is None:
        return default
    return entry["value"]

def memory_load_all():
    return _memory_read()

def memory_delete(key):
    data = _memory_read()
    if key in data:
        del data[key]
        _memory_write(data)
        logger.info("Memory deleted: %s", key)

# ─────────────────────────────────────────────────────────────
# SECTION 9: Task queue
# ─────────────────────────────────────────────────────────────

def queue_add(instruction):
    state.task_queue.append(instruction)
    logger.info("Queue add: %s (size=%d)", instruction[:40], len(state.task_queue))

def queue_next():
    if state.task_queue:
        return state.task_queue.popleft()
    return None

def queue_clear():
    state.task_queue.clear()
    logger.info("Queue cleared")

def queue_status():
    if not state.task_queue:
        return "Task queue is empty."
    lines = ["Task queue:"]
    for i, t in enumerate(state.task_queue, 1):
        lines.append(f"  {i}. {t}")
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────
# SECTION 10: Telegram Bot
# ─────────────────────────────────────────────────────────────

class TelegramBot:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def _post(self, endpoint, **kwargs):
        try:
            resp = requests.post(f"{self.base}/{endpoint}", timeout=30, **kwargs)
            return resp.json()
        except requests.RequestException as exc:
            logger.error("Telegram API error (%s): %s", endpoint, exc)
            return {"ok": False}

    def get_updates(self):
        data = {"offset": self.offset, "timeout": 10}
        result = self._post("getUpdates", json=data)
        updates = result.get("result", [])
        for u in updates:
            self.offset = u["update_id"] + 1
        return updates

    def send_message(self, chat_id, text):
        text = text[:4096]
        return self._post("sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })

    def send_photo(self, chat_id, image_bytes, caption=""):
        caption = caption[:1024]
        files = {"photo": ("screenshot.png", io.BytesIO(image_bytes), "image/png")}
        data = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
        return self._post("sendPhoto", files=files, data=data)

    def send_typing(self, chat_id):
        return self._post("sendChatAction", json={"chat_id": chat_id, "action": "typing"})

    def download_file(self, file_id):
        result = self._post("getFile", json={"file_id": file_id})
        if not result.get("ok"):
            return None
        file_path = result["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        try:
            resp = requests.get(url, timeout=30)
            return resp.content
        except requests.RequestException as exc:
            logger.error("File download error: %s", exc)
            return None

# ─────────────────────────────────────────────────────────────
# SECTION 11: Screenshot engine
# ─────────────────────────────────────────────────────────────

def take_screenshot():
    if HAS_MSS:
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    if HAS_PYAUTOGUI:
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    img = Image.new("RGB", (1920, 1080), (30, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

def take_screenshot_pil():
    if HAS_MSS:
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    if HAS_PYAUTOGUI:
        return pyautogui.screenshot()
    return Image.new("RGB", (1920, 1080), (30, 30, 30))

def screenshot_to_base64():
    return base64.b64encode(take_screenshot()).decode("utf-8")

# ─────────────────────────────────────────────────────────────
# SECTION 12: OCR text extraction
# ─────────────────────────────────────────────────────────────

def ocr_full_screen():
    if not HAS_PYTESSERACT:
        return "[OCR not available — pytesseract not installed]"
    img = take_screenshot_pil()
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)
    img = img.convert("L")
    text = pytesseract.image_to_string(img, config="--psm 6")
    return text.strip()

def ocr_region(x, y, w, h):
    if not HAS_PYTESSERACT:
        return "[OCR not available]"
    img = take_screenshot_pil()
    cropped = img.crop((x, y, x + w, y + h))
    cw, ch = cropped.size
    cropped = cropped.resize((cw * 2, ch * 2), Image.LANCZOS)
    cropped = cropped.convert("L")
    text = pytesseract.image_to_string(cropped, config="--psm 6")
    return text.strip()

# ─────────────────────────────────────────────────────────────
# SECTION 13: OpenCV element detection
# ─────────────────────────────────────────────────────────────

def find_element(template_name, threshold=0.80):
    if not HAS_CV2:
        logger.warning("OpenCV not available")
        return None
    tpl_path = os.path.join(TEMPLATES_DIR, f"{template_name}.png")
    if not os.path.exists(tpl_path):
        logger.warning("Template not found: %s", tpl_path)
        return None
    template = cv2.imread(tpl_path, cv2.IMREAD_COLOR)
    if template is None:
        return None
    screen_img = take_screenshot_pil()
    screen_np = np.array(screen_img)
    screen_bgr = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
    result = cv2.matchTemplate(screen_bgr, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    logger.info("Template '%s' confidence: %.3f", template_name, max_val)
    if max_val >= threshold:
        th, tw = template.shape[:2]
        cx = max_loc[0] + tw // 2
        cy = max_loc[1] + th // 2
        return (cx, cy)
    return None

def save_template(name, x, y, w, h):
    img = take_screenshot_pil()
    cropped = img.crop((x, y, x + w, y + h))
    path = os.path.join(TEMPLATES_DIR, f"{name}.png")
    cropped.save(path)
    logger.info("Template saved: %s", path)
    return path

def click_element(template_name):
    coords = find_element(template_name)
    if coords:
        human_click(coords[0], coords[1])
        return True
    return False

# ─────────────────────────────────────────────────────────────
# SECTION 14: Voice command transcription
# ─────────────────────────────────────────────────────────────

def transcribe_voice(audio_bytes):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — voice transcription unavailable")
        return None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(audio_bytes)
        with open(tmp_path, "rb") as f:
            resp = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("voice.ogg", f, "audio/ogg")},
                data={"model": "whisper-1"},
                timeout=30,
            )
        if resp.status_code == 200:
            return resp.json().get("text", "")
        logger.error("Whisper API error %d: %s", resp.status_code, resp.text[:200])
        return None
    except Exception as exc:
        logger.error("Voice transcription error: %s", exc)
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

# ─────────────────────────────────────────────────────────────
# SECTION 15: Scheduled tasks
# ─────────────────────────────────────────────────────────────

def parse_schedule_command(text):
    text = text.strip()
    m = re.match(r"every\s+(\d+)h\s+(.+)", text, re.IGNORECASE)
    if m:
        return {"type": "interval_hours", "interval": int(m.group(1)), "task": m.group(2).strip()}
    m = re.match(r"every\s+(\d+)m\s+(.+)", text, re.IGNORECASE)
    if m:
        return {"type": "interval_mins", "interval": int(m.group(1)), "task": m.group(2).strip()}
    m = re.match(r"daily\s+(\d{1,2}:\d{2})\s+(.+)", text, re.IGNORECASE)
    if m:
        return {"type": "daily", "time": m.group(1), "task": m.group(2).strip()}
    m = re.match(r"(\d{1,2}:\d{2})\s+(.+)", text)
    if m:
        return {"type": "daily", "time": m.group(1), "task": m.group(2).strip()}
    return None

def start_scheduler(bot, chat_id):
    def _scheduler_loop():
        while not state.schedule_stop.is_set():
            now = datetime.datetime.now()
            for sched in state.schedules:
                if sched.get("_cancelled"):
                    continue
                if sched["type"] == "daily":
                    hm = now.strftime("%H:%M")
                    if hm == sched["time"] and sched.get("_last_run") != hm:
                        sched["_last_run"] = hm
                        bot.send_message(chat_id, f"Scheduled task: {sched['task']}")
                        log_activity("schedule_trigger", sched["task"])
                elif sched["type"] in ("interval_hours", "interval_mins"):
                    last_ts = sched.get("_last_ts", 0)
                    if sched["type"] == "interval_hours":
                        interval_sec = sched["interval"] * 3600
                    else:
                        interval_sec = sched["interval"] * 60
                    if time.time() - last_ts >= interval_sec:
                        sched["_last_ts"] = time.time()
                        bot.send_message(chat_id, f"Scheduled task: {sched['task']}")
                        log_activity("schedule_trigger", sched["task"])
            state.schedule_stop.wait(30)

    state.schedule_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    state.schedule_thread.start()
    logger.info("Scheduler started")

def list_schedules():
    active = [s for s in state.schedules if not s.get("_cancelled")]
    if not active:
        return "No active schedules."
    lines = ["Active schedules:"]
    for i, s in enumerate(active, 1):
        if s["type"] == "daily":
            lines.append(f"  {i}. Daily at {s['time']}: {s['task']}")
        elif s["type"] == "interval_hours":
            lines.append(f"  {i}. Every {s['interval']}h: {s['task']}")
        elif s["type"] == "interval_mins":
            lines.append(f"  {i}. Every {s['interval']}m: {s['task']}")
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────
# SECTION 16: Human-like mouse and keyboard (PyAutoGUI)
# ─────────────────────────────────────────────────────────────

def list_machines():
    return "Machines:\n  local: Local Machine <- ACTIVE"

def switch_machine(bot, chat_id, name):
    if name != "local":
        bot.send_message(chat_id, f"Unknown machine: {name}. Only 'local' is available.")
        return
    bot.send_message(chat_id, "Already on local machine.")
    log_activity("switch_machine", "local")

# ─────────────────────────────────────────────────────────────
# SECTION 16: Mouse and keyboard (PyAutoGUI)
# ─────────────────────────────────────────────────────────────

def human_click(x, y, button="left"):
    if not HAS_PYAUTOGUI:
        logger.info("[MOCK] click (%d, %d) %s", x, y, button)
        return
    pyautogui.moveTo(x + random.randint(-3, 3), y + random.randint(-3, 3),
                     duration=random.uniform(MOUSE_MIN, MOUSE_MAX))
    time.sleep(random.uniform(0.06, 0.18))
    pyautogui.click(button=button)
    log_activity("click", f"({x},{y}) {button}")

def human_double_click(x, y):
    if not HAS_PYAUTOGUI:
        logger.info("[MOCK] double_click (%d, %d)", x, y)
        return
    pyautogui.moveTo(x, y, duration=random.uniform(MOUSE_MIN, MOUSE_MAX))
    time.sleep(random.uniform(0.06, 0.15))
    pyautogui.doubleClick()
    log_activity("double_click", f"({x},{y})")

def human_right_click(x, y):
    human_click(x, y, button="right")

def human_type(text):
    if not HAS_PYAUTOGUI:
        logger.info("[MOCK] type: %s", text[:50])
        return
    for ch in text:
        pyautogui.typewrite(ch, interval=random.uniform(TYPING_MIN, TYPING_MAX))
    log_activity("type", text[:80])

def human_scroll(direction, amount=3):
    if not HAS_PYAUTOGUI:
        logger.info("[MOCK] scroll %s %d", direction, amount)
        return
    clicks = amount if direction == "up" else -amount
    pyautogui.scroll(clicks)
    log_activity("scroll", f"{direction} {amount}")

def press_key(key):
    if not HAS_PYAUTOGUI:
        logger.info("[MOCK] key: %s", key)
        return
    if "+" in key:
        parts = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*parts)
    else:
        pyautogui.press(key)
    log_activity("key", key)

def human_drag(x1, y1, x2, y2):
    if not HAS_PYAUTOGUI:
        logger.info("[MOCK] drag (%d,%d)->(%d,%d)", x1, y1, x2, y2)
        return
    pyautogui.moveTo(x1, y1, duration=random.uniform(MOUSE_MIN, MOUSE_MAX))
    time.sleep(random.uniform(0.08, 0.2))
    pyautogui.mouseDown()
    time.sleep(random.uniform(0.05, 0.15))
    pyautogui.moveTo(x2, y2, duration=random.uniform(MOUSE_MIN, MOUSE_MAX))
    time.sleep(random.uniform(0.05, 0.15))
    pyautogui.mouseUp()
    log_activity("drag", f"({x1},{y1})->({x2},{y2})")

# ─────────────────────────────────────────────────────────────
# SECTION 17: Playwright browser control
# ─────────────────────────────────────────────────────────────

_pw = None
_browser = None
_page = None

async def get_page():
    global _pw, _browser, _page
    if _page and not _page.is_closed():
        return _page
    if _pw is None:
        _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
        headless=HEADLESS_BROWSER,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--start-maximized"],
    )
    context = await _browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    )
    _page = await context.new_page()
    return _page

def _normalize_url(url):
    if not re.match(r"https?://", url):
        url = "https://" + url
    return url

async def browser_navigate(url):
    url = _normalize_url(url)
    page = await get_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        logger.warning("Navigation timeout/error: %s", exc)
    await asyncio.sleep(random.uniform(0.8, 1.5))

async def browser_click(x, y):
    page = await get_page()
    await page.mouse.move(x + random.randint(-2, 2), y + random.randint(-2, 2))
    await asyncio.sleep(random.uniform(0.05, 0.15))
    await page.mouse.click(x, y)

async def browser_type(text):
    page = await get_page()
    for ch in text:
        await page.keyboard.type(ch, delay=random.uniform(40, 130))

async def browser_scroll(direction, amount=300):
    page = await get_page()
    dy = -amount if direction == "up" else amount
    await page.mouse.wheel(0, dy)

async def close_browser():
    global _pw, _browser, _page
    if _browser:
        await _browser.close()
    if _pw:
        await _pw.stop()
    _pw = _browser = _page = None
    logger.info("Browser closed")

# ─────────────────────────────────────────────────────────────
# SECTION 18: Claude Vision AI brain
# ─────────────────────────────────────────────────────────────

def ai_analyze(screenshot_b64, instruction, history):
    history_text = ""
    if history:
        for h in history[-5:]:
            history_text += f"- Step {h['step']}: {h['action']} → {h['status']}\n"

    mem_text = ""
    mem = memory_load_all()
    if mem:
        items = list(mem.items())[-8:]
        for k, v in items:
            mem_text += f"- {k}: {v['value']}\n"

    prompt = f"""You are an automation agent controlling a computer screen.

TASK: {instruction}

RECENT HISTORY:
{history_text if history_text else '(none)'}

MEMORY:
{mem_text if mem_text else '(none)'}

Analyze the screenshot and decide the single best next action.
Respond ONLY with valid JSON (no markdown fences, no extra text).

JSON schema:
{{
  "action": "click|double_click|right_click|type|scroll|key|drag|navigate|browser_navigate|browser_click|browser_type|browser_scroll|ocr_read|wait|done",
  "x": 0,
  "y": 0,
  "x2": 0,
  "y2": 0,
  "text": "",
  "url": "",
  "key": "",
  "direction": "up|down",
  "amount": 3,
  "memory_save": {{"key": "", "value": ""}},
  "reasoning": "what you see and why you chose this action",
  "status": "short user-facing status"
}}
"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": screenshot_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        raw = body["content"][0]["text"]
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        decision = json.loads(raw)
        ms = decision.get("memory_save", {})
        if ms and ms.get("key"):
            memory_save(ms["key"], ms["value"])
        return decision
    except json.JSONDecodeError as exc:
        logger.error("AI response JSON parse error: %s", exc)
        return {"action": "wait", "reasoning": "JSON parse error", "status": "retrying..."}
    except requests.RequestException as exc:
        logger.error("AI API error: %s", exc)
        return {"action": "wait", "reasoning": "API error", "status": "retrying..."}

# ─────────────────────────────────────────────────────────────
# SECTION 19: Action executor
# ─────────────────────────────────────────────────────────────

async def execute_action(decision, bot, chat_id):
    action = decision.get("action", "wait")
    x = decision.get("x", 0)
    y = decision.get("y", 0)
    x2 = decision.get("x2", 0)
    y2 = decision.get("y2", 0)
    text = decision.get("text", "")
    url = decision.get("url", "")
    key = decision.get("key", "")
    direction = decision.get("direction", "down")
    amount = decision.get("amount", 3)

    if action == "click":
        human_click(x, y)
    elif action == "double_click":
        human_double_click(x, y)
    elif action == "right_click":
        human_right_click(x, y)
    elif action == "type":
        human_type(text)
    elif action == "scroll":
        human_scroll(direction, amount)
    elif action == "key":
        press_key(key)
    elif action == "drag":
        human_drag(x, y, x2, y2)
    elif action == "navigate":
        press_key("ctrl+l")
        time.sleep(0.3)
        press_key("ctrl+a")
        time.sleep(0.1)
        human_type(url)
        time.sleep(0.2)
        press_key("enter")
    elif action == "browser_navigate":
        await browser_navigate(url)
    elif action == "browser_click":
        await browser_click(x, y)
    elif action == "browser_type":
        await browser_type(text)
    elif action == "browser_scroll":
        await browser_scroll(direction, amount * 100)
    elif action == "ocr_read":
        ocr_text = ocr_full_screen()
        chunks = [ocr_text[i:i + 3500] for i in range(0, len(ocr_text), 3500)]
        for chunk in chunks:
            bot.send_message(chat_id, f"```\n{chunk}\n```")
    elif action == "wait":
        await asyncio.sleep(2.5)
    elif action == "done":
        pass
    else:
        logger.warning("Unknown action: %s", action)
        await asyncio.sleep(1)

# ─────────────────────────────────────────────────────────────
# SECTION 20: Task runner
# ─────────────────────────────────────────────────────────────

async def run_task(bot, chat_id, instruction):
    history = []
    start_time = time.time()
    bot.send_message(chat_id, f"Starting task: _{instruction}_")
    log_activity("task_start", instruction)

    for step in range(1, MAX_TASK_STEPS + 1):
        bot.send_typing(chat_id)
        try:
            b64 = screenshot_to_base64()
        except Exception as exc:
            bot.send_message(chat_id, f"Screenshot error: {exc}")
            break

        decision = ai_analyze(b64, instruction, history)
        action = decision.get("action", "wait")
        status = decision.get("status", "")
        reasoning = decision.get("reasoning", "")

        caption = f"Step {step} | `{action}`\n{status}"
        try:
            screenshot_bytes = base64.b64decode(b64)
            bot.send_photo(chat_id, screenshot_bytes, caption)
        except Exception:
            bot.send_message(chat_id, caption)

        history.append({"step": step, "action": action, "status": status})

        if action == "done":
            elapsed = time.time() - start_time
            bot.send_message(
                chat_id,
                f"Task complete in {elapsed:.1f}s ({step} steps)\n{reasoning}",
            )
            log_activity("task_done", f"{instruction} | {step} steps | {elapsed:.1f}s")
            nxt = queue_next()
            if nxt:
                await asyncio.sleep(2)
                await run_task(bot, chat_id, nxt)
            return

        try:
            await execute_action(decision, bot, chat_id)
        except Exception as exc:
            bot.send_message(chat_id, f"Action error: {exc}")
            logger.error("Action error step %d: %s", step, exc)

        await asyncio.sleep(random.uniform(1.0, 2.0))

    bot.send_message(chat_id, f"Reached max steps ({MAX_TASK_STEPS}). Stopping.")
    log_activity("task_maxstep", instruction)

# ─────────────────────────────────────────────────────────────
# SECTION 21: Command router
# ─────────────────────────────────────────────────────────────

async def route_command(bot, chat_id, text, update):
    cmd = text.strip()
    low = cmd.lower()

    if low in ("/start", "/help"):
        help_text = (
            "*Local Automation Agent v3*\n\n"
            "Send any text instruction to start a task.\n\n"
            "*Commands:*\n"
            "/shot — Take screenshot\n"
            "/read — OCR full screen\n"
            "/close — Close browser\n"
            "/status — Agent status\n"
            "/queue <task> — Add task to queue\n"
            "/queued — Show queue\n"
            "/clearqueue — Clear queue\n"
            "/schedule <args> — Schedule a task\n"
            "/schedules — List schedules\n"
            "/unschedule <n> — Cancel schedule\n"
            "/memory — Show memory\n"
            "/forget <key> — Delete memory key\n"
            "/log [n] — Show recent activity\n"
        )
        bot.send_message(chat_id, help_text)
        return

    if low == "/shot":
        shot = take_screenshot()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        bot.send_photo(chat_id, shot, f"Screenshot {ts}")
        return

    if low == "/read":
        ocr_text = ocr_full_screen()
        log_activity("ocr_read", f"{len(ocr_text)} chars")
        chunks = [ocr_text[i:i + 3500] for i in range(0, len(ocr_text), 3500)]
        for chunk in chunks:
            bot.send_message(chat_id, f"```\n{chunk}\n```")
        return

    if low == "/close":
        await close_browser()
        bot.send_message(chat_id, "Browser closed.")
        return

    if low == "/status":
        running = "Yes" if state.current_task and not state.current_task.done() else "No"
        status_text = (
            f"*Status*\n"
            f"Running: {running}\n"
            f"Queue size: {len(state.task_queue)}\n"
            f"Schedules: {len([s for s in state.schedules if not s.get('_cancelled')])}\n"
            f"PyAutoGUI: {'OK' if HAS_PYAUTOGUI else 'N/A'}\n"
            f"MSS: {'OK' if HAS_MSS else 'N/A'}\n"
            f"Playwright: {'OK' if HAS_PLAYWRIGHT else 'N/A'}\n"
            f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        bot.send_message(chat_id, status_text)
        return

    if low.startswith("/queue "):
        task = cmd[7:].strip()
        if task:
            queue_add(task)
            bot.send_message(chat_id, f"Added to queue.\n{queue_status()}")
        return

    if low == "/queued":
        bot.send_message(chat_id, queue_status())
        return

    if low == "/clearqueue":
        queue_clear()
        bot.send_message(chat_id, "Queue cleared.")
        return

    if low.startswith("/schedule "):
        args = cmd[10:].strip()
        parsed = parse_schedule_command(args)
        if parsed:
            parsed["_last_ts"] = time.time()
            state.schedules.append(parsed)
            bot.send_message(chat_id, f"Schedule added.\n{list_schedules()}")
        else:
            bot.send_message(
                chat_id,
                "Usage:\n"
                "  /schedule every 2h Check alerts\n"
                "  /schedule every 30m Screenshot\n"
                "  /schedule 08:30 Morning report\n"
                "  /schedule daily 14:00 Run backup",
            )
        return

    if low == "/schedules":
        bot.send_message(chat_id, list_schedules())
        return

    if low.startswith("/unschedule "):
        try:
            n = int(cmd.split()[1])
            active = [s for s in state.schedules if not s.get("_cancelled")]
            if 1 <= n <= len(active):
                active[n - 1]["_cancelled"] = True
                bot.send_message(chat_id, f"Schedule {n} cancelled.\n{list_schedules()}")
            else:
                bot.send_message(chat_id, f"Invalid index. {list_schedules()}")
        except (ValueError, IndexError):
            bot.send_message(chat_id, "Usage: /unschedule <number>")
        return

    if low == "/memory":
        mem = memory_load_all()
        if not mem:
            bot.send_message(chat_id, "Memory is empty.")
        else:
            lines = ["*Memory:*"]
            for k, v in mem.items():
                lines.append(f"  `{k}`: {v['value']}")
            bot.send_message(chat_id, "\n".join(lines))
        return

    if low.startswith("/forget "):
        key = cmd[8:].strip()
        memory_delete(key)
        bot.send_message(chat_id, f"Deleted memory key: `{key}`")
        return

    if low.startswith("/log"):
        parts = cmd.split()
        n = 10
        if len(parts) > 1:
            try:
                n = int(parts[1])
            except ValueError:
                pass
        if os.path.exists(ACTIVITY_LOG_FILE):
            with open(ACTIVITY_LOG_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
            recent = entries[-n:]
            lines = ["*Recent activity:*"]
            for e in recent:
                lines.append(f"  {e['time'][:19]} | {e['type']} | {e['detail'][:60]}")
            bot.send_message(chat_id, "\n".join(lines))
        else:
            bot.send_message(chat_id, "No activity log yet.")
        return

    # Voice message handling
    msg = update.get("message", {})
    if msg.get("voice"):
        file_id = msg["voice"]["file_id"]
        audio = bot.download_file(file_id)
        if audio:
            transcript = transcribe_voice(audio)
            if transcript:
                bot.send_message(chat_id, f"Voice: _{transcript}_")
                state.current_task = asyncio.create_task(run_task(bot, chat_id, transcript))
            else:
                bot.send_message(chat_id, "Could not transcribe. Set OPENAI\\_API\\_KEY env var.")
        return

    # Default: run as task instruction
    if state.current_task and not state.current_task.done():
        state.current_task.cancel()
        await asyncio.sleep(0.5)
    state.current_task = asyncio.create_task(run_task(bot, chat_id, text))

# ─────────────────────────────────────────────────────────────
# SECTION 22: Main
# ─────────────────────────────────────────────────────────────

async def main():
    validate_config()
    bot = TelegramBot(TELEGRAM_BOT_TOKEN)
    start_scheduler(bot, TELEGRAM_CHAT_ID)

    logger.info("=" * 50)
    logger.info("  Local Automation Agent v3 — Starting")
    logger.info("  Playwright: %s | PyAutoGUI: %s | MSS: %s",
                "OK" if HAS_PLAYWRIGHT else "N/A",
                "OK" if HAS_PYAUTOGUI else "N/A",
                "OK" if HAS_MSS else "N/A")
    logger.info("=" * 50)

    try:
        bot.send_message(TELEGRAM_CHAT_ID, "Agent v3 online.")
    except Exception:
        pass
    log_activity("agent_start", "Agent v3 started")

    while True:
        try:
            updates = bot.get_updates()
            for update in updates:
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")

                if chat_id != TELEGRAM_CHAT_ID:
                    if chat_id:
                        bot.send_message(chat_id, "Unauthorized.")
                        log_activity("auth_reject", f"chat_id={chat_id}")
                    continue

                if msg.get("voice"):
                    await route_command(bot, chat_id, "", update)
                elif text:
                    await route_command(bot, chat_id, text, update)

            await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Main loop error: %s", exc)
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        state.schedule_stop.set()
        print("Agent stopped.")
        log_activity("agent_stop", "manual shutdown")
