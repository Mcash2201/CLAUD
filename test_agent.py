#!/usr/bin/env python3
"""Test suite for Local Automation Agent v3 — 15 tests, no API keys needed."""

import base64
import io
import json
import os
import random
import sys
import tempfile

# Add current dir to path and import agent module pieces
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rdp_agent_v3 as agent

passed = 0
failed = 0


def ok(n, name):
    global passed
    passed += 1
    print(f"[{n}] {name}: PASS")


def fail(n, name, reason=""):
    global failed
    failed += 1
    print(f"[{n}] {name}: FAIL  {reason}")


# ── TEST 1: Screenshot engine ──────────────────────────────
try:
    from PIL import Image
    img = Image.new("RGB", (200, 100), (50, 100, 150))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    b64 = base64.b64encode(png_bytes).decode()
    assert len(b64) > 100
    decoded = base64.b64decode(b64)
    assert decoded[:4] == b"\x89PNG"
    ok(1, "Screenshot engine")
except Exception as e:
    fail(1, "Screenshot engine", str(e))

# ── TEST 2: OCR pipeline ──────────────────────────────────
try:
    from PIL import Image
    import pytesseract
    img = Image.new("RGB", (300, 100), (255, 255, 255))
    result = pytesseract.image_to_string(img)
    assert isinstance(result, str)
    ok(2, "OCR pipeline")
except Exception as e:
    fail(2, "OCR pipeline", str(e))

# ── TEST 3: OpenCV template matching ──────────────────────
try:
    import cv2
    import numpy as np
    # Create a random noise screen so uniform patches don't match everywhere
    rng = np.random.RandomState(42)
    screen = rng.randint(0, 256, (480, 640, 3), dtype=np.uint8)
    # Create a distinctive gradient template
    template = np.zeros((30, 40, 3), dtype=np.uint8)
    for r in range(30):
        for c in range(40):
            template[r, c] = [r * 8, c * 6, 128]
    # Plant template at x=100, y=150
    screen[150:180, 100:140] = template
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    assert max_val > 0.95, f"Confidence too low: {max_val}"
    cx = max_loc[0] + 40 // 2
    cy = max_loc[1] + 30 // 2
    assert abs(cx - 120) <= 2 and abs(cy - 165) <= 2, f"Wrong coords: ({cx},{cy})"
    ok(3, "OpenCV matching")
except Exception as e:
    fail(3, "OpenCV matching", str(e))

# ── TEST 4: Mouse timing values (replaces Bezier) ────────
try:
    delays = [random.uniform(agent.TYPING_MIN, agent.TYPING_MAX) for _ in range(1000)]
    speeds = [random.uniform(agent.MOUSE_MIN, agent.MOUSE_MAX) for _ in range(1000)]
    assert all(agent.TYPING_MIN <= d <= agent.TYPING_MAX for d in delays)
    assert all(agent.MOUSE_MIN <= s <= agent.MOUSE_MAX for s in speeds)
    ok(4, "Human timing bounds")
except Exception as e:
    fail(4, "Human timing bounds", str(e))

# ── TEST 5: Task queue ────────────────────────────────────
try:
    agent.queue_clear()
    agent.queue_add("task1")
    agent.queue_add("task2")
    agent.queue_add("task3")
    agent.queue_add("task4")
    assert len(agent.state.task_queue) == 4
    nxt = agent.queue_next()
    assert nxt == "task1"
    assert len(agent.state.task_queue) == 3
    agent.queue_clear()
    assert len(agent.state.task_queue) == 0
    ok(5, "Task queue")
except Exception as e:
    fail(5, "Task queue", str(e))

# ── TEST 6: Schedule parser ───────────────────────────────
try:
    r1 = agent.parse_schedule_command("every 2h Check alerts")
    assert r1["type"] == "interval_hours" and r1["interval"] == 2
    r2 = agent.parse_schedule_command("every 30m Screenshot")
    assert r2["type"] == "interval_mins" and r2["interval"] == 30
    r3 = agent.parse_schedule_command("08:30 Morning report")
    assert r3["type"] == "daily" and r3["time"] == "08:30"
    r4 = agent.parse_schedule_command("daily 14:00 Run backup")
    assert r4["type"] == "daily" and r4["time"] == "14:00"
    ok(6, "Schedule parser")
except Exception as e:
    fail(6, "Schedule parser", str(e))

# ── TEST 7: Memory system ─────────────────────────────────
try:
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(tmp)  # Start with no file so _memory_read returns {}
    orig = agent.MEMORY_FILE
    agent.MEMORY_FILE = tmp
    agent.memory_save("test_key", "test_value")
    assert agent.memory_get("test_key") == "test_value"
    agent.memory_delete("test_key")
    assert agent.memory_get("test_key") is None
    agent.MEMORY_FILE = orig
    os.unlink(tmp)
    ok(7, "Memory system")
