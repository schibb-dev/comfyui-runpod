# WSL move to E: — follow-up (junction workaround)

**Status as of 2026-07-02:** Ubuntu root disk is on **E:** and WSL is **running**. The official `wsl --manage --move` failed; a **directory junction** on C: points WSL at `E:\WSL\Ubuntu`. The junction works but is a **workaround** — WSL’s registry still says C:. Planned maintenance (when rested): **export/import** to register `E:\WSL\Ubuntu` officially, then remove the junction. Swap (~24 GB on C:) can be done the same session or separately.

| Location | File |
|----------|------|
| Windows (available while WSL is down) | `E:\WSL\WSL_MOVE_TO_E_PROCEDURE.txt` |
| Windows (follow-up summary) | `E:\WSL\WSL_MOVE_FOLLOWUP.txt` |
| Windows (**tomorrow’s checklist**) | `E:\WSL\WSL_PLANNED_MAINTENANCE.txt` |
| Repo | `scripts/wsl_move_distro_to_e.ps1`, `scripts/wslconfig-move-swap.example` |

---

## What we were trying to do

Free ~190 GB on **C:** by moving the Ubuntu virtual disk (`ext4.vhdx`) to **E:\WSL\Ubuntu**.

WSL2 stores that file under a GUID folder, typically:

```text
C:\Users\<you>\AppData\Local\wsl\{b7fa9724-762b-4f5c-9a5a-0d53a75bab60}\ext4.vhdx
```

The planned tool was:

```powershell
wsl --manage Ubuntu --move E:\WSL\Ubuntu
```

---

## What actually happened

| When | Event |
|------|--------|
| 2026-07-01 ~16:53 | `wsl --shutdown`, then `wsl --manage Ubuntu --move E:\WSL\Ubuntu` started |
| 2026-07-01 ~17:33 | **Failed** after ~39 min: `Wsl/Service/MoveDistro/E_ACCESSDENIED` |
| 2026-07-01 ~23:38 | Retry blocked: `E:\WSL\Ubuntu` already contained `ext4.vhdx` (~190 GB) |
| After failure | **Manual fix:** NTFS **junction** from the original C: GUID path → `E:\WSL\Ubuntu` |
| Result | Ubuntu boots; vhdx bytes live on **E:**; WSL still opens the file via the **C:** path |

Evidence on disk:

- `E:\WSL\Ubuntu\ext4.vhdx` — ~190 GB (physical location)
- `C:\Users\yuji\AppData\Local\wsl\{b7fa9724-762b-4f5c-9a5a-0d53a75bab60}` — **Junction** → `E:\WSL\Ubuntu`
- `...\{guid}.empty-after-failed-move` — empty placeholder (old folder renamed aside)
- `E:\WSL\move.log` — failure + retry lines

The Microsoft Store package path `...\Packages\CanonicalGroupLimited.Ubuntu_...\LocalState` is **empty**; that is normal when the registered distro disk is under `AppData\Local\wsl\{guid}\`.

---

## How the junction fix works (plain language)

Windows **junction** (`mklink /J`) is a directory link: anything that opens the C: path is redirected to E:.

```text
  WSL service
      │
      ▼
  C:\Users\yuji\AppData\Local\wsl\{guid}\ext4.vhdx   ← path WSL expects
      │
      │  (junction — transparent redirect)
      ▼
  E:\WSL\Ubuntu\ext4.vhdx                            ← bytes on disk
```

Inside Ubuntu nothing changes: `~/src/comfyui-runpod`, `~/comfyui-runpod-data`, and `.env` are the same paths as before. Only the **host-side vhdx file location** moved.

**Junction vs symlink:** use a **junction** for this case (directory-only, usually no admin). Symlinks (`mklink /D`) often need Developer Mode or elevation.

---

## Junction vs official registration

| | Junction (today) | Official registration (target) |
|--|------------------|----------------------------------|
| **WSL registry `BasePath`** | `C:\Users\yuji\AppData\Local\wsl\{guid}\` | `E:\WSL\Ubuntu` |
| **Where vhdx bytes live** | E: (via redirect) | E: (direct) |
| **Inside Ubuntu** | Same paths | Same paths |
| **Supported by Microsoft** | No (workaround) | Yes |
| **Risk if E: unavailable** | Won’t boot | Won’t boot |
| **Risk on WSL update / re-move** | Junction may conflict | Low |

**Is the junction broken?** No — it is stable enough to keep using until you do maintenance. **Is official registration better?** Yes — one less layer, registry matches reality, no C: redirect to maintain.

**Do not** re-run `wsl --manage --move` on top of the current junction without a backup and a written plan (see below).

---

## Related migration (Comfy bind data — already done)

Separately from the WSL vhdx move, **Comfy input/output/user** were migrated from the E: shadow tree to WSL ext4:

| Before | After |
|--------|--------|
| `/mnt/e/comfyui-runpod-shadow/workspace/...` | `/home/yuji/comfyui-runpod-data/...` |

Recorded in `~/comfyui-runpod-data/.migration_sources.env` and `.env.bak.migrate.20260430000730`. Models remain on `/mnt/e/models`.

These two moves are independent: the vhdx holds the Linux filesystem; bind mounts in `.env` point at paths **inside** that filesystem.

---

## Verify (quick checklist)

Run from **Windows PowerShell** (paths) and **Ubuntu** (inside WSL).

### Windows

```powershell
# Junction still points at E:
(Get-Item "C:\Users\yuji\AppData\Local\wsl\{b7fa9724-762b-4f5c-9a5a-0d53a75bab60}").Target

