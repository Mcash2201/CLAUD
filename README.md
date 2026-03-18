# Local Automation Agent v3

A Telegram-controlled automation bot for your local machine. Send text instructions via Telegram, and the agent uses Claude Vision AI to read your screen and execute actions autonomously — clicking, typing, scrolling, navigating — until the task is complete.

## Quick Setup

1. Edit `rdp_agent_v3.py` and set these three values:
   - `TELEGRAM_BOT_TOKEN` — Get from [@BotFather](https://t.me/BotFather)
   - `TELEGRAM_CHAT_ID` — Get from [@userinfobot](https://t.me/userinfobot)
   - `ANTHROPIC_API_KEY` — Get from [console.anthropic.com](https://console.anthropic.com)

2. Install dependencies:

   **Linux:**
   ```bash
   pip3 install -r requirements.txt
   python3 -m playwright install chromium
   sudo apt install tesseract-ocr ffmpeg
   ```

   **Windows:**
   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```
   Install [Tesseract](https://github.com/tesseract-ocr/tesseract) manually on Windows.

3. Run:
   ```bash
   python3 rdp_agent_v3.py
   ```

Or use the automated setup script:
```bash
python3 setup.py
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start`, `/help` | Show help menu |
| `/shot` | Take and send a screenshot |
| `/read` | OCR the full screen and send text |
| `/close` | Close the Playwright browser |
| `/status` | Show agent status |
| `/queue <task>` | Add task to queue |
| `/queued` | Show queued tasks |
| `/clearqueue` | Clear the task queue |
| `/schedule <args>` | Schedule a recurring task |
| `/schedules` | List active schedules |
| `/unschedule <n>` | Cancel a schedule |
| `/memory` | Show persistent memory |
| `/forget <key>` | Delete a memory key |
| `/log [n]` | Show recent activity log |
| *Any other text* | Run as an AI-driven task |

## Scheduling Tasks

```
/schedule every 2h Check server status
/schedule every 30m Take a screenshot
/schedule 08:30 Morning system check
/schedule daily 14:00 Run backup verification
```

## Task Queue

Queue multiple tasks to run sequentially:
```
/queue Open browser and go to example.com
/queue Take a screenshot of the homepage
/queue Close the browser
```
Tasks run one after another when the current task finishes.

## OpenCV Template Detection

Save a region of the screen as a template, then the agent can find and click matching elements:

1. Templates are stored in the `templates/` directory as PNG files
2. The AI can use `find_element` to locate UI elements by visual matching
3. Useful for clicking buttons that always look the same

## Voice Commands

Send a voice note in Telegram to control the agent by voice.
Requires `OPENAI_API_KEY` environment variable for Whisper transcription.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `TELEGRAM_BOT_TOKEN is not set` | Edit the config at the top of `rdp_agent_v3.py` |
| `Playwright is not installed` | Run `pip install playwright && python -m playwright install chromium` |
| PyAutoGUI not available | Install X11 dev headers: `sudo apt install python3-tk python3-dev` |
| OCR returns empty text | Install tesseract: `sudo apt install tesseract-ocr` |
| Screenshot is blank/dark | Ensure a display is available (use `xvfb-run` on headless servers) |

## Stopping the Agent

- Press **Ctrl+C** in the terminal
- If PyAutoGUI is active, move your mouse to the top-left corner (failsafe)
