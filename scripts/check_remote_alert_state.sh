#!/usr/bin/env bash
set -euo pipefail

state_blob() {
  local blob
  if blob=$(git rev-parse --verify "$1:internship_alert_state.json" 2>/dev/null); then
    printf '%s' "$blob"
  else
    printf '__MISSING__'
  fi
}

base_state=$(state_blob "$1")
remote_state=$(state_blob "$2")
if [ "$base_state" != "$remote_state" ]; then
  echo 'Remote alert state changed during this run; refusing to overwrite it.' >&2
  exit 1
fi
