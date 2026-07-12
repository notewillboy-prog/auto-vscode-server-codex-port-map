#!/usr/bin/env python3
"""
Use VSCode Remote-SSH to connect to a server and keep an SSH port proxy alive.

The script uses the password once to install a local public key on the remote
server. After that, VSCode and the proxy use key-based SSH.
"""

from __future__ import annotations

import argparse
import getpass
import pathlib
import re
import shutil
import subprocess
import sys
import time


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)


def ensure_paramiko():
    try:
        import paramiko  # type: ignore

        return paramiko
    except ImportError:
        print("缺少 Python 依赖 paramiko。请先运行：")
        print(f"  {sys.executable} -m pip install paramiko")
        sys.exit(1)


def ensure_key(key_path: pathlib.Path) -> None:
    if key_path.exists() and key_path.with_suffix(".pub").exists():
        return
    key_path.parent.mkdir(parents=True, exist_ok=True)
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        raise RuntimeError("未找到 ssh-keygen，请先安装 Windows OpenSSH 客户端。")
    run([ssh_keygen, "-t", "ed25519", "-N", "", "-f", str(key_path)])


def install_public_key(
    host: str,
    port: int,
    user: str,
    password: str,
    public_key_path: pathlib.Path,
) -> None:
    paramiko = ensure_paramiko()
    public_key = public_key_path.read_text(encoding="utf-8").strip()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"正在使用密码连接 {user}@{host}:{port} 并安装公钥...")
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )

    command = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
        f"grep -qxF {shell_quote(public_key)} ~/.ssh/authorized_keys || "
        f"printf '%s\\n' {shell_quote(public_key)} >> ~/.ssh/authorized_keys"
    )
    stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    err = stderr.read().decode("utf-8", errors="replace")
    client.close()
    if exit_status != 0:
        raise RuntimeError(f"远程安装公钥失败：{err}")
    print("公钥已安装或已存在。")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def build_alias(user: str, host: str, port: int) -> str:
    raw = f"codex-{user}-{host}-{port}".lower()
    alias = re.sub(r"[^a-z0-9_.-]+", "-", raw).strip("-")
    alias = re.sub(r"-{2,}", "-", alias)
    return alias or "codex-remote"


def update_ssh_config(
    alias: str,
    host: str,
    port: int,
    user: str,
    key_path: pathlib.Path,
    forwards: list[str],
    write_forwards: bool,
) -> None:
    ssh_dir = pathlib.Path.home() / ".ssh"
    config_path = ssh_dir / "config"
    ssh_dir.mkdir(parents=True, exist_ok=True)

    marker_begin = f"# BEGIN vscode_remote_codex_proxy:{alias}"
    marker_end = f"# END vscode_remote_codex_proxy:{alias}"
    lines = [
        marker_begin,
        f"Host {alias}",
        f"    HostName {host}",
        f"    User {user}",
        f"    Port {port}",
        f"    IdentityFile {key_path}",
        "    IdentitiesOnly yes",
        "    ServerAliveInterval 30",
        "    ServerAliveCountMax 3",
    ]
    if write_forwards:
        for item in forwards:
            directive, spec = parse_forward_for_config(item)
            lines.append(f"    {directive} {spec}")
    lines.extend([marker_end, ""])
    block = "\n".join(lines)

    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if marker_begin in existing and marker_end in existing:
        before = existing.split(marker_begin)[0]
        after = existing.split(marker_end, 1)[1].lstrip("\r\n")
        new_content = before + block + after
    else:
        new_content = existing.rstrip() + "\n\n" + block if existing.strip() else block

    config_path.write_text(new_content, encoding="utf-8")
    print(f"已更新 SSH 配置：{config_path}")


def start_vscode(alias: str, remote_path: str | None) -> subprocess.Popen[str] | None:
    code = shutil.which("code")
    if not code:
        print("未找到 code 命令。请在 VSCode 命令面板执行：Shell Command: Install 'code' command in PATH")
        print(f"然后手动打开：Remote-SSH: Connect to Host... -> {alias}")
        return None

    target = f"vscode-remote://ssh-remote+{alias}{remote_path or ''}"
    print(f"正在启动 VSCode Remote-SSH：{target}")
    return subprocess.Popen([code, "--folder-uri", target])


