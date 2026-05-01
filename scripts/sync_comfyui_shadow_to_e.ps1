<#
  ---------------------------------------------------------------------------
  TRANSITION / MIGRATION HELPER (Windows host — PowerShell)
  ---------------------------------------------------------------------------
  Purpose: one-way copy of git-ignored and local-only state from a checkout
  on this PC into a fixed "shadow tree" on E: (E:\comfyui-runpod-shadow) that
  mirrors repo-relative paths. Use when moving primary work to WSL or before
  retiring a Windows-side clone — not required for day-to-day dev after you
  bind-mount the shadow from Linux (see README "Host dev on WSL2").
  ---------------------------------------------------------------------------

.SYNOPSIS
  Copy local-only comfyui-runpod state to E:\comfyui-runpod-shadow (repo-relative layout).

.DESCRIPTION
  Mirrors paths that git does not restore: .env, credentials/, repo output/,
  workspace tokens, workspace/comfyui_user, workspace/input|output|models.
  Uses robocopy for directories (multi-threaded). Safe to re-run; robocopy
  exit codes 0-7 are success.

.PARAMETER SourceRoot
  Path to the comfyui-runpod checkout (folder containing .git).

.PARAMETER ShadowRoot
  Destination root (default E:\comfyui-runpod-shadow).
#>
param(
    [string] $SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string] $ShadowRoot = "E:\comfyui-runpod-shadow"
)

$ErrorActionPreference = "Stop"

function Invoke-RobocopyOk {
    param([string] $From, [string] $To)
    if (-not (Test-Path $From)) { return }
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    & robocopy $From $To /E /COPY:DAT /R:2 /W:5 /MT:16 | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed ($LASTEXITCODE): $From -> $To"
    }
}

New-Item -ItemType Directory -Path $ShadowRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ShadowRoot "workspace") -Force | Out-Null

$envFile = Join-Path $SourceRoot ".env"
if (Test-Path $envFile) {
    Copy-Item $envFile (Join-Path $ShadowRoot ".env") -Force
}

foreach ($t in @(".civitai_token", ".huggingface_token", ".hf_token")) {
    $sf = Join-Path $SourceRoot "workspace\$t"
    if (Test-Path $sf) {
        Copy-Item $sf (Join-Path $ShadowRoot "workspace\$t") -Force
    }
}

Invoke-RobocopyOk (Join-Path $SourceRoot "credentials") (Join-Path $ShadowRoot "credentials")
Invoke-RobocopyOk (Join-Path $SourceRoot "output") (Join-Path $ShadowRoot "output")
Invoke-RobocopyOk (Join-Path $SourceRoot "workspace\models") (Join-Path $ShadowRoot "workspace\models")
Invoke-RobocopyOk (Join-Path $SourceRoot "workspace\comfyui_user") (Join-Path $ShadowRoot "workspace\comfyui_user")
Invoke-RobocopyOk (Join-Path $SourceRoot "workspace\input") (Join-Path $ShadowRoot "workspace\input")
Invoke-RobocopyOk (Join-Path $SourceRoot "workspace\output") (Join-Path $ShadowRoot "workspace\output")

Write-Host "Shadow sync complete: $ShadowRoot"
