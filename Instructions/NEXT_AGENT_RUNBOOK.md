# Next-agent runbook: VS Code Remote + remote Codex

This runbook is for the next model or human agent opening this project.
If the user only provides an SSH command and a password, use this document to:

1. connect VS Code Remote-SSH to the server,
2. keep a reverse proxy port available for remote Codex,
3. verify remote Codex auth and provider configuration,
4. repair common Codex login/provider failures,
5. start remote Codex in full-access mode when requested.

Do not commit real passwords, API keys, access tokens, or `auth.json`.

## Inputs Expected From User

The user may provide only:

```text
ssh -p <PORT> <USER>@<HOST>
password: <PASSWORD>
```

Parse it as:

```text
HOST=<HOST>
USER=<USER>
PORT=<PORT>
REMOTE_PATH=/root        # use /root for root, otherwise /home/<USER>
ALIAS=codex-<USER>-<HOST>-<PORT> or a short stable alias
```

Do not place the password in a shell variable or command line. Let the script
prompt for it interactively during the one-time public-key installation.

Example:

```text
ssh -p <PORT> <USER>@<HOST>
```

means:

```text
HOST=<HOST>
USER=<USER>
PORT=<PORT>
REMOTE_PATH=/root-or-home-directory
```

## Local Prerequisites

From the project root on Windows:

```powershell
python -m pip install paramiko
Get-Command ssh,ssh-keygen,code -ErrorAction SilentlyContinue
```

If `code` is missing, ask the user to enable the VS Code command:

```text
Shell Command: Install 'code' command in PATH
```

## Detect The Local Proxy Port

The project is usually used to let remote Codex access the local machine's proxy.
Common local proxy ports are `7897`, `7890`, `7899`, `1080`, `10808`, `8080`, and `8118`.

On Windows, inspect listeners:

```powershell
netstat -ano
```

Look for something like:

```text
127.0.0.1:7897 LISTENING
```

Verify it can reach GitHub or OpenAI from the local machine:

```powershell
curl.exe -I -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://github.com
curl.exe -I -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://api.openai.com/v1/models
```

For `api.openai.com/v1/models`, `401 Unauthorized` is a good connectivity result.
It means the network path works and the request simply lacks auth.

## Connect VS Code Remote And Start Reverse Proxy

Run this project script from the project root:

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --host <HOST> `
  --user <USER> `
  --port <PORT> `
  --alias <ALIAS> `
  --remote-path <REMOTE_PATH> `
  --forward R:127.0.0.1:<LOCAL_PROXY_PORT>:127.0.0.1:<LOCAL_PROXY_PORT>
```

Enter the SSH password only at the hidden interactive prompt. The password is
used once to install the public key and is not written to SSH config or logs.

What the script does:

- generates or reuses `~/.ssh/codex_remote_ed25519`,
- installs its public key into remote `~/.ssh/authorized_keys`,
- writes a marked block into local `~/.ssh/config`,
- opens VS Code Remote-SSH with `code --folder-uri`,
- starts an `ssh -N -T -R ...` reverse-forward process.

If OpenSSH fails with host-key verification, accept the host key once:

```powershell
ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 <ALIAS> hostname
```

Then restart the script if needed.

## Persistent Windows Keepalive For The Reverse Proxy

For a frequently used server, install the local keepalive script as a Windows
logon task. This avoids manually recreating the remote `127.0.0.1:<PORT>` proxy
each time Windows or VS Code restarts.

The keepalive script is:

```text
Scripts/keep_codex_reverse_proxy.ps1
```

It continuously runs:

```text
ssh -N -T -R 127.0.0.1:<PORT>:127.0.0.1:<PORT> <ALIAS>
```

with `ExitOnForwardFailure`, `ServerAliveInterval`, `ServerAliveCountMax`, and
`TCPKeepAlive`. It uses a mutex and an alias/port-specific log. It never kills
an existing remote listener automatically; choose another remote port when a
different tunnel already owns the requested port.

Register a current-user logon task from Windows PowerShell:

```powershell
$ProjectRoot = "<PROJECT_ROOT>"
$ScriptPath = Join-Path $ProjectRoot "Scripts\keep_codex_reverse_proxy.ps1"
$TaskName = "CodexReverseProxy-<SSH_ALIAS>-<REMOTE_PORT>"

$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`" -Alias `"<SSH_ALIAS>`" -LocalPort <LOCAL_PORT> -RemotePort <REMOTE_PORT>"

$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Keep SSH reverse proxy alive for remote Codex" `
  -Force

Start-ScheduledTask -TaskName $TaskName
```

If Task Scheduler returns `Access is denied`, use the current user's Windows
Startup folder instead:

```powershell
$Startup = [Environment]::GetFolderPath("Startup")
$Target = "<PROJECT_ROOT>\Scripts\start_codex_reverse_proxy_keepalive.cmd"
$Shortcut = Join-Path $Startup "CodexReverseProxy-<SSH_ALIAS>-<REMOTE_PORT>.lnk"

