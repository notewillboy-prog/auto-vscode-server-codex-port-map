@echo off
if "%~1"=="" (
  echo Usage: %~nx0 -Alias ^<SSH_ALIAS^> [-LocalPort ^<PORT^>] [-RemotePort ^<PORT^>] [-LogPath ^<PATH^>]
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0keep_codex_reverse_proxy.ps1" %*
