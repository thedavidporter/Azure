#!/bin/bash
# Reads prompt from Claude hook stdin JSON and saves session if user typed "exit" or "save session"
PROMPT=$(jq -r '.prompt // ""' 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr -d '\n')
if [ "$PROMPT" = "exit" ] || [ "$PROMPT" = "save session" ]; then
  /home/thedavidporter/save_session.sh
fi
