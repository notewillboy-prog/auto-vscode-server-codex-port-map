# 让远程 Codex 使用本机 CC Switch

本文记录一种已经端到端验证的部署方式：CC Switch 和真实上游 API
凭据只保留在 Windows 本机；通过 SSH 反向端口转发，让远程服务器上的
Codex CLI 访问本机 CC Switch 的 Codex 路由。

该方式与“把本机 HTTP/Clash 代理转发给远程服务器”不同：转发的是 CC
Switch 的 OpenAI Responses 兼容路由本身。因此远程服务器不需要保存真实
上游 API Key，也不需要知道当前由 CC Switch 选中了哪个供应商。

## 数据流

```text
远程 Codex
  -> http://127.0.0.1:<REMOTE_ROUTE_PORT>/v1
  -> SSH RemoteForward
  -> Windows 本机 127.0.0.1:<LOCAL_CC_SWITCH_PORT>
  -> CC Switch 当前启用的 Codex 供应商
```

本文示例默认 CC Switch 的本地 Codex 路由是 `127.0.0.1:15721`。端口以
CC Switch 当前界面显示为准；不要假定所有安装都使用同一个端口。

## 安全边界

- 不读取、复制或记录 CC Switch 中保存的真实 API Key。
- 不把真实 API Key 写入 SSH 配置、项目文件、命令历史或远程服务器。
- 远程 `auth.json` 只放一个非敏感占位值，用来满足 Codex CLI 的认证字段要求。
- 远程监听地址保持 `127.0.0.1`，不要改成 `0.0.0.0`。
- 使用 `ExitOnForwardFailure=yes`，防止转发绑定失败后仍误以为链路有效。
- 日志和验证输出只记录状态码、版本或固定测试文本，不打印响应头中的敏感字段。

## 1. 确认本机 CC Switch 路由

在 CC Switch 的 Codex 页面确认本地路由已开启，并记下监听端口。可以在
Windows PowerShell 中只检查监听与非敏感响应：

```powershell
$CcSwitchPort = 15721
Get-NetTCPConnection -State Listen -LocalPort $CcSwitchPort
Invoke-WebRequest `
  -Uri "http://127.0.0.1:$CcSwitchPort/v1/models" `
  -UseBasicParsing `
  -TimeoutSec 10 |
  Select-Object StatusCode
```

得到 HTTP `200` 即可。不要在排查过程中打开或输出 CC Switch 的 API Key 字段。

## 2. 准备 SSH 别名和公钥登录

建议每台服务器使用独立别名，例如：

```sshconfig
Host codex-gpu-a
    HostName <SSH_HOST>
    User <SSH_USER>
    Port <SSH_PORT>
    IdentityFile C:\Users\<WINDOWS_USER>\.ssh\codex_remote_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

先验证免交互登录：

```powershell
ssh -o BatchMode=yes codex-gpu-a hostname
```

长期保活必须使用公钥认证，不能把 SSH 密码写入保活脚本。

## 3. 建立 CC Switch 反向转发

一次性启动：

```powershell
$Alias = "codex-gpu-a"
$LocalCcSwitchPort = 15721
$RemoteRoutePort = 15721

ssh -N -T `
  -o ExitOnForwardFailure=yes `
  -o BatchMode=yes `
  -o ConnectTimeout=10 `
  -o ServerAliveInterval=30 `
  -o ServerAliveCountMax=3 `
  -o TCPKeepAlive=yes `
  -R "127.0.0.1:${RemoteRoutePort}:127.0.0.1:${LocalCcSwitchPort}" `
  $Alias
```

也可以复用仓库中的保活脚本：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\Scripts\keep_codex_reverse_proxy.ps1 `
  -Alias codex-gpu-a `
  -LocalPort 15721 `
  -RemotePort 15721 `
  -LogPath "$env:LOCALAPPDATA\CodexReverseProxy\keepalive-codex-gpu-a-15721.log"
