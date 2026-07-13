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
PASSWORD=<PASSWORD>
REMOTE_PATH=/root        # use /root for root, otherwise /home/<USER>
ALIAS=codex-<USER>-<HOST>-<PORT> or a short stable alias
```

Example:

```text
ssh -p 2224 root@gpu-b.example.com
```

means:

```text
HOST=gpu-b.example.com
USER=root
PORT=2224
REMOTE_PATH=/root
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
  --forward R:127.0.0.1:<LOCAL_PROXY_PORT>:127.0.0.1:<LOCAL_PROXY_PORT> `
  --password '<PASSWORD>'
```

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
`TCPKeepAlive`. If the remote port is occupied by a stale `sshd` listener, it
kills only that stale listener and retries.

Register a current-user logon task from Windows PowerShell:

```powershell
$ProjectRoot = "D:\paper\login\auto-vscode-server-codex-port-map"
$ScriptPath = Join-Path $ProjectRoot "Scripts\keep_codex_reverse_proxy.ps1"
$TaskName = "CodexReverseProxy-example-gpu-root-2224-7897"

$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`" -Alias `"example-gpu-root-2224`" -LocalPort 7897 -RemotePort 7897"

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
$Target = "D:\paper\login\auto-vscode-server-codex-port-map\Scripts\start_codex_reverse_proxy_keepalive.cmd"
$Shortcut = Join-Path $Startup "CodexReverseProxy-example-gpu-root-2224-7897.lnk"

$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Shortcut)
$Link.TargetPath = $Target
$Link.WorkingDirectory = Split-Path -Parent $Target
$Link.WindowStyle = 7
$Link.Description = "Keep SSH reverse proxy alive for remote Codex"
$Link.Save()
```

Check task state:

```powershell
Get-ScheduledTask -TaskName "CodexReverseProxy-example-gpu-root-2224-7897"
Get-ScheduledTaskInfo -TaskName "CodexReverseProxy-example-gpu-root-2224-7897"
```

Check logs:

```powershell
Get-Content "$env:LOCALAPPDATA\CodexReverseProxy\keepalive.log" -Tail 80
```

Remove the task:

```powershell
Unregister-ScheduledTask -TaskName "CodexReverseProxy-example-gpu-root-2224-7897" -Confirm:$false
```

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
base_url = "http://8.153.202.222:3001/v1"
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

Fallback: if the user's local Codex is already logged in, copy local
`%USERPROFILE%\.codex\auth.json` to remote `~/.codex/auth.json`.
Treat this file as a secret.

```powershell
scp -P <PORT> $env:USERPROFILE\.codex\auth.json <USER>@<HOST>:~/.codex/auth.json
```

Then on remote:

```bash
chmod 600 ~/.codex/auth.json
codex login status
```

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

codex --disable apps --dangerously-bypass-approvals-and-sandbox
```

With search:

```bash
codex --disable apps --dangerously-bypass-approvals-and-sandbox --search
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

## Start Remote Codex With Maximum Permissions

Only do this when the user explicitly asks for full access mode.
It skips approval prompts and sandboxing.

On the remote server:

```bash
export HTTP_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
export HTTPS_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>
export ALL_PROXY=http://127.0.0.1:<LOCAL_PROXY_PORT>

codex --dangerously-bypass-approvals-and-sandbox
```

With web search:

```bash
codex --dangerously-bypass-approvals-and-sandbox --search
```

Less aggressive alternative:

```bash
codex -s danger-full-access -a never
```

## Remote LaSOT Batch Dataset Workflow

For the current remote server, large writable storage is:

```text
/root/autodl-tmp
```

Confirmed working paths:

```bash
export LASOT_ROOT=/root/autodl-tmp/LaSOT/LaSOTBenchmark
export LASOT_UPLOAD=/root/autodl-tmp/lasot_upload
mkdir -p /root/autodl-tmp/LaSOT /root/autodl-tmp/lasot_upload
```

Do not use `/root/autodl-pub` for this workflow; it is read-only in the current
environment. Current free space observed on `/root/autodl-tmp` was about `27G`,
so local LaSOT tar batches should be kept around `3G` to `5G`.

Never extract the full LaSOT dataset on the remote server. The required loop is:

```text
upload one tar batch
-> verify sha256
-> extract into /root/autodl-tmp/LaSOT
-> delete uploaded tar and checksum
-> run work on this batch only
-> delete processed category/sequence
-> upload next batch
```

Remote helper scripts were created and syntax-checked under:

