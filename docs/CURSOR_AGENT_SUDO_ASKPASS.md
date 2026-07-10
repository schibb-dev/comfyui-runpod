# Cursor agent — sudo / askpass troubleshooting

When the Cursor **agent** runs `sudo`, it does **not** use your normal Ubuntu terminal session. It uses **`sudo -A`** and **`cursor-askpass`** (a Unix socket between the agent and the Cursor UI).

Symptoms of a broken askpass:

- Popup appears, you enter the password, UI shows **“Authenticating”**, then nothing
- Agent shell commands hang; logs show `cursor-askpass: password request timed out`
- `sudo -n true` in the agent always fails
- No `/var/lib/sudo/ts/` tickets from agent auth

This doc: **fix askpass first**, then **fallbacks** if it still fails.

| Location | File |
|----------|------|
| Repo | `docs/CURSOR_AGENT_SUDO_ASKPASS.md` |
| Windows (WSL down / easy find) | `E:\WSL\CURSOR_AGENT_SUDO_ASKPASS.txt` |
| Cleanup script | `scripts/cleanup_cursor_agent_askpass.sh` |
| Copy on E: | `E:\WSL\cleanup_cursor_agent_askpass.sh` |

---

## Step 1 — Fix askpass (try this first)

Run from a **normal WSL terminal** (Windows Terminal / Ubuntu app). **Not** from inside the Cursor agent.

```bash
cd ~/src/comfyui-runpod

# Preview
bash scripts/cleanup_cursor_agent_askpass.sh --dry-run

# Interactive cleanup
bash scripts/cleanup_cursor_agent_askpass.sh

# Or non-interactive
bash scripts/cleanup_cursor_agent_askpass.sh --yes
```

The script:

1. Refuses to run if `CURSOR_AGENT` / `CURSOR_ASKPASS_SOCKET` are set (agent shell)
2. Stops stale `agent` / `worker-server` processes and hung `sudo -A`
3. Removes orphaned `/tmp/cursor-askpass-*.sock` files

**Then:**

1. Close any stuck Cursor agent tabs / chats
2. Start **one** fresh agent session (`cursor agent` or a new chat — avoid multiple `resume` sessions)
3. Test inside the agent:

   ```bash
   sudo -A true && echo "askpass OK"
   ```

4. Complete the askpass popup when prompted

**Why this works:** Multiple old agent processes each listen on a different askpass socket. The UI can authenticate against the wrong listener while `sudo` waits on another → timeout. Cleanup leaves one agent, one socket.

---

## Step 2 — If askpass works again

Expectations:

- You will still get askpass **popups in the agent** — this is normal; it is not your terminal’s “sudo once per day” cache
- Heavy `sudo` work (docker dir inspection, system changes) is still smoother in a **normal WSL terminal**

Optional: append to `E:\WSL\move.log` when fixed:

```text
YYYY-MM-DD — cursor askpass cleanup; sudo -A true OK in agent
```

---

## Step 3 — If askpass still fails after cleanup

Try these in order.

### 3a. Confirm the failure mode

In a **normal WSL terminal** (should work):

```bash
sudo true && echo "terminal sudo OK"
```

In the **agent** after fresh start:

```bash
sudo -A true
```

| Terminal OK, agent fails | Askpass / Cursor bridge issue — continue below |
| Both fail | Linux sudo / password issue — fix outside Cursor first |

Check for duplicate agents (should be none after cleanup):

```bash
pgrep -af 'agent.*index.js'
ls -la /tmp/cursor-askpass-*.sock
```

### 3b. Do the task in your WSL terminal (no agent sudo)

Run `sudo` commands yourself in Ubuntu / Windows Terminal. Tell the agent to **only** use:

- `docker …` (you are in the `docker` group)
- `du` / `ls` under `~/` and `/mnt/e/…`
- `npm run …` for the Comfy stack

This avoids askpass entirely for most comfyui-runpod work.

### 3c. Narrow passwordless sudo (agent-friendly, no popup)

Only if you accept passwordless **specific read-only** commands. From a **normal terminal**:

```bash
sudo visudo -f /etc/sudoers.d/yuji-agent
```

Example (adjust paths/commands as needed):

```sudoers
# Read-only inspection of docker storage — no password for agent
Cmnd_Alias AGENT_DOCKER_READ = \
    /usr/bin/du -sh /var/lib/docker, \
    /usr/bin/du -sh /var/lib/docker/*, \
    /usr/bin/du -sh /var/snap/docker/common/var-lib-docker, \
    /usr/bin/du -sh /var/snap/docker/common/var-lib-docker/*, \
    /bin/ls -la /var/lib/docker, \
    /bin/ls -la /var/snap/docker/common/var-lib-docker

yuji ALL=(root) NOPASSWD: AGENT_DOCKER_READ
```

Validate:

```bash
sudo visudo -c
sudo -n du -sh /var/lib/docker
```

**Do not** use `NOPASSWD: ALL` unless you fully accept the risk.

### 3d. Avoid needing root: use Docker CLI instead of `/var/lib/docker`

You rarely need to `ls` docker’s data root. Prefer:

```bash
docker system df -v
docker images
docker compose ps
```

For Comfy disk usage, use user paths:

```bash
du -sh ~/comfyui-runpod-data ~/src/comfyui-runpod
```

### 3e. Docker Desktop migration (structural; reduces future root needs)

Snap Docker stores ~67 GB **inside** the Ubuntu vhdx under root-owned paths. Migrating to Docker Desktop moves the engine off snap and matches the repo’s long-term plan.

From repo (one-time; uses sudo in **your** terminal, not the agent):

```bash
# After Docker Desktop WSL integration is enabled for Ubuntu
bash ~/src/comfyui-runpod/scripts/wsl_migrate_to_docker_desktop.sh
```

Then Windows: `wsl --shutdown`, start Docker Desktop, in Ubuntu:

```bash
docker context use desktop-linux
cd ~/src/comfyui-runpod && npm run up
```

See also `docs/WSL_MOVE_TO_E_FOLLOWUP.md` and `E:\WSL\WSL_PLANNED_MAINTENANCE.txt`.

### 3f. Cursor / agent restart (last resort before support)

1. Run cleanup script again (`--yes`)
2. Quit Cursor completely (all windows)
3. Reopen repo via Remote WSL
4. Single new agent chat — retest `sudo -A true`

If it still hangs on “Authenticating”, treat as a **Cursor WSL askpass bug** and use **3b–3d** until Cursor fixes it.

---

## Quick reference

| Goal | Action |
|------|--------|
| Fix stuck askpass | `bash scripts/cleanup_cursor_agent_askpass.sh` → one fresh agent → `sudo -A true` |
| Agent work without sudo | `docker …`, `du` on `~/…`, `npm run …` |
| One-off root inspection | Your WSL terminal, not the agent |
| Agent needs specific root reads | `sudoers.d` NOPASSWD for named commands only |
| Long-term docker + disk | Docker Desktop migration script |

---

## Related docs

- [WSL_MOVE_TO_E_FOLLOWUP.md](./WSL_MOVE_TO_E_FOLLOWUP.md) — vhdx / junction / planned maintenance
- [DOCUMENTATION.md](../DOCUMENTATION.md) — doc index
