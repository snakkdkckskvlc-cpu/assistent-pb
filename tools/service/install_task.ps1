# ================================================================
# Autostart for the server mode: the app must survive a reboot.
# ================================================================
# Registers a Scheduled Task that starts the backend at boot, before
# anyone logs in. Ollama installs its own Windows service; LanguageTool
# is started by the backend itself (infrastructure/languagetool.py).
#
# The TZ says "systemd", but the server runs Windows - Task Scheduler is
# the local equivalent. See docs/07-ops/install-server.md.
#
# Run as administrator:
#     .\tools\service\install_task.ps1
#     .\tools\service\install_task.ps1 -Remove
# ================================================================

param(
    [switch]$Remove,
    [int]$Port = 8000,
    # Account the app runs under. This is a SECURITY decision, not a detail:
    # document encryption (DPAPI) is bound to this account.
    #
    #   SYSTEM (default) - simplest, survives reboot, no password to manage.
    #     Downside: SYSTEM's DPAPI keys are reachable by ANY process running as
    #     SYSTEM or as a local administrator, so encryption stops protecting
    #     against an administrator of this very machine.
    #
    #   A dedicated low-privilege account (recommended) - keys belong to it
    #     alone. Pass -UserId "SERVER\assistentpb" and grant "Log on as a batch
    #     job". You will be asked for its password by Task Scheduler.
    #
    # Whatever you choose, DO NOT change it later: files encrypted under one
    # account are unreadable under another.
    [string]$UserId = "SYSTEM"
)

$ErrorActionPreference = "Stop"

$TaskName = "AssistentPB Server"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venvPython = Join-Path $root "venv\Scripts\python.exe"
$runner = Join-Path $root "scripts\run_server.py"

function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [X] $msg" -ForegroundColor Red; exit 1 }

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Fail "Requires administrator privileges." }

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Ok "Task removed: $TaskName"
    } else {
        Warn "Task not found: $TaskName"
    }
    exit 0
}

if (-not (Test-Path $venvPython)) { Fail "venv not found. Run bootstrap.ps1 first." }
if (-not (Test-Path $runner))     { Fail "scripts\run_server.py not found." }

# PYTHONPATH is passed through the working directory + the runner itself,
# which prepends the source folders to sys.path. Nothing else to set up.
$action = New-ScheduledTaskAction -Execute $venvPython `
    -Argument "`"$runner`" --port $Port" -WorkingDirectory $root

# At startup, not at logon: nobody may ever log in to a server, and the
# app has to answer anyway.
$trigger = New-ScheduledTaskTrigger -AtStartup

# See the -UserId comment at the top: this choice decides whose DPAPI keys
# encrypt the documents. See docs/05-quality/security.md.
if ($UserId -eq "SYSTEM") {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Warn "Running as SYSTEM: encryption will not protect documents from a local administrator."
    Warn "A dedicated account is safer: -UserId 'SERVER\assistentpb'"
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Password -RunLevel Limited
    Ok "Running as $UserId - Task Scheduler will ask for its password."
}

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Ok "Task registered: $TaskName (port $Port)"
Write-Host ""
Write-Host "  Start now:  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "  Status:     Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "  Remove:     .\tools\service\install_task.ps1 -Remove" -ForegroundColor Cyan
Write-Host ""
Warn "Encryption note: documents are encrypted for the account that runs the app."
Warn "If you change that account later, existing .enc files stop being readable."