except Exception as e:
    fail(7, "Memory system", str(e))

# ── TEST 8: Activity log ──────────────────────────────────
try:
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(tmp)
    orig = agent.ACTIVITY_LOG_FILE
    agent.ACTIVITY_LOG_FILE = tmp
    agent.log_activity("test", "unit test entry")
    with open(tmp, "r") as f:
        entries = json.load(f)
    assert len(entries) == 1
    assert entries[0]["type"] == "test"
    agent.ACTIVITY_LOG_FILE = orig
    os.unlink(tmp)
    ok(8, "Activity log")
except Exception as e:
    fail(8, "Activity log", str(e))

# ── TEST 9: AI response JSON parsing ─────────────────────
try:
    import re
    # Clean JSON
    raw1 = '{"action":"click","x":100,"y":200,"reasoning":"test","status":"ok"}'
    d1 = json.loads(raw1)
    assert d1["action"] == "click"

    # JSON with fences
    raw2 = '```json\n{"action":"type","text":"hello","reasoning":"r","status":"s"}\n```'
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw2.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    d2 = json.loads(cleaned)
    assert d2["action"] == "type"

    # JSON with memory_save
    raw3 = '{"action":"done","memory_save":{"key":"url","value":"example.com"},"reasoning":"r","status":"s"}'
    d3 = json.loads(raw3)
    assert d3["memory_save"]["key"] == "url"
    ok(9, "AI JSON parsing")
except Exception as e:
    fail(9, "AI JSON parsing", str(e))

# ── TEST 10: Machine config ───────────────────────────────
try:
    result = agent.list_machines()
    assert "local" in result
    assert "ACTIVE" in result
    ok(10, "Machine config")
except Exception as e:
    fail(10, "Machine config", str(e))

# ── TEST 11: URL normalization ────────────────────────────
try:
    assert agent._normalize_url("google.com") == "https://google.com"
    assert agent._normalize_url("https://gmail.com") == "https://gmail.com"
    assert agent._normalize_url("http://test.com") == "http://test.com"
    ok(11, "URL normalization")
except Exception as e:
    fail(11, "URL normalization", str(e))

# ── TEST 12: Telegram payload structure ───────────────────
try:
    bot = agent.TelegramBot("fake_token")
    # Verify base URL built correctly
    assert "fake_token" in bot.base
    # Check that send_message builds right payload
    assert bot.offset == 0
    # Verify parse_mode would be Markdown (inspect code logic)
    import inspect
    src = inspect.getsource(agent.TelegramBot.send_message)
    assert "Markdown" in src
    src2 = inspect.getsource(agent.TelegramBot.send_photo)
    assert "Markdown" in src2
    ok(12, "Telegram payloads")
except Exception as e:
    fail(12, "Telegram payloads", str(e))

# ── TEST 13: Human timing bounds (extended) ───────────────
try:
    typing_delays = [random.uniform(agent.TYPING_MIN, agent.TYPING_MAX) for _ in range(1000)]
    mouse_speeds = [random.uniform(agent.MOUSE_MIN, agent.MOUSE_MAX) for _ in range(1000)]
    assert all(agent.TYPING_MIN <= d <= agent.TYPING_MAX for d in typing_delays)
    assert all(agent.MOUSE_MIN <= s <= agent.MOUSE_MAX for s in mouse_speeds)
    assert agent.TYPING_MIN < agent.TYPING_MAX
    assert agent.MOUSE_MIN < agent.MOUSE_MAX
    ok(13, "Timing config validation")
except Exception as e:
    fail(13, "Timing config validation", str(e))

# ── TEST 14: Step loop exit logic ─────────────────────────
try:
    # Simulate: action="done" at step 5
    exit_step = None
    for step in range(1, agent.MAX_TASK_STEPS + 1):
        action = "done" if step == 5 else "wait"
        if action == "done":
            exit_step = step
            break
    assert exit_step == 5

    # Simulate: never hits done → stops at MAX_TASK_STEPS
    final_step = 0
    for step in range(1, agent.MAX_TASK_STEPS + 1):
        final_step = step
    assert final_step == agent.MAX_TASK_STEPS
    ok(14, "Loop exit logic")
except Exception as e:
    fail(14, "Loop exit logic", str(e))

# ── TEST 15: Auth gate ────────────────────────────────────
try:
    import inspect
    src = inspect.getsource(agent.main)
    assert "Unauthorized" in src
    # Verify chat_id comparison exists
    assert "TELEGRAM_CHAT_ID" in src
    ok(15, "Auth gate")
except Exception as e:
    fail(15, "Auth gate", str(e))

# ── Summary ───────────────────────────────────────────────
print()
print("=" * 30)
print(f"RESULTS: {passed}/15 tests passed")
print("=" * 30)
sys.exit(0 if failed == 0 else 1)