# VHDX size on E:
(Get-Item "E:\WSL\Ubuntu\ext4.vhdx").Length / 1GB

# No stray ext4.vhdx under your profile on C:
Get-ChildItem "C:\Users\yuji" -Filter ext4.vhdx -Recurse -Force -ErrorAction SilentlyContinue

wsl --list -v
```

### Ubuntu

```bash
df -h /
ls ~/src/comfyui-runpod/.env
ls ~/comfyui-runpod-data/input | head
docker context show   # expect desktop-linux if using Docker Desktop
```

Expect: Ubuntu **Running**, `/` on ext4 with comfortable free space, repo and data dirs present.

---

## Planned maintenance — convert junction → official registration

**When:** a rested session with ~1–2 hours of wall time (mostly export/import I/O).  
**Where to run:** Windows PowerShell only (not inside WSL/Cursor during export/unregister).  
**Short checklist on E:** `E:\WSL\WSL_PLANNED_MAINTENANCE.txt`

### Before you start

- [ ] **E: free space:** need room for a **full export tar** (~190 GB compressed varies; budget **≥200 GB free** on E: while the old vhdx still exists). Check: File Explorer → E: properties.
- [ ] **Nothing important running** in Ubuntu (stop Comfy stack: `npm run down` or `docker compose down`).
- [ ] **Close Cursor / WSL terminals** before `wsl --shutdown` (you will be disconnected).
- [ ] **Read** this section once end-to-end before executing.

### Recommended session order

1. **Export / import** (this section) — fix WSL registration + remove junction  
2. **Move swap to E:** — see [Remaining follow-up §1](#1-move-swap-off-c--24-gb--recommended) (~15 min)  
3. **Housekeeping** — delete backup tar, old vhdx folder, empty `{guid}.empty-after-failed-move`

Swap can be done before or after official registration; doing registration first avoids touching `.wslconfig` mid-migration.

### Method A — Export / import (recommended, safest)

Uses supported WSL commands. Temporarily needs **two copies** on E: (old vhdx + export tar) until you delete the old tree.

**Paths used below** (adjust dates as needed):

```text
E:\WSL\backups\ubuntu-20260703.tar     ← export backup
E:\WSL\Ubuntu                         ← official import target (new registration)
E:\WSL\Ubuntu.pre-junction-era        ← renamed old folder (delete after verify)
```

**Steps** — run in **Windows PowerShell**:

```powershell
# 0. Prep
New-Item -ItemType Directory -Force -Path E:\WSL\backups | Out-Null
wsl --list -v

# 1. Stop everything WSL
wsl --shutdown
Start-Sleep -Seconds 5

# 2. Export (30–90+ min for ~190 GB — do not interrupt)
wsl --export Ubuntu E:\WSL\backups\ubuntu-20260703.tar

# 3. Sanity-check backup exists and is non-trivial size
(Get-Item E:\WSL\backups\ubuntu-20260703.tar).Length / 1GB

# 4. Unregister — removes WSL registration; files on E: and the junction are NOT deleted by this alone
wsl --unregister Ubuntu

# 5. Remove the C: junction (distro no longer registered; safe now)
cmd /c rmdir "C:\Users\yuji\AppData\Local\wsl\{b7fa9724-762b-4f5c-9a5a-0d53a75bab60}"

# 6. Preserve old vhdx folder out of the way (import wants a clean or new target)
Rename-Item E:\WSL\Ubuntu E:\WSL\Ubuntu.pre-junction-era

# 7. Import — creates fresh registration with BasePath = E:\WSL\Ubuntu
wsl --import Ubuntu E:\WSL\Ubuntu E:\WSL\backups\ubuntu-20260703.tar --version 2

# 8. Set default distro + default user (import often lands as root first)
wsl --set-default Ubuntu
ubuntu.exe config --default-user yuji
# If ubuntu.exe is not on PATH, after first boot edit /etc/wsl.conf inside Ubuntu:
#   [user]
#   default=yuji

# 9. Verify registration points at E: (not C: junction path)
Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss |
  ForEach-Object {
    if ($_.GetValue('DistributionName') -eq 'Ubuntu') {
      $_.GetValue('BasePath')
    }
  }
# Expect: E:\WSL\Ubuntu