$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Shortcut)
$Link.TargetPath = $Target
$Link.Arguments = '-Alias "<SSH_ALIAS>" -LocalPort <LOCAL_PORT> -RemotePort <REMOTE_PORT>'
$Link.WorkingDirectory = Split-Path -Parent $Target
$Link.WindowStyle = 7
$Link.Description = "Keep SSH reverse proxy alive for remote Codex"
$Link.Save()
```

Check task state:

```powershell
Get-ScheduledTask -TaskName "CodexReverseProxy-<SSH_ALIAS>-<REMOTE_PORT>"
Get-ScheduledTaskInfo -TaskName "CodexReverseProxy-<SSH_ALIAS>-<REMOTE_PORT>"
```

Check logs:

```powershell
Get-Content "$env:LOCALAPPDATA\CodexReverseProxy\keepalive-<SSH_ALIAS>-<REMOTE_PORT>.log" -Tail 80
```

Remove the task:

```powershell
Unregister-ScheduledTask -TaskName "CodexReverseProxy-<SSH_ALIAS>-<REMOTE_PORT>" -Confirm:$false
```

For multiple servers, repeat this setup with one SSH alias, remote port, task
name, mutex, and log per server. Different remote servers may use the same port;
multiple tunnels into the same remote server must use different remote ports.

## Verify The Remote Connection

From Windows:

```powershell
ssh -o BatchMode=yes <ALIAS> hostname
ssh -o BatchMode=yes <ALIAS> "python3 --version || python --version"
```

Verify the remote side can see the forwarded local proxy:

```powershell
ssh -o BatchMode=yes <ALIAS> "python3 - <<'PY'
import socket
s = socket.create_connection(('127.0.0.1', <LOCAL_PROXY_PORT>), timeout=5)
s.close()
print('remote proxy port open')
PY"
```

Verify remote traffic can use it:

```powershell
ssh -o BatchMode=yes <ALIAS> "HTTPS_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT> HTTP_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT> python3 - <<'PY'
import urllib.request
r = urllib.request.urlopen('https://github.com', timeout=15)
print('remote via forwarded proxy ok', r.status)
PY"
```

## Remote Codex Provider Configuration

If the user wants remote Codex to follow the provider currently selected in
local CC Switch, do not copy the upstream API key to the server. Reverse-forward
the local CC Switch Codex route and use a placeholder auth value remotely. See
[CC_SWITCH_REMOTE_CODEX.md](CC_SWITCH_REMOTE_CODEX.md) for the verified setup,
security boundary, server-reboot recovery, Windows `10048` handling,
multi-server setup, Codex `0.147.0` compatibility note, and layered end-to-end
checks. In CC Switch mode, always start components in this order: CC Switch,
SSH keepalive, then VS Code Remote-SSH.

On the remote server, check:

```bash
codex login status
grep -n "model_provider\|model =" ~/.codex/config.toml
codex doctor --summary
```

The desired state for official ChatGPT/OpenAI account use:

```text
Logged in using ChatGPT
model_provider = "openai"
```

If `~/.codex/config.toml` contains:

```toml
model_provider = "custom-openai"
base_url = "https://custom-provider.example/v1"
model = "gpt-5.5"
```

then Codex will keep calling the custom distributor URL and may fail with errors like:

```text
Unexpected status 503 Service Unavailable:
No available channel for model gpt-5.5 under group vip (distributor)
```

Repair it:

```bash
cp ~/.codex/config.toml ~/.codex/config.toml.bak.$(date +%Y%m%d-%H%M%S)

python3 - <<'PY'
from pathlib import Path

p = Path.home() / ".codex" / "config.toml"
lines = p.read_text().splitlines()
out = []

for line in lines:
    if line.startswith("model_provider ="):
        out.append('model_provider = "openai"')
    elif line.startswith("model ="):
        continue
    elif line.startswith("model_reasoning_effort ="):
        continue
    else:
        out.append(line)

p.write_text("\n".join(out) + "\n")
PY
```

It is okay if an unused `[model_providers.custom-openai]` block remains,
as long as the top-level `model_provider` is `openai`.

## Remote Codex Login

If status says API-key login:

```text
Logged in using an API key
```

and the user wants account login, run:

```bash
codex logout
codex login --device-auth
```

Expected success status:

```bash
codex login status
# Logged in using ChatGPT
```

If `codex login --device-auth` hangs in VS Code terminal, kill it from another shell:

```bash
pkill -f "codex login" || true
```

Then retry with a pseudo-terminal wrapper:

```bash
script -q -c "codex login --device-auth" /dev/null
```

If device login fails with:

```text
device code request failed with status 403 Forbidden
```

the server IP is likely blocked by `auth.openai.com`.
Try login through the forwarded local proxy:

```bash
export HTTP_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
export HTTPS_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
export ALL_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
codex login --device-auth
```

If browser login reaches a page saying:

```text
Sign-in could not be completed
Token exchange failed: error sending request for url
https://auth.openai.com/oauth/token
```

retry in a clean browser or incognito window. Also test:

```bash
curl -I https://auth.openai.com/oauth/token
curl -I -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://auth.openai.com/oauth/token
```

Expected results are usually `403`, `405`, or another quick HTTP response.
A timeout means the path is not usable.

Do not copy local `auth.json`, browser tokens, or API keys to a remote server as
a troubleshooting shortcut. Use the official login flow, or use the CC Switch
loopback method documented above when the user explicitly selects it.

## Connectivity Diagnostics

If `codex doctor --summary` says:

```text
network no proxy env vars
reachability one or more required provider endpoints are unreachable
```

that often only means the current shell did not export proxy variables.

Use:

```bash
export HTTP_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
export HTTPS_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
export ALL_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>

