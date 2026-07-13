# 功能更新说明

本文用于说明当前项目相较初始 GitHub 版本新增的功能。当前仅整理为待审阅版本，尚未推送到 GitHub。

## 初始版本能力

初始版本主要提供一个基础脚本，用于：

1. 使用 SSH 密码连接远程服务器。
2. 安装本机 SSH 公钥到远端。
3. 写入本机 `~/.ssh/config`。
4. 启动 VSCode Remote-SSH。
5. 支持基础 SSH 端口映射，让远端访问本机端口。

## 当前新增能力

### 1. 支持完整 SSH 命令输入

新增 `--ssh-command`：

```powershell
--ssh-command "ssh -p 2223 root@gpu-a.example.com"
```

脚本会自动解析：

- 用户名
- 主机名
- SSH 端口

适合每次服务器地址、端口、用户都不同的云服务器场景。

### 2. 支持多远程主机批量配置

新增 `--remotes-file`，可通过 JSON 管理多台远程主机：

```json
{
  "remotes": [
    {
      "ssh_command": "ssh -p 2222 root@gpu-a.example.com",
      "alias": "example-gpu-codex-test-2222",
      "password_env": "EXAMPLE_GPU_PASSWORD",
      "remote_path": "/root"
    }
  ]
}
```

每台主机可独立设置别名、密码环境变量和 VSCode 打开目录。

### 3. 支持密码从环境变量读取

新增 `--password-env`，避免把 SSH 密码写入命令历史：

```powershell
$env:REMOTE_SSH_PASSWORD="你的SSH密码"
--password-env REMOTE_SSH_PASSWORD
```

### 4. 自动检测本机 VPN/代理端口

新增自动代理检测逻辑。

脚本会检测环境变量和常见代理端口，例如：

- `7897`
- `7890`
- `7891`
- `10809`
- `1080`
- `2080`
- `8080`
- `8888`
- `20171`

检测成功后自动建立反向 SSH 端口映射，让远端通过本机代理访问外网。

### 5. 支持便捷端口映射参数

新增 `--map-local-port` 和 `--remote-port`，不再必须手写完整 `R:` 规则。

示例：

```powershell
--map-local-port 7897
```

等价于：

```text
R:127.0.0.1:7897:127.0.0.1:7897
```

### 6. 支持设备码登录回传

新增本机设备码登录接收器：

```powershell
--device-login-port 17860
```

远端 `codex login --device-auth` 输出 URL 和 code 后，脚本会把信息回传到本机，并自动打开本机浏览器。

支持 GET 和 POST JSON 两种方式，便于后续扩展。

### 7. 支持自动安装远端 Codex

新增 `--setup-codex`。

脚本会检查远端是否存在 `codex`：

- 已存在：读取版本并继续配置。
- 不存在：优先使用官方 install.sh 安装。
- install.sh 失败：回退到 `npm install -g @openai/codex`。
- Node.js 版本过低：在 Ubuntu/Debian 上尝试安装 Node.js 20。

### 8. 支持远端 Codex 默认代理配置

新增远端代理持久化配置。

脚本会写入：

- `/etc/profile.d/codex_proxy.sh`
- `/etc/environment`
- `/root/.bashrc`
- `/usr/local/bin/codex` wrapper

效果是远端直接运行 `codex` 时，默认通过本机代理反向隧道访问外网。

### 9. 登录前自动退出旧 Codex 状态

默认在设备码登录前执行：

```bash
codex logout
```

然后再执行：

```bash
codex login --device-auth
```

如果确实需要跳过，可使用：

```powershell
--skip-codex-logout
```

### 10. VSCode 打开顺序调整

现在 VSCode 会在以下步骤完成后再打开：

1. SSH config 写入
2. 端口映射启动
3. 远端 Codex 检查或安装
4. 远端代理配置
5. 退出旧登录
6. 触发设备码登录

这样用户进入 VSCode 远程终端时，远端 Codex 已经处于可用状态。

### 11. 增加测试覆盖

新增：

```text
Instructions/test_vscode_remote_codex_proxy.py
```

测试覆盖：

- SSH 命令解析
- 多主机配置解析
- 端口映射解析
- 自动代理候选端口
- 设备码登录接收器
- Codex 登录 URL/code 提取

运行方式：

```powershell
python -m unittest Instructions.test_vscode_remote_codex_proxy
```

## 推荐展示用命令

完整流程示例：

```powershell
$env:REMOTE_SSH_PASSWORD="你的SSH密码"

python .\Instructions\vscode_remote_codex_proxy.py `
  --ssh-command "ssh -p 2223 root@gpu-a.example.com" `
  --password-env REMOTE_SSH_PASSWORD `
  --alias example-gpu-codex-test-2223 `
  --remote-path /root `
  --device-login-port 17860 `
  --setup-codex
```

测试模式：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --ssh-command "ssh -p 2223 root@gpu-a.example.com" `
  --password-env REMOTE_SSH_PASSWORD `
  --alias example-gpu-codex-test-2223 `
  --remote-path /root `
  --device-login-port 17860 `
  --setup-codex `
  --no-vscode `
  --no-open-browser `
  --exit-after-setup
```

## 本轮验证摘要

已验证：

- 本机 Codex CLI 可用：`codex-cli 0.144.3`
- 远端 Codex 可用：`Codex Doctor v0.144.3`
- 远端 WebSocket 连通：`HTTP 101 Switching Protocols`
- 远端 provider endpoints 可访问
- 远端真实 `codex exec` 返回 `REMOTE_CODEX_OK`

## 准备推送前建议

推送前建议用户确认：

1. README 中示例端口和别名是否符合自己的常用命名。
2. 是否需要保留本轮验证记录。
3. 是否需要把 `Instructions/session_2026-07-13_remote_codex_repair.md` 改成更通用的故障排查文档。
4. 是否要在 GitHub README 中保留中文为主，还是增加英文摘要。

