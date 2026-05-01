#!/usr/bin/env bash
# Run from WSL (canonical repo: ~/src/comfyui-runpod).
# Cursor on Windows may inject "git commit" trailers that break PowerShell; calling
# /usr/bin/git from this script avoids that class of wrapper issues.
#
# Usage:
#   printf '%s\n' 'subject line' '' 'body' > /tmp/msg.txt
#   COMMIT_MSG_FILE=/tmp/msg.txt bash scripts/wsl-git-commit-staged.sh
#
# Or pass the message file as the first argument:
#   bash scripts/wsl-git-commit-staged.sh /tmp/msg.txt
#
set -eu
cd /home/yuji/src/comfyui-runpod
export GIT_EDITOR=true

msgfile="${COMMIT_MSG_FILE:-}"
if [ -z "$msgfile" ] && [ -n "${1:-}" ]; then
  msgfile=$1
fi
if [ -z "$msgfile" ] || [ ! -f "$msgfile" ]; then
  echo "Usage: COMMIT_MSG_FILE=/path/to/file.txt $0" >&2
  echo "   or: $0 /path/to/message.txt" >&2
  exit 1
fi

/usr/bin/git commit --no-verify -F "$msgfile"