codex doctor --summary
```

If `websocket timed out` appears but HTTPS reachability works, Codex may still work via HTTPS fallback.

If OpenAI still times out through the reverse proxy, test from both local and remote:

```powershell
curl.exe -I -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://api.openai.com/v1/models
```

```bash
curl -I -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://api.openai.com/v1/models
```

Local `401 Unauthorized` plus remote timeout means the reverse-forwarded proxy path is failing for that host.
Restart the project script and re-test the `ssh -N -R` process.

If restarting `ssh -R` fails with:

```text
Error: remote port forwarding failed for listen port <LOCAL_PROXY_PORT>
```

the remote port may be occupied by a stale `sshd` remote-forward listener.
Check and clear it on the remote server:

```bash
lsof -iTCP:<LOCAL_PROXY_PORT> -sTCP:LISTEN -nP 2>/dev/null || true
```

If the listener is an old `sshd` process and proxy requests time out, kill only
that stale listener PID:

```bash
kill <STALE_SSHD_PID>
```

Then recreate the reverse forward from Windows:

```powershell
Start-Process -FilePath ssh -ArgumentList @(
  '-N','-T',
  '-o','ExitOnForwardFailure=yes',
  '-o','ServerAliveInterval=30',
  '-o','ServerAliveCountMax=3',
  '-R','127.0.0.1:<LOCAL_PROXY_PORT>:127.0.0.1:<LOCAL_PROXY_PORT>',
  '<ALIAS>'
) -WindowStyle Hidden
```

Successful probes after repair:

```bash
curl -I --max-time 20 -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://api.openai.com/v1/models
# expected: 401 Unauthorized

curl -I --max-time 20 -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://chatgpt.com/backend-api/codex/responses
# expected for HEAD probe: 405 Method Not Allowed, allow: POST
```

## `codex_apps` MCP Startup Failure

Remote Codex may print:

```text
MCP client for `codex_apps` failed to start
MCP startup failed: handshaking with MCP server failed
error sending request for url (https://chatgpt.com/backend-api/ps/mcp)
MCP startup incomplete (failed: codex_apps)
```

This is usually not the core OpenAI model endpoint. It is Codex trying to start
the built-in ChatGPT Apps MCP client. Diagnose it separately from `api.openai.com`.

First check whether the remote shell has proxy variables:

```bash
env | grep -Ei 'HTTP_PROXY|HTTPS_PROXY|ALL_PROXY'
```

Then test direct and proxied access:

```bash
curl -I --max-time 15 https://chatgpt.com/backend-api/ps/mcp
curl -I --max-time 20 -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://chatgpt.com/backend-api/ps/mcp
curl -I --max-time 20 -x http://127.0.0.1:<LOCAL_PROXY_PORT> https://api.openai.com/v1/models
```

Interpretation:

- direct `chatgpt.com` timeout: server egress cannot reach ChatGPT directly;
- proxied `127.0.0.1:<LOCAL_PROXY_PORT>` connection refused: the SSH reverse-forward is down;
- proxied `api.openai.com/v1/models` returns `401`: OpenAI API path is reachable;
- proxied ChatGPT Apps endpoint returns `451` or another quick non-2xx response:
  the Apps MCP endpoint is reachable but unavailable for that route/account/region.

If the user only needs remote Codex coding, disable Apps for the run:

```bash
export HTTP_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
export HTTPS_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
export ALL_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>

codex --disable apps
```

With search:

```bash
codex --disable apps --search
```

To persistently disable the Apps feature on that remote machine:

```bash
codex features disable apps
```

Re-enable later with:

```bash
codex features enable apps
```

Only keep `apps` enabled if the user specifically needs ChatGPT Apps/connectors
inside remote Codex and the `chatgpt.com/backend-api/ps/mcp` route works.

## Clean Process Checklist

When done, report these facts to the user:

```text
VS Code Remote-SSH opened: yes/no
SSH alias: <ALIAS>
Reverse proxy: remote 127.0.0.1:<PORT> -> local 127.0.0.1:<PORT>
Remote proxy test: ok/fail
Codex login: ChatGPT/API key/not logged in
Codex provider: openai/custom-openai
Codex doctor: ok or remaining warnings
```

Do not include passwords or tokens in the report.
