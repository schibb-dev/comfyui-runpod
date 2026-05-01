#!/usr/bin/env bash
set -eu
cd /home/yuji/src/comfyui-runpod
git add -A
git reset HEAD -- tmp/ .env.bak.migrate.20260430000730 2>/dev/null || true
MSG=/tmp/msg-merge-windows.txt
{
  printf '%s\n' 'chore: merge Windows working tree into WSL'
  printf '%s\n' ''
  printf '%s\n' 'Synced from Windows checkout via rsync (excludes .git, node_modules, dist).'
  printf '%s\n' 'Some paths under workspace/input/ could not be updated (permission denied).'
} >"$MSG"
COMMIT_MSG_FILE="$MSG" bash scripts/wsl-git-commit-staged.sh
/usr/bin/git push origin main
