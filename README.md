# VSCode Remote Codex 端口映射与设备码登录

这个项目提供一个 Windows 本机脚本，用来配置 VSCode Remote-SSH，让远端服务器上的 Codex 通过本机 VPN/代理访问外网，并通过本机浏览器完成设备码登录。

## 内容

- [脚本](Instructions/vscode_remote_codex_proxy.py)
- [完整中文说明](Instructions/README_vscode_remote_codex_proxy.md)
- [批量远程示例](Instructions/remotes.example.json)
- [本地测试](Instructions/test_vscode_remote_codex_proxy.py)
- [功能更新说明](Instructions/FEATURE_UPDATE_SUMMARY.md)
- [本轮修复与验证记录](Instructions/session_2026-07-13_remote_codex_repair.md)

## 所有远程接入 VSCode

先准备一个 JSON 文件，例如 [remotes.example.json](Instructions/remotes.example.json)：

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

然后运行：

```powershell
$env:EXAMPLE_GPU_PASSWORD="你的SSH密码"

python .\Instructions\vscode_remote_codex_proxy.py `
  --remotes-file .\Instructions\remotes.example.json
```

脚本会逐台安装 SSH 公钥、写入本机 `~/.ssh/config`，并通过 VSCode Remote-SSH 打开远程主机。

## 完整 Codex 配置流程

正式使用时运行：

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

脚本会按顺序完成：

1. 生成或复用本机 SSH key。
2. 用密码连接远端并安装本机公钥。
3. 写入独立的 VSCode Remote-SSH 配置。
4. 自动检测本机 VPN/代理端口。
5. 建立反向端口映射，让远端通过本机代理访问外网。
6. 启动本机设备码登录接收器。
7. 检查远端 Codex，不存在则安装。
8. 在远端执行 `codex logout`，清除旧登录。
9. 执行 `codex login --device-auth`，把 URL/code 回传到本机。
10. 你在本机浏览器完成设备码授权。
11. 上述流程完成后，自动打开 VSCode Remote-SSH 连接该远程。
12. 脚本继续保持端口映射运行。

如果自动代理端口检测没命中，可以追加候选端口：

```powershell
--proxy-port-candidate 7897
```

或者完全手动指定：

```powershell
--no-auto-proxy-detect --map-local-port 7897 --remote-proxy-port 7897
```

测试模式可以加：

```powershell
--no-vscode --no-open-browser --exit-after-setup
```