def start_proxy(alias: str, forwards: list[str]) -> subprocess.Popen[str] | None:
    if not forwards:
        return None
    ssh = shutil.which("ssh")
    if not ssh:
        raise RuntimeError("未找到 ssh，请先安装 Windows OpenSSH 客户端。")

    cmd = [ssh, "-N", "-T", "-o", "ExitOnForwardFailure=yes"]
    for item in forwards:
        mode, spec = parse_forward(item)
        cmd.extend([mode, spec])
    cmd.append(alias)

    print("正在启动端口代理。按 Ctrl+C 可停止本脚本和代理。")
    print(f"+ {' '.join(cmd)}")
    return subprocess.Popen(cmd, text=True)


def parse_forward(item: str) -> tuple[str, str]:
    if item.startswith("L:"):
        return "-L", item[2:]
    if item.startswith("R:"):
        return "-R", item[2:]
    raise ValueError("端口代理格式必须以 L: 或 R: 开头，例如 R:127.0.0.1:17860:127.0.0.1:17860")


def parse_forward_for_config(item: str) -> tuple[str, str]:
    mode, spec = parse_forward(item)
    if mode == "-L":
        return "LocalForward", spec
    return "RemoteForward", spec


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Connect VSCode to a remote server and keep SSH port forwarding alive."
    )
    parser.add_argument("--host", required=True, help="服务器地址，例如 1.2.3.4")
    parser.add_argument("--user", required=True, help="SSH 用户名")
    parser.add_argument("--port", type=int, default=22, help="SSH 端口，默认 22")
    parser.add_argument(
        "--alias",
        help="写入 ~/.ssh/config 的主机别名；不传时按 user、host、port 自动生成",
    )
    parser.add_argument("--password", help="SSH 密码；不传则交互式输入")
    parser.add_argument("--remote-path", default="", help="VSCode 打开的远程目录，例如 /home/ubuntu/project")
    parser.add_argument(
        "--forward",
        action="append",
        default=[],
        help=(
            "端口代理，可重复。L:本机监听host:port:远程host:port；"
            "R:远程监听host:port:本机host:port"
        ),
    )
    parser.add_argument(
        "--key-path",
        default=str(pathlib.Path.home() / ".ssh" / "codex_remote_ed25519"),
        help="本机私钥路径",
    )
    parser.add_argument(
        "--write-forward-to-config",
        action="store_true",
        help="把 --forward 同步写入 ~/.ssh/config，让 VSCode Remote-SSH 连接时自动建立转发",
    )
    parser.add_argument(
        "--config-only-forward",
        action="store_true",
        help="只把 --forward 写入 SSH config，不再额外启动独立 ssh -N 代理进程",
    )
    parser.add_argument(
        "--no-vscode",
        action="store_true",
        help="只配置 SSH 和端口代理，不启动 VSCode",
    )
    args = parser.parse_args()

    alias = args.alias or build_alias(args.user, args.host, args.port)
    key_path = pathlib.Path(args.key_path).expanduser()
    ensure_key(key_path)

    password = args.password or getpass.getpass("SSH password: ")
    install_public_key(args.host, args.port, args.user, password, key_path.with_suffix(".pub"))
    update_ssh_config(
        alias,
        args.host,
        args.port,
        args.user,
        key_path,
        args.forward,
        args.write_forward_to_config or args.config_only_forward,
    )

    print(f"本次连接使用的 SSH Host 别名：{alias}")
    vscode_proc = None if args.no_vscode else start_vscode(alias, args.remote_path)
    proxy_proc = None if args.config_only_forward else start_proxy(alias, args.forward)

    if not proxy_proc:
        print("脚本已完成 VSCode 连接配置。")
        return 0

    try:
        while proxy_proc.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止端口代理...")
        proxy_proc.terminate()
        proxy_proc.wait(timeout=10)

    if vscode_proc and vscode_proc.poll() is not None:
        print(f"VSCode 进程已退出，返回码：{vscode_proc.returncode}")
    return proxy_proc.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
