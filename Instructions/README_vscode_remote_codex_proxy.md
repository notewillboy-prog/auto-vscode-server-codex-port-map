# VSCode Remote Codex 端口映射脚本说明

脚本：`Instructions/vscode_remote_codex_proxy.py`

## 功能

这个脚本在 Windows 本机完成以下工作：

1. 使用 SSH 密码连接远端服务器，并把本机公钥写入远端 `~/.ssh/authorized_keys`。
2. 更新本机 `~/.ssh/config`，让 VSCode Remote-SSH 后续可用密钥连接。
3. 启动 SSH 端口映射，让远端服务器上的 Codex 可以访问本机端口。
4. 可选启动本机设备码登录接收器，远端把登录 URL 和设备码发回来后，本机会自动打开浏览器。
5. 可选检查远端是否安装 Codex；不存在时自动安装。
6. 默认先在远端执行 `codex logout` 清掉旧登录，再触发 `codex login --device-auth`，使用本机设备码登录。
7. 上述流程完成后，自动打开 VSCode Remote-SSH 连接该远程。

脚本不会把 SSH 密码写入文件。密码只用于首次安装公钥。

## 前置条件

本机需要安装：

- Python 3.9 或更高版本
- VSCode
- VSCode 插件：Remote - SSH
- Windows OpenSSH 客户端，也就是命令行里可以运行 `ssh` 和 `ssh-keygen`
- Python 依赖 `paramiko`

安装依赖：

```powershell
python -m pip install paramiko
```

## 基本用法

### 多主机 SSH 命令

每台服务器的 SSH 命令、端口、密码和别名都可以不同。推荐直接传完整 SSH 命令：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --ssh-command "ssh -p 2222 root@gpu-a.example.com" `
  --alias example-gpu-codex-test-2222 `
  --password-env REMOTE_SSH_PASSWORD
```

如果不传 `--alias`，脚本会按 `user + host + port` 自动生成，例如：

```text
codex-root-gpu-a.example.com-2222
```

这会让不同主机写入各自独立的 `~/.ssh/config` 配置块。

### 所有远程主机接入 VSCode

把所有远程主机写进一个 JSON 文件，例如 `Instructions/remotes.example.json`：

```json
{
  "remotes": [
    {
      "ssh_command": "ssh -p 2222 root@gpu-a.example.com",
      "alias": "example-gpu-codex-test-2222",
      "password_env": "EXAMPLE_GPU_PASSWORD",
      "remote_path": "/root"
    },
    {
      "ssh_command": "ssh ubuntu@gpu.example.com",
      "alias": "gpu-example",
      "password_env": "GPU_EXAMPLE_PASSWORD",
      "remote_path": "/home/ubuntu"
    }
  ]
}
```

然后运行：

```powershell
$env:EXAMPLE_GPU_PASSWORD="第一台服务器密码"
$env:GPU_EXAMPLE_PASSWORD="第二台服务器密码"

python .\Instructions\vscode_remote_codex_proxy.py `
  --remotes-file .\Instructions\remotes.example.json
```

脚本会按顺序完成：

1. 为每台服务器生成或复用本机 SSH key。
2. 使用对应密码安装本机公钥。
3. 为每台服务器写入独立的 `~/.ssh/config` Host 配置。
4. 调用 VSCode Remote-SSH 打开每台服务器的 `remote_path`。

只写入配置、不打开 VSCode：

```powershell
--remotes-file .\Instructions\remotes.example.json --no-vscode
```

如果远端已经安装过本机公钥，只想写入配置并打开 VSCode：

```powershell
--remotes-file .\Instructions\remotes.example.json --skip-key-install
```

### 远端通过本机代理访问外网

启用 `--setup-codex` 时，脚本会自动检测本机 VPN/代理端口，并映射给远端服务器：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --ssh-command "ssh -p 2222 root@gpu-a.example.com" `
  --remote-path /home/ubuntu/project `
  --device-login-port 17860 `
  --setup-codex
```

脚本会依次检查环境变量里的代理端口和常见端口，例如 `7897`、`7890`、`10809`、`1080` 等。检测到可用端口后，会自动配置：

```text
远端 127.0.0.1:检测到的端口 -> 本机 127.0.0.1:检测到的端口
```

远端 Codex 可以设置：

```bash
export HTTP_PROXY=http://127.0.0.1:检测到的端口
export HTTPS_PROXY=http://127.0.0.1:检测到的端口
```

如果自动检测没有命中，可以手动追加候选端口：

```powershell
--proxy-port-candidate 7897
```

或者关闭自动检测，完全手动指定：

```powershell
--no-auto-proxy-detect --map-local-port 7897 --remote-proxy-port 7897
```

如果远端监听端口要和本机端口不同：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --host 1.2.3.4 `
  --user ubuntu `
  --map-local-port 7890 `
  --remote-port 17890
```

这会让远端 `127.0.0.1:17890` 转发到本机 `127.0.0.1:7890`。

## 设备码登录

启动本机设备码登录接收器，并映射给远端服务器：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --host 1.2.3.4 `
  --user ubuntu `
  --remote-path /home/ubuntu/project `
  --device-login-port 17860
```

脚本会在本机启动：

```text
http://127.0.0.1:17860/device-login
```

同时通过 SSH 反向映射，让远端服务器也能访问：

```text
http://127.0.0.1:17860/device-login
```

远端服务器可以通过 GET 发送登录信息：

```bash
curl "http://127.0.0.1:17860/device-login?url=https%3A%2F%2Fexample.com%2Fdevice&code=ABCD-EFGH"
```

