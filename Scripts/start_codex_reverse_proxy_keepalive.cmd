@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0keep_codex_reverse_proxy.ps1" -Alias "example-gpu-root-2224" -LocalPort 7897 -RemotePort 7897