```

保活脚本会自动重连，并始终只在远程回环地址监听。相同 SSH 别名与远端
端口只允许一个实例运行；互斥锁会阻止重复启动。脚本不会自动结束远端的
其他 `sshd` 监听器，避免误断另一台电脑或另一条隧道。

如果远程端口已被其他转发占用，请换一个远程端口，例如远程 `15722`
转到本机 `15721`：

```text
-R 127.0.0.1:15722:127.0.0.1:15721
```

后续远程配置中的 Base URL 也要相应改成 `127.0.0.1:15722`。

### 多服务器并行

每台服务器都应使用独立 SSH 别名、互斥锁和日志。不同服务器可以复用相同
远端端口；只有同一台服务器上的多条隧道才必须使用不同远端端口。例如，
两台独立服务器都可以转发到同一个本机 CC Switch 端口：

```powershell
.\Scripts\start_codex_reverse_proxy_keepalive.cmd `
  -Alias codex-gpu-a -LocalPort 15721 -RemotePort 15721

.\Scripts\start_codex_reverse_proxy_keepalive.cmd `
  -Alias codex-gpu-b -LocalPort 15721 -RemotePort 15721
```

两台服务器的 Codex Base URL 都可以使用
`http://127.0.0.1:15721/v1`。默认日志名包含 SSH 别名和远端端口，便于分别
排查。若同一台服务器需要第二条隧道，再把第二条的远端端口改为 `15722`，
并同步修改该隧道使用者的 Base URL。

## 4. 在远程验证转发

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:15721/v1/models
```

期望输出：

```text
200
```

这只能证明 CC Switch 路由可达；最终仍要用 `codex exec` 验证一次真实
Responses 调用。

## 5. 安装 Codex CLI

优先按官方 OpenAI 文档使用 Linux standalone installer 或 npm。网络受限的
服务器也可以从可信 npm 镜像取得 `@openai/codex` 与对应 Linux 平台包，但
必须验证压缩包完整性，再安装到非系统盘。

安装后至少检查：

```bash
codex --version
codex doctor --summary --no-color --ascii
```

若 Codex 安装在数据盘，可以只在标准命令目录创建符号链接：

```bash
ln -sfn /data/tools/codex/bin/codex /usr/local/bin/codex
```

实际二进制仍留在数据盘。

## 6. 配置远程 Codex provider

先备份既有配置：

```bash
mkdir -p ~/.codex
cp -p ~/.codex/config.toml ~/.codex/config.toml.bak 2>/dev/null || true
```

`~/.codex/config.toml`：

```toml
model_provider = "cc-switch-local"
model = "gpt-5.6-terra"
model_reasoning_effort = "xhigh"

[model_providers.cc-switch-local]
name = "CC Switch local route"
base_url = "http://127.0.0.1:15721/v1"
wire_api = "responses"
requires_openai_auth = true
```

模型名和 reasoning effort 应与 CC Switch 当前供应商实际支持范围一致。

`~/.codex/auth.json` 只写非敏感占位值：

```json
{"OPENAI_API_KEY":"cc-switch-local-route"}
```

收紧权限：

```bash
chmod 700 ~/.codex
chmod 600 ~/.codex/config.toml ~/.codex/auth.json
```

注意：不要把 CC Switch 中的真实 Key 写进此文件。CC Switch 的本地路由会在
本机完成上游认证。

### Codex 版本兼容性

Codex CLI `0.147.0` 的 `--strict-config` 不接受旧字段：

```toml
disable_response_storage = true
```

升级到该版本时应先备份配置，再移除此字段。其他版本应以
`codex exec --strict-config ...` 和 `codex doctor` 的实际结果为准，不要盲目
复用旧配置模板。

## 7. 验证配置和真实模型调用

```bash
codex login status
codex doctor --summary --no-color --ascii
```

`login status` 会把占位值脱敏显示为 API-key 登录，这是预期行为。

用临时、只读会话做端到端验证：

```bash
printf '%s\n' 'Reply with exactly: CODEX_CC_SWITCH_OK' | \
  codex exec \
    --strict-config \
    --ephemeral \
    --skip-git-repo-check \
    --ignore-rules \
    --sandbox read-only \
    --color never \
    -C /tmp \
    -