也可以通过 POST JSON：

```bash
curl -X POST http://127.0.0.1:17860/device-login \
  -H 'Content-Type: application/json' \
  -d '{"verification_uri":"https://example.com/device","user_code":"ABCD-EFGH"}'
```

本机收到请求后会：

- 在控制台显示设备码。
- 自动打开登录 URL。
- 返回一段文本响应，方便远端确认请求已送达。

如果不想自动打开浏览器，加：

```powershell
--no-open-browser
```

## 自动安装并配置远端 Codex

开启 `--setup-codex` 后，脚本会在建立端口映射之后：

1. 通过 `--remote-proxy-port` 检查远端是否能经本机代理访问外网。
2. 检查远端是否存在 `codex`。
3. 不存在或不可运行时，优先使用 `https://chatgpt.com/codex/install.sh` 安装。
4. 如果 install.sh 因 GitHub API 等问题失败，回退到 `npm install -g @openai/codex`。
5. 如果远端 Node.js 低于 16，Ubuntu/Debian 系统会自动通过 NodeSource 安装 Node.js 20。
6. 写入远端代理环境：`/etc/profile.d/codex_proxy.sh`、`/etc/environment`，并安装 `/usr/local/bin/codex` wrapper，确保 Codex 默认通过远端 `127.0.0.1:代理端口` 访问外网。
7. 安装完成后先执行 `codex logout`，清除远端旧登录状态。
8. 再执行 `codex login --device-auth`，并把 URL/code 回传到本机设备码接收器。
9. 登录流程完成后，自动打开 VSCode Remote-SSH 连接该远程主机。

示例：

```powershell
$env:REMOTE_SSH_PASSWORD="你的SSH密码"
python .\Instructions\vscode_remote_codex_proxy.py `
  --ssh-command "ssh -p 2222 root@gpu-a.example.com" `
  --password-env REMOTE_SSH_PASSWORD `
  --alias example-gpu-codex-test-2222 `
  --device-login-port 17860 `
  --setup-codex
```

测试或自动化时可以加：

```powershell
--no-vscode --no-open-browser --exit-after-setup
```

## 通用端口映射

仍然可以使用原始 `--forward` 参数，适合复杂场景：

```powershell
--forward R:127.0.0.1:17860:127.0.0.1:17860
--forward L:127.0.0.1:18080:127.0.0.1:8080
```

格式：

```text
R:远端监听地址:远端监听端口:本机目标地址:本机目标端口
L:本机监听地址:本机监听端口:远端目标地址:远端目标端口
```

## 写入 SSH config

默认情况下，脚本会启动一个独立的 `ssh -N` 进程保持映射。

如果希望把映射规则也写入 `~/.ssh/config`：

```powershell
--write-forward-to-config
```

如果只写入配置，不额外启动 `ssh -N`：

```powershell
--config-only-forward
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--ssh-command` | 完整 SSH 命令，例如 `ssh -p 2222 root@host` |
| `--remotes-file` | 批量远程主机 JSON 文件；用于把所有远程接入 VSCode |
| `--host` | 服务器 IP 或域名；不用 `--ssh-command` 时需要 |
| `--user` | SSH 用户名；不用 `--ssh-command` 时需要 |
| `--port` | SSH 端口，默认 `22` |
| `--alias` | 写入本机 `~/.ssh/config` 的别名；不传则自动生成 |
| `--password` | SSH 密码；不建议直接传，省略后会交互式输入 |
| `--password-env` | 从指定环境变量读取 SSH 密码 |
| `--remote-path` | VSCode 打开的远程目录 |
| `--map-local-port` | 把本机端口映射到远端同端口，可重复 |
| `--remote-port` | 配合 `--map-local-port` 指定远端监听端口 |
| `--remote-proxy-port` | 远端经本机代理访问外网所使用的远端端口；不传时 `--setup-codex` 会自动检测 |
| `--proxy-port-candidate` | 追加本机 VPN/代理端口候选值，可重复 |
| `--no-auto-proxy-detect` | 关闭自动代理端口检测 |
| `--device-login-port` | 启动本机设备码登录接收器并映射给远端 |
| `--device-login-remote-port` | 设备码接收器在远端监听的端口，默认同本机端口 |
| `--setup-codex` | 检查/安装远端 Codex，并触发设备码登录 |
| `--skip-codex-logout` | 设备码登录前不执行远端 `codex logout` |
| `--skip-remote-internet-check` | 跳过远端外网连通性检查 |
| `--forward` | 通用 SSH 端口映射规则，可重复 |
| `--write-forward-to-config` | 把端口映射写入 `~/.ssh/config` |
| `--config-only-forward` | 只写入 SSH config，不启动独立 `ssh -N` 进程 |
| `--key-path` | 本机私钥路径，默认 `~/.ssh/codex_remote_ed25519` |
| `--no-vscode` | 不启动 VSCode，只配置 SSH 和端口映射 |
| `--skip-key-install` | 跳过远端公钥安装，只写入 SSH config/打开 VSCode |
| `--no-open-browser` | 收到设备码登录 URL 时不自动打开浏览器 |
| `--exit-after-setup` | 完成检查/安装/登录触发后退出，用于测试 |

## 本地测试

不连接真实服务器时，可以运行本地单元测试：

```powershell
python -m py_compile Instructions\vscode_remote_codex_proxy.py Instructions\test_vscode_remote_codex_proxy.py
python -m unittest Instructions.test_vscode_remote_codex_proxy
```

这些测试覆盖端口映射解析、便捷映射生成、设备码登录接收器 GET/POST 调用等逻辑。
