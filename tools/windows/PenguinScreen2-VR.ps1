# SPDX-FileCopyrightText: 2026 PenguinScreen2 contributors
# SPDX-License-Identifier: GPL-3.0
#
# Start PenguinScreen2 in VR on Windows (the Windows counterpart of
# launch-vr-session.sh). Normally run through PenguinScreen2-VR.bat:
#
#   PenguinScreen2-VR.bat [emulator arguments...]   play in the headset (adds --vr)
#   PenguinScreen2-VR.bat -Check                    report the OpenXR runtime and headset, then exit
#
# Any other arguments pass straight to the emulator, e.g. a game path, or
# --qhist-live "C:\path\to\folder" for the stereo depth histogram.

# A plain (non-advanced) param block on purpose: unknown arguments such as
# --qhist-live land in $args instead of failing parameter binding.
param([switch]$Check)
$EmuArgs = @($args)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$exe = Get-ChildItem -Path $here -Filter 'pcsx2-qt*.exe' -File | Sort-Object Name | Select-Object -First 1
if (-not $exe) {
	Write-Host "Can't find pcsx2-qt.exe next to this script ($here)." -ForegroundColor Red
	exit 1
}

function Get-ActiveRuntime {
	if ($env:XR_RUNTIME_JSON) {
		return [pscustomobject]@{ Path = $env:XR_RUNTIME_JSON; Source = 'XR_RUNTIME_JSON' }
	}
	try {
		$p = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Khronos\OpenXR\1' -Name ActiveRuntime -ErrorAction Stop).ActiveRuntime
	} catch {
		$p = $null
	}
	if ($p) {
		return [pscustomobject]@{ Path = $p; Source = 'registry' }
	}
	return $null
}

function Quote-Arg([string]$a) {
	if ($a -match '[\s"]') { return '"' + ($a -replace '"', '\"') + '"' }
	return $a
}

$rt = Get-ActiveRuntime
if (-not $rt) {
	Write-Host 'No OpenXR runtime is active, so VR cannot start.' -ForegroundColor Yellow
	Write-Host '  Virtual Desktop: Streamer app > Settings > OpenXR Runtime > VirtualDesktopXR'
	Write-Host '  Meta Quest Link: Meta Quest app > Settings > General > OpenXR Runtime > Set as active'
	Write-Host '  SteamVR:         SteamVR > Settings > OpenXR > Set SteamVR as OpenXR runtime'
	exit 1
}

$name = '(unreadable manifest)'
try {
	$manifest = Get-Content -Raw -LiteralPath $rt.Path | ConvertFrom-Json
	if ($manifest.runtime.name) { $name = $manifest.runtime.name }
} catch { }
Write-Host "OpenXR runtime: $name"
Write-Host "  manifest: $($rt.Path)  (from $($rt.Source))"
if ($name -eq 'VirtualDesktopXR') {
	Write-Host '  Connect the headset to the Virtual Desktop Streamer before starting a game.'
}

if ($Check) {
	$report = Join-Path $env:TEMP 'penguinscreen2-vr-info.txt'
	# The emulator is a GUI app: its report only reaches us through a redirected handle.
	Start-Process -FilePath $exe.FullName -ArgumentList '-vr-info' -RedirectStandardError $report -Wait -NoNewWindow
	Get-Content -LiteralPath $report
	exit 0
}

$all = @('--vr') + @($EmuArgs | Where-Object { $_ -ne $null -and $_ -ne '' })
$argLine = ($all | ForEach-Object { Quote-Arg $_ }) -join ' '
Start-Process -FilePath $exe.FullName -ArgumentList $argLine -WorkingDirectory $here
