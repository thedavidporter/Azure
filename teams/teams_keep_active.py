import random
import subprocess
import time
import logging
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Keypress fires after a random delay in this range (seconds)
INTERVAL_MIN = 180   # 3 minutes
INTERVAL_MAX = 420   # 7 minutes

LOG_FILE = "teams_keep_active.log"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keypress via PowerShell
# ---------------------------------------------------------------------------

POWERSHELL_SCRIPT = """
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('+')
"""

def send_keypress():
    result = subprocess.run(
        ["powershell.exe", "-Command", POWERSHELL_SCRIPT],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("PowerShell error: %s", result.stderr.strip())
        return False
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("Teams Keep-Active started")
    log.info("Keypress interval: %d–%d seconds (random)", INTERVAL_MIN, INTERVAL_MAX)
    log.info("Press Ctrl+C to stop")
    log.info("=" * 60)

    while True:
        delay = random.randint(INTERVAL_MIN, INTERVAL_MAX)
        log.info("Next keypress in %d seconds", delay)
        time.sleep(delay)
        success = send_keypress()
        if success:
            log.info("Keypress sent successfully")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
