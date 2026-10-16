"""Windows toast notifications via PowerShell (no extra dependency needed)."""
from __future__ import annotations

import subprocess
import sys


def toast(title: str, message: str) -> bool:
    if sys.platform != "win32":
        return False
    ps = f"""
[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(6000, '{_esc(title)}', '{_esc(message)}', 'Info')
Start-Sleep -Seconds 6
$n.Dispose()
"""
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


def _esc(s: str) -> str:
    return s.replace("'", "''")[:200]
