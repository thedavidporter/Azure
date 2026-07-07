#!/bin/bash

LOGDIR="/home/thedavidporter/session_logs"
LOGFILE="$LOGDIR/session_$(date '+%Y%m%d_%H%M%S').log"

{
  echo "======================================================"
  echo " Claude Code Session Log"
  echo " Date    : $(date '+%Y-%m-%d %H:%M:%S')"
  echo " User    : $(whoami)"
  echo "======================================================"
  echo ""
  echo "--- BASH HISTORY (this session) ---"
  echo ""
  history | tail -100
  echo ""
  echo "======================================================"
  echo " End of Session Log"
  echo "======================================================"
} > "$LOGFILE"

echo "Session saved to: $LOGFILE"
