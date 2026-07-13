# 自动化 VS Code 连接服务器 + Codex 端口映射

用于在 Windows 本机上自动配置 VSCode Remote-SSH 连接远程服务器，并启动 SSH 端口映射，使远程服务器上的 Codex 可以通过本机端口访问代理或服务。

## 内容

- [脚本](Instructions/vscode_remote_codex_proxy.py)
- [完整中文说明](Instructions/README_vscode_remote_codex_proxy.md)
- [下次接手模型运行手册](Instructions/NEXT_AGENT_RUNBOOK.md)

## 快速示例

```powershell
python .\Instructions\vscode_remote_codex_proxy.py `
  --host 1.2.3.4 `
  --user ubuntu `
  --remote-path /home/ubuntu `
  --forward R:127.0.0.1:7890:127.0.0.1:7890
```