```text
/root/x2sam/X2SAM/scripts/lasot_ingest_batch.sh
/root/x2sam/X2SAM/scripts/lasot_cleanup_batch.sh
/root/x2sam/X2SAM/scripts/lasot_remote_notes.md
```

The ingest script:

- accepts only a file name inside `LASOT_UPLOAD`, such as `part_001.tar`;
- requires `part_001.tar.sha256` next to the archive;
- runs `sha256sum -c`;
- rejects unsafe tar paths, including absolute paths and `..`;
- extracts to `/root/autodl-tmp/LaSOT`;
- deletes the uploaded tar and `.sha256`;
- prints disk space and warns if free space drops below `MIN_FREE_GB`, default `8`.

Use one batch:

```bash
cd /root/x2sam/X2SAM

export LASOT_ROOT=/root/autodl-tmp/LaSOT/LaSOTBenchmark
export LASOT_UPLOAD=/root/autodl-tmp/lasot_upload

scripts/lasot_ingest_batch.sh part_001.tar
```

The project/data loader should point at:

```text
/root/autodl-tmp/LaSOT/LaSOTBenchmark
```

After the current batch is processed, remove only the completed categories or
sequences:

```bash
cd /root/x2sam/X2SAM

scripts/lasot_cleanup_batch.sh airplane
scripts/lasot_cleanup_batch.sh airplane/airplane-1
```

Manual fallback for a batch:

```bash
cd /root/autodl-tmp/lasot_upload
sha256sum -c part_001.tar.sha256
tar -xf part_001.tar -C /root/autodl-tmp/LaSOT
rm part_001.tar part_001.tar.sha256
df -h /root/autodl-tmp
```

Before and after each batch, check:

```bash
df -h /root/autodl-tmp
find /root/autodl-tmp/lasot_upload -maxdepth 1 -type f -printf '%f\n'
find /root/autodl-tmp/LaSOT -maxdepth 3 -type d | sort | head -50
```

The test chain has already been run once with a tiny tar:

```text
upload directory -> checksum verification -> extraction -> archive deletion
-> processed-data cleanup
```

The remote project path `/root/x2sam/X2SAM` was observed not to be a git
repository, so do not rely on remote `git status` there.

## Remote LaSOT SOT Base Evaluation

The remote X2SAM project has LaSOT SOT evaluation helpers:

```text
/root/x2sam/X2SAM/scripts/eval_lasot_sot.py
/root/x2sam/X2SAM/scripts/run_lasot_sot_eval.sh
/root/x2sam/X2SAM/scripts/lasot_remote_notes.md
```

Observed checks:

```bash
cd /root/x2sam/X2SAM
python3 -m py_compile scripts/eval_lasot_sot.py
bash -n scripts/run_lasot_sot_eval.sh
```

The remote currently cannot produce a real LaSOT base run until at least one
LaSOT batch is uploaded and ingested. Empty dataset startup fails with:

```text
No LaSOT test sequences found under /root/autodl-tmp/LaSOT/LaSOTBenchmark
```

First upload and ingest a batch that contains at least one test split sequence.
For LaSOT, the current convention is to use sequence names ending in an even
number as test examples, such as:

```text
airplane/airplane-2
airplane/airplane-4
```

After upload and ingest:

```bash
cd /root/x2sam/X2SAM

export LASOT_ROOT=/root/autodl-tmp/LaSOT/LaSOTBenchmark
export LASOT_UPLOAD=/root/autodl-tmp/lasot_upload

SUBSET=test MAX_SEQUENCES=1 scripts/run_lasot_sot_eval.sh
```

Results are written under:

```text
/root/autodl-tmp/x2sam/lasot_sot_base/...
```

The local Python metrics summary is:

```text
summary.json
```

The LaSOT-style per-sequence tracking output is:

```text
official_results/<tracker_name>/<sequence>.txt
```

The official LaSOT evaluation toolkit is MATLAB-based rather than an online
GOT-10k-style server. Official reference:

```text
https://github.com/HengLan/LaSOT_Evaluation_Toolkit
```

That toolkit expects tracking results under `tracking_results/` and runs
`run_tracker_performance_evaluation.m`. The current remote environment has no
MATLAB, no Octave, and no local LaSOT toolkit, so the practical remote strategy
is:

```text
generate LaSOT-style per-sequence result .txt files
-> compute base metrics locally in Python
-> keep official-format outputs for later toolkit evaluation elsewhere
```

After a batch is processed, immediately remove the completed category or
sequence with `scripts/lasot_cleanup_batch.sh` to keep `/root/autodl-tmp` below
space pressure.

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
Full-access Codex started: yes/no
```

Do not include passwords or tokens in the report.
