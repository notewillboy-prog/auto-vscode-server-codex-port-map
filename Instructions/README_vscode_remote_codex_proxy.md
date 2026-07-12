# VSCode 远程连接与 Codex 端口代理脚本说明

本文档记录本次回答的全部内容，并配套提供脚本：

- `Instructions/vscode_remote_codex_proxy.py`

## 功能

该脚本用于在 Windows 本机上完成三件事：

1. 使用你提供的服务器 SSH 信息和密码连接远程服务器。
2. 将本机生成的 SSH 公钥安装到服务器的 `~/.ssh/authorized_keys`，让后续 VSCode Remote-SSH 使用密钥登录。
3. 启动 VSCode 连接远程服务器，并保持一个 SSH 端口代理，使服务器上的 Codex 可以通过本机端口正常访问需要的本地服务。

脚本不会把密码写入文件。密码只用于第一次安装公钥。

## GitHub 参考来源

本脚本参考了 GitHub 上公开的 VSCode Remote-SSH 与 SSH tunnel 实践：

- [microsoft/vscode-docs: Remote Development using SSH](https://github.com/microsoft/vscode-docs/blob/main/docs/remote/ssh.md)
- [microsoft/vscode issue #84021: Workspace setting to configure port forwarding](https://github.com/microsoft/vscode/issues/84021)
- [JaneliaSciComp/ssh_cluster_tunnel_for_vscode](https://github.com/JaneliaSciComp/ssh_cluster_tunnel_for_vscode)

采用的思路是：脚本只负责准备标准 OpenSSH 配置、密钥和端口转发，VSCode 仍通过 Remote-SSH 插件读取 `~/.ssh/config` 来连接服务器。这样每次连接不同主机时也能稳定复用 VSCode 官方支持的路径。

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

如果 `code` 命令不可用，请在 VSCode 中打开命令面板，执行：

```text
Shell Command: Install 'code' command in PATH
```

## 基本用法

在项目根目录执行：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --host 服务器IP或域名 `
  --user 用户名 `
  --port 22 `
  --remote-path /home/用户名/project `
  --forward R:127.0.0.1:17860:127.0.0.1:17860
```

执行后脚本会提示输入 SSH 密码。

## 每次连接不同主机的处理

脚本已经考虑“每次连接的主机不一样”的情况。

如果不传 `--alias`，脚本会根据 `user + host + port` 自动生成一个独立的 VSCode Remote-SSH 主机别名，例如：

```text
codex-ubuntu-1.2.3.4-22
codex-root-gpu-server.example.com-2222
```

每台服务器会在本机 `~/.ssh/config` 中拥有自己的配置块，不会覆盖其他服务器。

如果你希望自己指定更短的名字，也可以传：

```powershell
--alias gpu-a100-01
```

建议：

- 临时或经常变化的服务器：不传 `--alias`，让脚本自动生成。
- 固定常用服务器：传一个好记的 `--alias`。
- 同一个 IP 但不同 SSH 端口或不同用户：脚本会生成不同别名。

## 端口代理格式

`--forward` 可以重复传入多次。

默认情况下，`--forward` 会启动一个独立的 `ssh -N` 代理进程。

也可以参考 GitHub 上常见的 Remote-SSH 配置方式，把转发规则写进 `~/.ssh/config`：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --host 1.2.3.4 `
  --user ubuntu `
  --forward R:127.0.0.1:7890:127.0.0.1:7890 `
  --write-forward-to-config
```

如果只希望 VSCode Remote-SSH 连接时自动带起转发，而不额外启动 `ssh -N` 代理进程：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --host 1.2.3.4 `
  --user ubuntu `
  --forward R:127.0.0.1:7890:127.0.0.1:7890 `
  --config-only-forward
```

反向代理，也就是远程服务器访问本机端口：

```text
R:远程监听地址:远程监听端口:本机地址:本机端口
```

示例：

```powershell
--forward R:127.0.0.1:17860:127.0.0.1:17860
```

含义是：远程服务器上的 `127.0.0.1:17860` 会转发到本机的 `127.0.0.1:17860`。如果服务器上的 Codex 需要访问本机某个代理或服务，通常使用这种 `R:` 方式。

正向代理，也就是本机访问远程服务器端口：

```text
L:本机监听地址:本机监听端口:远程地址:远程端口
```

示例：

```powershell
--forward L:127.0.0.1:18080:127.0.0.1:8080
```

含义是：本机访问 `127.0.0.1:18080` 会转发到服务器的 `127.0.0.1:8080`。

## 为 Codex 配置代理的常见示例

如果你的本机有 HTTP 代理，例如监听在：

```text
127.0.0.1:7890
```

可以这样启动：

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --host 1.2.3.4 `
  --user ubuntu `
  --port 22 `
  --remote-path /home/ubuntu `
  --forward R:127.0.0.1:7890:127.0.0.1:7890
```

然后在远程服务器 shell 中设置：

```bash
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
```

这样服务器上的 Codex 就可以通过远程的 `127.0.0.1:7890` 访问本机代理。

## 参数说明

| 参数 | 说明 |
| --- | --- |
| `--host` | 服务器 IP 或域名，必填 |
| `--user` | SSH 用户名，必填 |
| `--port` | SSH 端口，默认 `22` |
| `--alias` | 写入本机 `~/.ssh/config` 的别名；不传时按 `user + host + port` 自动生成 |
| `--password` | SSH 密码；不建议直接传，省略后会安全地交互输入 |
| `--remote-path` | VSCode 打开的远程目录 |
| `--forward` | SSH 端口代理规则，可重复 |
| `--write-forward-to-config` | 同时把端口转发写入 `~/.ssh/config` |
| `--config-only-forward` | 只写入 SSH config，不再启动独立 `ssh -N` 代理进程 |
| `--key-path` | 本机私钥路径，默认 `~/.ssh/codex_remote_ed25519` |
| `--no-vscode` | 只配置 SSH 和端口代理，不启动 VSCode |

## 脚本做了什么

1. 检查或生成本机 SSH 密钥：

```text
~/.ssh/codex_remote_ed25519
~/.ssh/codex_remote_ed25519.pub
```

2. 用密码登录服务器，把公钥追加到：

```text
~/.ssh/authorized_keys
```

3. 更新本机 SSH 配置：

```text
~/.ssh/config
```

写入类似配置：

```sshconfig
Host codex-ubuntu-1.2.3.4-22
    HostName 1.2.3.4
    User ubuntu
    Port 22
    IdentityFile C:\Users\你的用户名\.ssh\codex_remote_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
    RemoteForward 127.0.0.1:7890 127.0.0.1:7890
```

4. 调用 VSCode：

```powershell
code --folder-uri vscode-remote://ssh-remote+codex-ubuntu-1.2.3.4-22/home/ubuntu
```

5. 启动 SSH 端口代理：

```powershell
ssh -N -T -o ExitOnForwardFailure=yes -R 127.0.0.1:7890:127.0.0.1:7890 codex-ubuntu-1.2.3.4-22
```

## 注意事项

- 如果服务器禁用了公钥登录，需要联系服务器管理员开启 `PubkeyAuthentication yes`。
- 如果反向代理需要监听 `0.0.0.0`，服务器端 SSHD 可能需要启用 `GatewayPorts`。只给 Codex 在服务器本机使用时，推荐保持 `127.0.0.1`。
- 不建议把密码写进命令历史。优先让脚本交互式输入密码。
- 停止脚本会停止端口代理，但不会关闭 VSCode。