# 10. Boot test
wsl -d Ubuntu -e bash -lc "echo OK; whoami; df -h /; ls ~/src/comfyui-runpod/.env"
```

**Inside Ubuntu after import** — quick dev stack check:

```bash
docker context use desktop-linux   # if using Docker Desktop
cd ~/src/comfyui-runpod
npm run up
```

**After 24–48 h of normal use**, delete reclaimable files:

```powershell
# Old vhdx tree (≈190 GB)
Remove-Item -Recurse -Force E:\WSL\Ubuntu.pre-junction-era

# Export tar (same size order as export)
Remove-Item E:\WSL\backups\ubuntu-20260703.tar

# Optional empty placeholder
Remove-Item -Recurse -Force "C:\Users\yuji\AppData\Local\wsl\{b7fa9724-762b-4f5c-9a5a-0d53a75bab60}.empty-after-failed-move"
```

Append to `E:\WSL\move.log`:

```text
2026-07-XX — official import to E:\WSL\Ubuntu; junction removed; BasePath verified
```

### Method B — Retry `wsl --manage --move` (not recommended first)

Only consider if export space is impossible. The first attempt failed with `E_ACCESSDENIED` after ~39 min; retry may work with Docker/antivirus stopped, but with the vhdx **already on E:** and a **junction in place**, behavior is unpredictable. Prefer Method A.

If you ever retry without export: you must understand that `--move` updates registry **and** moves files — do not run it blindly while a junction exists.

### Method C — Keep the junction (valid deferral)

If tomorrow you are tired or E: is tight on space: **do nothing** to registration. The junction is fine for daily dev. Revisit Method A when you have space and energy.

---

## Remaining follow-up

### 1. Move swap off C: (~24 GB) — **recommended**

Current `.wslconfig` still uses:

```ini
swap=24GB
swapfile=C:\\Users\\yuji\\wsl-swap.vhdx
```

**Steps** (merge with your existing `memory=` / `processors=` lines — do not drop them):

1. Edit `C:\Users\yuji\.wslconfig` and set:

   ```ini
   swapFile=E:\\WSL\\wsl-swap.vhdx
   swap=8192
   ```

   (See `scripts/wslconfig-move-swap.example` in the repo; tune `swap=` if you want more than 8 GB on E:.)

2. From **Windows PowerShell**:

   ```powershell
   wsl --shutdown
   ```

3. Start Ubuntu again (Start menu or `wsl -d Ubuntu`).

4. Confirm swap file exists on E: and WSL is healthy:

   ```powershell
   Test-Path E:\WSL\wsl-swap.vhdx
   wsl -d Ubuntu -e df -h /
   ```

5. **Only after verify**, delete the old swap on C:

   ```powershell
   Remove-Item "C:\Users\yuji\wsl-swap.vhdx"
   ```

### 2. Optional housekeeping

- **`{guid}.empty-after-failed-move`** — empty folder under `AppData\Local\wsl\`; safe to delete once you are satisfied the junction setup is stable.
- **`~/comfyui-runpod-data/.tmp-comfyui-runpod-v1.2.0.tar*`** — large temp archive (~16 GB); delete if you no longer need it for restore testing.
- **E: shadow** (`E:\comfyui-runpod-shadow`) — already gone from E:; bind data lives in `~/comfyui-runpod-data`. If you find another copy elsewhere, treat it as backup until you confirm parity.

### 3. Do **not** do without reading [Planned maintenance](#planned-maintenance--convert-junction--official-registration)

- **`wsl --unregister Ubuntu`** — destroys registration; always **`wsl --export` first**.
- **Re-run `wsl --manage --move`** while the junction is in place — unpredictable.
- **Delete `E:\WSL\Ubuntu`** — that **is** your root filesystem (or will be after import).

---

## If Ubuntu stops booting

1. Read `E:\WSL\move.log` (last 30 lines).
2. Confirm the junction target exists: `E:\WSL\Ubuntu\ext4.vhdx`.
3. Confirm junction:

   ```powershell
   cmd /c dir "C:\Users\yuji\AppData\Local\wsl\{b7fa9724-762b-4f5c-9a5a-0d53a75bab60}"
   ```

4. Do **not** unregister without a recent `wsl --export` backup.

---

## Resume normal dev

```bash
cd ~/src/comfyui-runpod
npm run up
npm run ui:dev:start
```

ComfyUI: `http://localhost:8188/` (or `COMFYUI_HOST_PORT`). Experiments UI: `http://127.0.0.1:5178/`.

---

## Recording future changes

When you complete swap move, official registration, or other host-side steps, add a line to `E:\WSL\move.log` by hand, e.g.:

```text
2026-07-XX — official import to E:\WSL\Ubuntu; junction removed
2026-07-XX — swap moved to E:\WSL\wsl-swap.vhdx; deleted C:\Users\yuji\wsl-swap.vhdx
```

That keeps a single timeline next to the original automated log.