```

期望最终回复：

```text
CODEX_CC_SWITCH_OK
```

只有这一步成功，才表示“远程 Codex → SSH 反向转发 → 本机 CC Switch →
当前上游供应商”的完整 Responses 链路可用。

## 8. 正确启动顺序与重启恢复

固定使用以下顺序：

```text
启动 CC Switch 本地路由
-> 确认 Windows 本机 /v1/models 返回 200
-> 启动 SSH 反向转发保活
-> 确认远程 /v1/models 返回 200
-> 打开 VS Code Remote-SSH
-> 运行 codex doctor 和真实 Responses 测试
```

服务器重启会终止远端 `sshd` 转发监听，但不会替 Windows 自动恢复 CC
Switch 路由。恢复时先检查本机 CC Switch，再检查保活进程和独立日志，最后
逐层复测。不要因为远端端口短暂存在就跳过本机路由检查。

### Windows 10048：VS Code 抢占 CC Switch 端口

若 CC Switch 报“地址绑定失败”或 Windows `os error 10048`，先确定监听者：

```powershell
$CcSwitchPort = 15721
Get-NetTCPConnection -State Listen -LocalPort $CcSwitchPort |
  Select-Object LocalAddress,LocalPort,OwningProcess
Get-Process -Id <OWNING_PROCESS_ID>
```

如果监听者是 `Code.exe`，通常是 VS Code Remote-SSH 的“端口/Ports”面板
自动恢复了远端端口，并映射到相同本机端口。先在该面板停止对应转发；若
VS Code 立即重新创建监听，关闭相关 Remote-SSH 窗口。确认端口释放后再启动
CC Switch。不要结束未知进程，也不要在 CC Switch 尚未监听时启动保活。

### 克隆服务器：先检查，再复用 Codex

克隆服务器可能已经保留数据盘上的 Codex、`/usr/local/bin/codex` 链接和
`~/.codex` 配置。先做只读检查：

```bash
command -v codex || true
codex --version || true
test -f ~/.codex/config.toml && stat -c '%a %n' ~/.codex/config.toml
test -f ~/.codex/auth.json && stat -c '%a %n' ~/.codex/auth.json
```

如果版本、链接和配置均正常，只需为新服务器建立独立 SSH 别名与反向转发，
无需重复下载。不要输出 `auth.json` 内容；配置文件权限应为 `600`。

## 9. 分层验收

按以下三层验收，不能用前一层代替后一层：

1. 本机与远端分别请求 `/v1/models`，确认 CC Switch 路由和 SSH 隧道可达。
2. 运行 `codex doctor --summary --no-color --ascii`，确认 Codex 安装、配置、
   认证字段与 provider endpoint 均正常。
3. 运行第 7 节的临时只读 `codex exec`，确认真实 Responses 请求返回固定文本。

只有三层全部成功，才能报告远程 Codex 已可用。

## 10. 常见问题

### `/v1/models` 返回 200，但 Codex 调用失败

检查：

```bash
codex doctor --summary --no-color --ascii
codex exec --strict-config --ephemeral --sandbox read-only \
  --skip-git-repo-check 'Reply with OK'
```

常见原因是 `wire_api` 错误、模型名不受当前供应商支持，或配置中残留旧字段。

### 远程端口突然不可用

在 Windows 检查：

```powershell
Get-Content "$env:LOCALAPPDATA\CodexReverseProxy\keepalive-<SSH_ALIAS>-<REMOTE_PORT>.log" -Tail 80
Get-Process ssh,powershell -ErrorAction SilentlyContinue
```

同时检查 CC Switch 本机路由仍在监听。SSH 转发存活并不保证 CC Switch 本身
仍在运行。

### Codex 提示 bubblewrap 不在 PATH

新版 standalone 包可能自动使用 bundled bubblewrap。若 `codex doctor` 的
sandbox 检查正常且实际只读运行成功，该提示不阻塞使用；否则按 Codex 官方
sandbox prerequisites 安装系统 bubblewrap。

### 本机切换 CC Switch 供应商

反向转发和远程配置不需要改变。CC Switch 在本机热切换当前 Codex 供应商后，
远程下一次请求会走新的上游。新供应商必须支持配置中的模型名和 Responses
格式；切换后建议重新运行最小端到端验证。

## 已验证基线

以下组合在 2026-08-13 做过端到端验证：

- Windows 本机 CC Switch Codex 路由：`127.0.0.1:15721`
- SSH 反向转发：远程 `127.0.0.1:15721` 到本机同端口
- 远程 Codex CLI：`0.147.0`，Linux x86_64
- provider wire API：`responses`
- 模型：`gpt-5.6-terra`
- `codex doctor`：16 ok、0 fail
- 临时只读调用返回：`CODEX_CC_SWITCH_OK`

该基线只说明链路与配置模板已经验证，不代表其他供应商、模型或 Codex 版本
无需重新验证。
