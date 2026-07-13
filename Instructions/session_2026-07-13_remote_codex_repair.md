# 2026-07-13 远程 Codex 修复与验证记录

本文记录本轮围绕“让远程服务器上的 Codex 通过本机代理正常使用，并通过 VSCode Remote-SSH 连接”的排查、实现和验证过程。

## 背景

目标是在 Windows 本机上自动完成远程服务器接入：

1. 使用用户提供的 SSH 信息连接远程服务器。
2. 为每台远程主机生成独立 SSH 配置，支持不同主机、端口、用户、密码和别名。
3. 配置 SSH 反向端口转发，让远端服务器通过本机 VPN/代理访问外网。
4. 检查远端是否已安装 Codex；没有则自动安装。
5. 远端连接后先执行 `codex logout`，再使用本机设备码完成 `codex login --device-auth`。
6. 完成后自动打开 VSCode Remote-SSH 连接远程主机。

## 本轮新增和调整

### 多主机配置

脚本支持两种输入方式：

- 单台主机：`--ssh-command "ssh -p 2223 root@gpu-a.example.com"`
- 多台主机：`--remotes-file Instructions/remotes.example.json`

每台主机可拥有独立的：

- SSH 命令
- SSH 密码环境变量
- VSCode Remote-SSH 别名
- 远程打开目录

### 自动代理端口检测

启用 `--setup-codex` 时，如果未手动指定 `--remote-proxy-port`，脚本会自动检测本机可用 HTTP/HTTPS 代理端口。

检测来源包括：

- `HTTP_PROXY`
- `HTTPS_PROXY`
- `ALL_PROXY`
- 常见端口候选，例如 `7897`、`7890`、`7891`、`10809`、`1080`、`2080`、`8080`、`8888`、`20171`
- 用户通过 `--proxy-port-candidate` 追加的端口

检测成功后，脚本会自动追加反向端口映射：

```text
远端 127.0.0.1:代理端口 -> 本机 127.0.0.1:代理端口
```

### 远端 Codex 安装与代理配置

脚本会在远端执行以下操作：

1. 检查 `codex` 是否存在。
2. 不存在或不可运行时，优先通过 `https://chatgpt.com/codex/install.sh` 安装。
3. 如果 install.sh 失败，回退到 `npm install -g @openai/codex`。
4. 如远端 Node.js 版本过低，Ubuntu/Debian 系统会尝试安装 Node.js 20。
5. 写入远端代理环境：
   - `/etc/profile.d/codex_proxy.sh`
   - `/etc/environment`
   - `/root/.bashrc`
6. 安装 `/usr/local/bin/codex` wrapper，使远端执行 `codex` 时默认带上代理环境变量。

远端默认代理变量示例：

```bash
HTTP_PROXY=http://127.0.0.1:7897
HTTPS_PROXY=http://127.0.0.1:7897
NO_PROXY=localhost,127.0.0.1,::1
```

### 设备码登录流程

脚本支持启动本机设备码接收器：

```text
http://127.0.0.1:17860/device-login
```

同时通过 SSH 反向端口映射让远端也能访问该地址。远端 `codex login --device-auth` 输出登录 URL 和设备码后，脚本会转发回本机，本机自动打开浏览器完成授权。

默认流程变为：

1. 安装或检查远端 Codex。
2. 配置远端 Codex 代理环境。
3. 远端执行 `codex logout`。
4. 远端执行 `codex login --device-auth`。
5. 本机打开设备码授权页面。
6. 授权完成后打开 VSCode Remote-SSH。

### VSCode 打开时机

为了避免 VSCode 先连接但远端 Codex 还没配置好，脚本现在在 Codex 安装、代理配置、退出旧登录和设备码登录流程之后，再打开 VSCode Remote-SSH。

## 实际验证结果

本轮使用一台干净远程服务器进行验证。文档中不记录 SSH 密码。

### 本机 Codex CLI

本机 `codex` 曾被 WindowsApps 中的无权限路径抢先匹配，表现为：

```text
Access is denied
```

处理方式：

```powershell
npm install -g @openai/codex@0.144.3 --force --foreground-scripts
```

验证结果：

```text
codex-cli 0.144.3
```

本机 `codex doctor --summary --ascii --no-color` 结果：

```text
websocket connected (HTTP 101 Switching Protocols)
active provider endpoints are reachable over HTTP
16 ok | 1 idle | 1 notes | 1 warn | 0 fail degraded
```

### 远端 Codex

远端 `codex doctor --summary --ascii --no-color` 结果：

```text
Codex Doctor v0.144.3 · linux-x86_64
websocket connected (HTTP 101 Switching Protocols)
active provider endpoints are reachable over HTTP
16 ok | 1 idle | 1 notes | 1 warn | 0 fail degraded
```

远端真实模型调用验证：

```bash
printf %s "Reply with exactly: REMOTE_CODEX_OK" | timeout 120 codex exec --ephemeral --sandbox read-only --skip-git-repo-check --color never -
```

返回：

```text
REMOTE_CODEX_OK
```

结论：远端 Codex 的安装、登录、代理和模型调用均已验证可用。

## 关于 `codex_apps` MCP 报错

用户遇到的错误：

```text
MCP client for `codex_apps` timed out after 30 seconds
MCP startup incomplete (failed: codex_apps)
Falling back from WebSockets to HTTPS transport. request timed out
```

排查结论：

- `codex_apps` 是本机 Codex 桌面端的 app connector，不是远端服务器上的 Codex。
- 远端服务器上的 `codex mcp list` 显示没有配置 MCP server。
- 远端 `codex doctor` 和真实 `codex exec` 均已成功，说明远端 Codex 本体可用。
- 直接在本机 `~/.codex/config.toml` 中添加只有 `startup_timeout_sec` 的 `[mcp_servers.codex_apps]` 会导致 CLI 配置无效，已撤销。

说明：

如果当前已经打开的 Codex 桌面任务仍显示旧的 `codex_apps` 报错，需要重启 Codex 桌面端或新开任务，因为 MCP 服务是在任务启动时加载的。

## 本地测试

新增单元测试文件：

```text
Instructions/test_vscode_remote_codex_proxy.py
```

测试命令：

```powershell
python -m py_compile Instructions\vscode_remote_codex_proxy.py Instructions\test_vscode_remote_codex_proxy.py
python -m unittest Instructions.test_vscode_remote_codex_proxy
```

已覆盖：

- SSH 命令解析
- 多主机配置解析
- 端口映射规则解析
- 自动代理端口候选生成
- 设备码登录 GET/POST 接收
- 远端 Codex 登录信息提取

## 当前使用顺序

最终用户侧推荐使用顺序：

1. 启动本机 VPN/代理。
2. 准备远程 SSH 命令和密码环境变量。
3. 运行脚本并启用 `--setup-codex`、`--device-login-port`。
4. 脚本自动安装公钥并写入 SSH config。
5. 脚本自动检测本机代理端口并建立远端反向转发。
6. 脚本检查或安装远端 Codex。
7. 脚本配置远端 Codex 默认代理环境。
8. 脚本远端执行 `codex logout`。
9. 脚本触发设备码登录。
10. 用户在本机浏览器完成授权。
11. 脚本打开 VSCode Remote-SSH。
12. 在 VSCode 远程终端中直接使用 `codex`。

