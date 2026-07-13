#!/usr/bin/env python3
"""
Configure VSCode Remote-SSH, keep SSH port mappings alive, install Codex on the
remote host when needed, and relay device-code login requests to this machine.
"""

from __future__ import annotations

import argparse
import getpass
import http.server
import json
import os
import pathlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass


@dataclass(frozen=True)
class SshTarget:
    host: str
    user: str
    port: int


@dataclass(frozen=True)
class RemoteProfile:
    target: SshTarget
    alias: str
    password: str | None
    password_env: str | None
    remote_path: str


@dataclass(frozen=True)
class ForwardRule:
    mode: str
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int

    @property
    def ssh_flag(self) -> str:
        return "-L" if self.mode == "L" else "-R"

    @property
    def ssh_spec(self) -> str:
        return f"{self.listen_host}:{self.listen_port}:{self.target_host}:{self.target_port}"

    @property
    def config_directive(self) -> str:
        return "LocalForward" if self.mode == "L" else "RemoteForward"

    @property
    def config_spec(self) -> str:
        return f"{self.listen_host}:{self.listen_port} {self.target_host}:{self.target_port}"


DEFAULT_PROXY_PORT_CANDIDATES = [
    7897,
    7890,
    7891,
    10809,
    1080,
    2080,
    8080,
    8888,
    20171,
]

PROXY_TEST_URL = "https://chatgpt.com/codex/install.sh"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)


def ensure_paramiko():
    try:
        import paramiko  # type: ignore

        return paramiko
    except ImportError:
        print("Missing Python dependency: paramiko")
        print(f"Install it with: {sys.executable} -m pip install paramiko")
        sys.exit(1)


def ensure_key(key_path: pathlib.Path) -> None:
    if key_path.exists() and key_path.with_suffix(".pub").exists():
        return
    key_path.parent.mkdir(parents=True, exist_ok=True)
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        raise RuntimeError("ssh-keygen was not found. Install Windows OpenSSH Client first.")
    run([ssh_keygen, "-t", "ed25519", "-N", "", "-f", str(key_path)])


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


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
    print(f"Connecting with password to install SSH key: {user}@{host}:{port}")
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
    _stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    err = stderr.read().decode("utf-8", errors="replace")
    client.close()
    if exit_status != 0:
        raise RuntimeError(f"Failed to install public key on remote host: {err}")
    print("SSH public key is installed.")


def open_ssh_client(host: str, port: int, user: str, key_path: pathlib.Path):
    paramiko = ensure_paramiko()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        key_filename=str(key_path),
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )
    return client


def remote_exec(client, command: str, timeout: int = 120) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdin.close()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return stdout.channel.recv_exit_status(), out, err


def build_alias(user: str, host: str, port: int) -> str:
    raw = f"codex-{user}-{host}-{port}".lower()
    alias = re.sub(r"[^a-z0-9_.-]+", "-", raw).strip("-")
    alias = re.sub(r"-{2,}", "-", alias)
    return alias or "codex-remote"


def parse_ssh_command(command: str) -> SshTarget:
    parts = shlex.split(command, posix=False)
    if not parts or pathlib.Path(parts[0]).name.lower() != "ssh":
        raise ValueError("--ssh-command must start with ssh")

    port = 22
    destination: str | None = None
    index = 1
    while index < len(parts):
        part = parts[index]
        if part == "-p":
            index += 1
            if index >= len(parts):
                raise ValueError("ssh -p requires a port")
            port = int(parts[index])
        elif part.startswith("-p") and len(part) > 2:
            port = int(part[2:])
        elif part in {"-l", "-i", "-F", "-J", "-o"}:
            index += 1
        elif part.startswith("-"):
            pass
        else:
            destination = part
        index += 1

    if not destination:
        raise ValueError("--ssh-command must include user@host or host")
    if "@" in destination:
        user, host = destination.rsplit("@", 1)
    else:
        user = getpass.getuser()
        host = destination
    if not user or not host:
        raise ValueError("Could not parse user/host from --ssh-command")
    return SshTarget(host=host, user=user, port=port)


def resolve_target_values(
    ssh_command: str | None,
    host_value: str | None,
    user_value: str | None,
    port_value: int | None,
) -> SshTarget:
    parsed = parse_ssh_command(ssh_command) if ssh_command else None
    host = host_value or (parsed.host if parsed else None)
    user = user_value or (parsed.user if parsed else None)
    port = port_value if port_value is not None else (parsed.port if parsed else 22)
    if not host or not user:
        raise RuntimeError("Provide --ssh-command or both --host and --user")
    return SshTarget(host=host, user=user, port=port)


def resolve_target(args: argparse.Namespace) -> SshTarget:
    return resolve_target_values(args.ssh_command, args.host, args.user, args.port)


def load_remote_profiles(path: pathlib.Path) -> list[RemoteProfile]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("remotes", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("Remote config must be a JSON array or an object with a 'remotes' array.")

    profiles: list[RemoteProfile] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Remote entry #{index} must be an object.")
        target = resolve_target_values(
            item.get("ssh_command"),
            item.get("host"),
            item.get("user"),
            item.get("port"),
        )
        alias = item.get("alias") or build_alias(target.user, target.host, target.port)
        profiles.append(
            RemoteProfile(
                target=target,
                alias=alias,
                password=item.get("password"),
                password_env=item.get("password_env"),
                remote_path=item.get("remote_path", ""),
            )
        )
    return profiles


def get_profile_password(profile: RemoteProfile, skip_key_install: bool) -> str | None:
    if skip_key_install:
        return None
    password = profile.password or (os.environ.get(profile.password_env) if profile.password_env else None)
    if password:
        return password
    return getpass.getpass(f"SSH password for {profile.alias} ({profile.target.user}@{profile.target.host}:{profile.target.port}): ")


def configure_vscode_remote(
    profile: RemoteProfile,
    key_path: pathlib.Path,
    forwards: list[ForwardRule],
    write_forwards: bool,
    open_vscode: bool,
    skip_key_install: bool,
) -> None:
    password = get_profile_password(profile, skip_key_install)
    if password:
        install_public_key(
            profile.target.host,
            profile.target.port,
            profile.target.user,
            password,
            key_path.with_suffix(".pub"),
        )
    update_ssh_config(
        profile.alias,
        profile.target.host,
        profile.target.port,
        profile.target.user,
        key_path,
        forwards,
        write_forwards,
    )
    print(f"VSCode Remote-SSH host alias ready: {profile.alias}")
    if open_vscode:
        start_vscode(profile.alias, profile.remote_path)


def configure_vscode_remotes(
    profiles: list[RemoteProfile],
    key_path: pathlib.Path,
    forwards: list[ForwardRule],
    write_forwards: bool,
    open_vscode: bool,
    skip_key_install: bool,
) -> None:
    ensure_key(key_path)
    for profile in profiles:
        print(f"\nConfiguring VSCode remote: {profile.alias}")
        configure_vscode_remote(profile, key_path, forwards, write_forwards, open_vscode, skip_key_install)


def parse_forward(item: str) -> ForwardRule:
    match = re.fullmatch(r"([LR]):([^:]+):(\d+):([^:]+):(\d+)", item)
    if not match:
        raise ValueError(
            "Forward format must be L:listen_host:listen_port:target_host:target_port "
            "or R:listen_host:listen_port:target_host:target_port"
        )
    mode, listen_host, listen_port, target_host, target_port = match.groups()
    return ForwardRule(mode, listen_host, int(listen_port), target_host, int(target_port))


def parse_proxy_port_from_url(value: str | None) -> int | None:
    if not value:
        return None
    parsed = urllib.parse.urlparse(value if "://" in value else f"http://{value}")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return parsed.port


def proxy_port_candidates(extra_candidates: list[int] | None = None) -> list[int]:
    candidates: list[int] = []
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        port = parse_proxy_port_from_url(os.environ.get(name))
        if port:
            candidates.append(port)
    candidates.extend(extra_candidates or [])
    candidates.extend(DEFAULT_PROXY_PORT_CANDIDATES)
    seen: set[int] = set()
    unique: list[int] = []
    for port in candidates:
        if 0 < port < 65536 and port not in seen:
            seen.add(port)
            unique.append(port)
    return unique


def can_use_http_proxy(port: int, test_url: str = PROXY_TEST_URL, timeout: int = 12) -> bool:
    proxy_url = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    )
    request = urllib.request.Request(test_url, headers={"User-Agent": "codex-remote-proxy-check"})
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read(1)
            return 200 <= response.status < 500
    except Exception:
        return False


def detect_local_proxy_port(extra_candidates: list[int] | None = None) -> int:
    candidates = proxy_port_candidates(extra_candidates)
    print(f"Detecting local VPN/proxy port from candidates: {', '.join(map(str, candidates))}")
    for port in candidates:
        if can_use_http_proxy(port):
            print(f"Detected usable local VPN/proxy port: {port}")
            return port
    raise RuntimeError(
        "Could not detect a usable local HTTP/HTTPS proxy port. "
        "Pass --remote-proxy-port and --map-local-port explicitly, or set HTTP_PROXY/HTTPS_PROXY."
    )


def has_remote_forward_for_port(forwards: list[str], port: int) -> bool:
    for item in forwards:
        try:
            rule = parse_forward(item)
        except ValueError:
            continue
        if rule.mode == "R" and rule.listen_port == port:
            return True
    return False


def configure_auto_proxy(args: argparse.Namespace) -> None:
    if not args.setup_codex or args.no_auto_proxy_detect:
        return
    if args.remote_proxy_port:
        if not args.map_local_port and not has_remote_forward_for_port(args.forward, args.remote_proxy_port):
            args.map_local_port.append(args.remote_proxy_port)
            print(f"Auto-added reverse mapping for explicit proxy port: {args.remote_proxy_port}")
        return

    detected_port = detect_local_proxy_port(args.proxy_port_candidate)
    args.remote_proxy_port = detected_port
    if not has_remote_forward_for_port(args.forward, detected_port):
        args.map_local_port.append(detected_port)


def make_remote_local_port_forward(
    local_port: int,
    remote_port: int | None,
    local_host: str,
    remote_host: str,
) -> ForwardRule:
    return ForwardRule("R", remote_host, remote_port or local_port, local_host, local_port)


def update_ssh_config(
    alias: str,
    host: str,
    port: int,
    user: str,
    key_path: pathlib.Path,
    forwards: list[ForwardRule],
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
        "    StrictHostKeyChecking accept-new",
        "    ServerAliveInterval 30",
        "    ServerAliveCountMax 3",
    ]
    if write_forwards:
        for rule in forwards:
            lines.append(f"    {rule.config_directive} {rule.config_spec}")
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
    print(f"Updated SSH config: {config_path}")


def start_vscode(alias: str, remote_path: str | None) -> subprocess.Popen[str] | None:
    code = shutil.which("code")
    if not code:
        print("The 'code' command was not found. Open VSCode Remote-SSH manually if needed.")
        print(f"Remote-SSH host alias: {alias}")
        return None

    target = f"vscode-remote://ssh-remote+{alias}{remote_path or ''}"
    print(f"Starting VSCode Remote-SSH: {target}")
    return subprocess.Popen([code, "--folder-uri", target])


def start_proxy(alias: str, forwards: list[ForwardRule]) -> subprocess.Popen[str] | None:
    if not forwards:
        return None
    ssh = shutil.which("ssh")
    if not ssh:
        raise RuntimeError("ssh was not found. Install Windows OpenSSH Client first.")

    cmd = [ssh, "-N", "-T", "-o", "ExitOnForwardFailure=yes", "-o", "StrictHostKeyChecking=accept-new"]
    for rule in forwards:
        cmd.extend([rule.ssh_flag, rule.ssh_spec])
    cmd.append(alias)

    print("Starting SSH port forwarding. Press Ctrl+C to stop it.")
    print(f"+ {' '.join(cmd)}")
    return subprocess.Popen(cmd, text=True)


class DeviceLoginHandler(http.server.BaseHTTPRequestHandler):
    server_version = "CodexDeviceLoginRelay/1.0"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/", "/device-login"}:
            self.send_error(404)
            return
        params = urllib.parse.parse_qs(parsed.query)
        self._handle_login(
            url=first(params, "url") or first(params, "verification_uri"),
            code=first(params, "code") or first(params, "user_code"),
        )

    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/device-login":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            data = json.loads(raw_body or "{}")
        else:
            data = {key: values[0] for key, values in urllib.parse.parse_qs(raw_body).items()}
        self._handle_login(
            url=data.get("url") or data.get("verification_uri"),
            code=data.get("code") or data.get("user_code"),
        )

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[device-login] {self.address_string()} - {fmt % args}")

    def _handle_login(self, url: str | None, code: str | None) -> None:
        if not url and not code:
            self.send_response(400, "Bad Request")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Missing url/verification_uri or code/user_code\n")
            return

        print("\nReceived remote device-login request:")
        if code:
            print(f"  Device code: {code}")
        if url:
            print(f"  Login URL: {url}")
            if getattr(self.server, "open_browser", True):
                webbrowser.open(url)

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        message = "Device-login request received on the local machine.\n"
        if code:
            message += f"Device code: {code}\n"
        if url:
            message += f"Login URL: {url}\n"
        self.wfile.write(message.encode("utf-8"))


def first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def start_device_login_server(
    host: str,
    port: int,
    open_browser: bool,
) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer((host, port), DeviceLoginHandler)
    server.open_browser = open_browser  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Device-login relay is listening at http://{host}:{port}/device-login")
    return server


def wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def build_forwards(args: argparse.Namespace) -> list[ForwardRule]:
    forwards = [parse_forward(item) for item in args.forward]
    for local_port in args.map_local_port:
        forwards.append(
            make_remote_local_port_forward(
                local_port=local_port,
                remote_port=args.remote_port,
                local_host=args.local_host,
                remote_host=args.remote_host,
            )
        )
    if args.device_login_port:
        forwards.append(
            make_remote_local_port_forward(
                local_port=args.device_login_port,
                remote_port=args.device_login_remote_port,
                local_host=args.device_login_host,
                remote_host=args.remote_host,
            )
        )
    return forwards


def proxy_env(remote_proxy_port: int | None) -> str:
    if not remote_proxy_port:
        return ""
    proxy = f"http://127.0.0.1:{remote_proxy_port}"
    return (
        f"export HTTP_PROXY={shell_quote(proxy)} HTTPS_PROXY={shell_quote(proxy)} "
        f"http_proxy={shell_quote(proxy)} https_proxy={shell_quote(proxy)};"
    )


def check_remote_internet(client, remote_proxy_port: int | None) -> None:
    env = proxy_env(remote_proxy_port)
    command = (
        "set -e; "
        f"{env} "
        "if command -v curl >/dev/null 2>&1; then "
        "curl -fsSL --connect-timeout 15 https://chatgpt.com/codex/install.sh >/dev/null; "
        "else python3 - <<'PY'\n"
        "import urllib.request\n"
        "urllib.request.urlopen('https://chatgpt.com/codex/install.sh', timeout=15).read(128)\n"
        "PY\n"
        "fi"
    )
    code, out, err = remote_exec(client, command, timeout=45)
    if code != 0:
        raise RuntimeError(f"Remote host cannot reach the internet through the configured proxy.\n{out}\n{err}")
    print("Remote internet check through local port forwarding passed.")


def ensure_remote_codex(client, remote_proxy_port: int | None) -> str:
    code, out, _err = remote_exec(
        client,
        "export PATH=\"$HOME/.local/bin:$HOME/bin:$PATH\"; command -v codex && codex --version",
        timeout=30,
    )
    if code == 0:
        version = out.strip().splitlines()[-1] if out.strip() else "codex found"
        print(f"Remote Codex already exists: {version}")
        return version

    print("Remote Codex was not found. Installing Codex CLI...")
    env = proxy_env(remote_proxy_port)
    install_command = (
        "set -e; "
        "export DEBIAN_FRONTEND=noninteractive; "
        f"{env} "
        "if ! command -v curl >/dev/null 2>&1; then "
        "  if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y curl; "
        "  elif command -v yum >/dev/null 2>&1; then yum install -y curl; "
        "  elif command -v dnf >/dev/null 2>&1; then dnf install -y curl; "
        "  else echo 'curl is required but no supported package manager was found' >&2; exit 1; fi; "
        "fi; "
        "("
        "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
        ") || ("
        "echo 'install.sh failed; falling back to npm install -g @openai/codex' >&2; "
        "if ! command -v npm >/dev/null 2>&1; then "
        "  if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y nodejs npm; "
        "  elif command -v yum >/dev/null 2>&1; then yum install -y nodejs npm; "
        "  elif command -v dnf >/dev/null 2>&1; then dnf install -y nodejs npm; "
        "  else echo 'npm is required but no supported package manager was found' >&2; exit 1; fi; "
        "fi; "
        "node_major=$(node -p \"Number(process.versions.node.split('.')[0])\" 2>/dev/null || echo 0); "
        "if [ \"$node_major\" -lt 16 ]; then "
        "  echo 'Node.js is older than 16; installing NodeSource Node.js 20' >&2; "
        "  if command -v apt-get >/dev/null 2>&1; then "
        "    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; "
        "    apt-get remove -y libnode-dev nodejs npm || true; "
        "    apt-get -f install -y; "
        "    apt-get install -y nodejs; "
        "  else echo 'Automatic Node.js upgrade is only implemented for apt-get systems' >&2; exit 1; fi; "
        "fi; "
        "npm install -g @openai/codex@latest --include=optional --force; "
        "codex_dir=$(npm root -g)/@openai/codex; "
        "if [ -d \"$codex_dir\" ]; then (cd \"$codex_dir\" && npm install --include=optional --force); fi"
        "); "
        "export PATH=\"$HOME/.local/bin:$HOME/bin:$PATH\"; "
        "command -v codex; codex --version"
    )
    code, out, err = remote_exec(client, install_command, timeout=300)
    if code != 0:
        raise RuntimeError(f"Failed to install Codex on the remote host.\n{out}\n{err}")
    version = out.strip().splitlines()[-1] if out.strip() else "codex installed"
    print(f"Remote Codex installed: {version}")
    return version


def logout_remote_codex(client) -> None:
    command = 'export PATH="$HOME/.local/bin:$HOME/bin:$PATH"; codex logout'
    code, out, err = remote_exec(client, command, timeout=60)
    combined = "\n".join(part.strip() for part in (out, err) if part.strip())
    if code == 0:
        print("Remote Codex logout completed.")
        if combined:
            print(combined)
        return
    print("Remote Codex logout did not complete cleanly; continuing with device login.")
    if combined:
        print(combined)


def configure_remote_codex_proxy(client, remote_proxy_port: int) -> None:
    proxy_url = f"http://127.0.0.1:{remote_proxy_port}"
    command = f"""
set -e
proxy_url={shell_quote(proxy_url)}
no_proxy_value='localhost,127.0.0.1,::1'
cat > /etc/profile.d/codex_proxy.sh <<EOF
# Added by vscode_remote_codex_proxy: route remote Codex traffic through the local VPN/proxy SSH reverse tunnel.
export HTTP_PROXY="$proxy_url"
export HTTPS_PROXY="$proxy_url"
export http_proxy="$proxy_url"
export https_proxy="$proxy_url"
export NO_PROXY="$no_proxy_value"
export no_proxy="$no_proxy_value"
EOF
chmod 644 /etc/profile.d/codex_proxy.sh
python3 - <<'PY'
from pathlib import Path
path = Path('/etc/environment')
remove = {{'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NO_PROXY', 'no_proxy'}}
lines = path.read_text().splitlines() if path.exists() else []
kept = [line for line in lines if line.split('=', 1)[0] not in remove]
proxy = {proxy_url!r}
no_proxy = 'localhost,127.0.0.1,::1'
kept.extend([
    f'HTTP_PROXY="{{proxy}}"',
    f'HTTPS_PROXY="{{proxy}}"',
    f'http_proxy="{{proxy}}"',
    f'https_proxy="{{proxy}}"',
    f'NO_PROXY="{{no_proxy}}"',
    f'no_proxy="{{no_proxy}}"',
])
path.write_text('\\n'.join(kept) + '\\n')
PY
if ! grep -q 'BEGIN vscode_remote_codex_proxy proxy env' /root/.bashrc 2>/dev/null; then
  cat >> /root/.bashrc <<'EOF'

# BEGIN vscode_remote_codex_proxy proxy env
if [ -f /etc/profile.d/codex_proxy.sh ]; then
  . /etc/profile.d/codex_proxy.sh
fi
# END vscode_remote_codex_proxy proxy env
EOF
fi
real_codex=$(PATH="$HOME/.local/bin:/usr/bin:/bin" command -v codex || true)
if [ -z "$real_codex" ]; then
  echo 'Could not locate real codex binary for proxy wrapper' >&2
  exit 1
fi
cat > /usr/local/bin/codex <<EOF
#!/usr/bin/env sh
# Added by vscode_remote_codex_proxy: ensure remote Codex always uses the local VPN/proxy reverse tunnel.
export HTTP_PROXY="\\${{HTTP_PROXY:-$proxy_url}}"
export HTTPS_PROXY="\\${{HTTPS_PROXY:-$proxy_url}}"
export http_proxy="\\${{http_proxy:-$proxy_url}}"
export https_proxy="\\${{https_proxy:-$proxy_url}}"
export NO_PROXY="\\${{NO_PROXY:-$no_proxy_value}}"
export no_proxy="\\${{no_proxy:-$no_proxy_value}}"
exec "$real_codex" "\\$@"
EOF
chmod 755 /usr/local/bin/codex
sed -i 's/\\r$//' /usr/local/bin/codex /etc/profile.d/codex_proxy.sh /etc/environment /root/.bashrc
printf 'Remote Codex proxy configured: %s\\n' "$proxy_url"
"""
    code, out, err = remote_exec(client, command, timeout=60)
    if code != 0:
        raise RuntimeError(f"Failed to configure remote Codex proxy environment.\n{out}\n{err}")
    print(out.strip())


def post_device_login_to_local(relay_host: str, relay_port: int, url: str | None, code: str | None) -> None:
    data = json.dumps({"verification_uri": url, "user_code": code}).encode("utf-8")
    request = urllib.request.Request(
        f"http://{relay_host}:{relay_port}/device-login",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=5).read()


def extract_device_login(text: str) -> tuple[str | None, str | None]:
    clean = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    urls = re.findall(r"https?://[^\s'\"<>]+", clean)
    url = next((item.rstrip(".,)") for item in urls if "device" in item.lower() or "auth" in item.lower()), None)
    if not url and urls:
        url = urls[0].rstrip(".,)")
    code_match = re.search(r"\b[A-Z0-9]{4,}(?:[- ][A-Z0-9]{4,})+\b", clean)
    code = code_match.group(0) if code_match else None
    return url, code


def run_remote_codex_device_login(
    client,
    remote_proxy_port: int | None,
    relay_host: str,
    relay_port: int,
    timeout: int,
) -> tuple[str | None, str | None]:
    env = proxy_env(remote_proxy_port)
    command = f"{env} export PATH=\"$HOME/.local/bin:$HOME/bin:$PATH\"; codex login --device-auth"
    transport = client.get_transport()
    channel = transport.open_session()
    channel.get_pty()
    channel.exec_command(command)

    output_parts: list[str] = []
    login_sent = False
    deadline = time.monotonic() + timeout
    print("Starting remote 'codex login --device-auth'...")
    while time.monotonic() < deadline:
        while channel.recv_ready():
            chunk = channel.recv(4096).decode("utf-8", errors="replace")
            print(chunk, end="")
            output_parts.append(chunk)
            url, code = extract_device_login("".join(output_parts))
            if (url or code) and not login_sent:
                post_device_login_to_local(relay_host, relay_port, url, code)
                login_sent = True
                print("\nDevice-login details were relayed to this local machine.")
        if channel.exit_status_ready():
            break
        time.sleep(0.2)

    if not channel.exit_status_ready():
        print("Device login is still waiting on the remote host. Complete it in the local browser, then rerun if needed.")
        channel.close()

    url, code = extract_device_login("".join(output_parts))
    if not url and not code:
        raise RuntimeError("Could not detect a device-login URL or code from Codex output.")
    return url, code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure VSCode Remote-SSH, port forwarding, remote Codex install, and device login."
    )
    parser.add_argument("--remotes-file", help="JSON file containing all VSCode Remote-SSH hosts to configure/open")
    parser.add_argument("--ssh-command", help="Full SSH command, e.g. ssh -p 2222 root@gpu-a.example.com")
    parser.add_argument("--host", help="Remote SSH host")
    parser.add_argument("--user", help="Remote SSH user")
    parser.add_argument("--port", type=int, help="Remote SSH port")
    parser.add_argument("--alias", help="Host alias written to ~/.ssh/config")
    parser.add_argument("--password", help="SSH password; omit to enter interactively")
    parser.add_argument("--password-env", help="Read SSH password from this environment variable")
    parser.add_argument("--remote-path", default="", help="Remote folder opened by VSCode")
    parser.add_argument("--forward", action="append", default=[], help="Raw SSH forward rule. Example: R:127.0.0.1:7890:127.0.0.1:7890")
    parser.add_argument("--map-local-port", action="append", type=int, default=[], help="Map a local port to the same remote port; repeatable")
    parser.add_argument("--remote-port", type=int, help="Remote listen port for --map-local-port")
    parser.add_argument("--local-host", default="127.0.0.1", help="Local target host")
    parser.add_argument("--remote-host", default="127.0.0.1", help="Remote listen host")
    parser.add_argument("--device-login-port", type=int, help="Local device-login relay port, also reverse-forwarded to the remote host")
    parser.add_argument("--device-login-remote-port", type=int, help="Remote device-login relay port; defaults to --device-login-port")
    parser.add_argument("--device-login-host", default="127.0.0.1", help="Local device-login relay listen host")
    parser.add_argument("--no-open-browser", action="store_true", help="Do not open the local browser for device login")
    parser.add_argument("--setup-codex", action="store_true", help="Check/install Codex on the remote host and start device login")
    parser.add_argument("--skip-codex-logout", action="store_true", help="Do not run 'codex logout' before device login")
    parser.add_argument("--remote-proxy-port", type=int, help="Remote port that forwards to the local internet proxy")
    parser.add_argument("--proxy-port-candidate", action="append", type=int, default=[], help="Extra local VPN/proxy port candidate to test; repeatable")
    parser.add_argument("--no-auto-proxy-detect", action="store_true", help="Disable automatic local VPN/proxy port detection")
    parser.add_argument("--skip-remote-internet-check", action="store_true", help="Skip remote internet check before Codex install")
    parser.add_argument("--device-login-timeout", type=int, default=120, help="Seconds to wait for Codex to print device-login details")
    parser.add_argument("--key-path", default=str(pathlib.Path.home() / ".ssh" / "codex_remote_ed25519"), help="Local private key path")
    parser.add_argument("--write-forward-to-config", action="store_true", help="Write forwards into ~/.ssh/config too")
    parser.add_argument("--config-only-forward", action="store_true", help="Only write SSH config; do not start ssh -N")
    parser.add_argument("--no-vscode", action="store_true", help="Do not start VSCode")
    parser.add_argument("--skip-key-install", action="store_true", help="Do not install SSH public keys; only write SSH config/open VSCode")
    parser.add_argument("--exit-after-setup", action="store_true", help="Stop port forwarding and exit after setup checks complete")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    key_path = pathlib.Path(args.key_path).expanduser()
    configure_auto_proxy(args)
    forwards = build_forwards(args)

    if args.remotes_file:
        profiles = load_remote_profiles(pathlib.Path(args.remotes_file).expanduser())
        configure_vscode_remotes(
            profiles,
            key_path,
            forwards,
            args.write_forward_to_config or args.config_only_forward,
            open_vscode=not args.no_vscode,
            skip_key_install=args.skip_key_install,
        )
        print(f"\nConfigured {len(profiles)} VSCode remote host(s).")
        return 0

    target = resolve_target(args)
    alias = args.alias or build_alias(target.user, target.host, target.port)

    if args.setup_codex and not args.device_login_port:
        raise RuntimeError("--setup-codex requires --device-login-port")
    if args.setup_codex and not args.remote_proxy_port:
        raise RuntimeError("--setup-codex requires --remote-proxy-port")

    ensure_key(key_path)
    password = args.password or (os.environ.get(args.password_env) if args.password_env else None)
    if not args.skip_key_install:
        if not password:
            password = getpass.getpass("SSH password: ")
        install_public_key(target.host, target.port, target.user, password, key_path.with_suffix(".pub"))
    update_ssh_config(
        alias,
        target.host,
        target.port,
        target.user,
        key_path,
        forwards,
        args.write_forward_to_config or args.config_only_forward,
    )

    device_server = None
    if args.device_login_port:
        device_server = start_device_login_server(
            args.device_login_host,
            args.device_login_port,
            open_browser=not args.no_open_browser,
        )
        if not wait_for_port(args.device_login_host, args.device_login_port):
            raise RuntimeError("Device-login relay failed to start.")

    print(f"SSH Host alias for this connection: {alias}")
    proxy_proc = None if args.config_only_forward else start_proxy(alias, forwards)
    if proxy_proc:
        time.sleep(2)
        if proxy_proc.poll() is not None:
            raise RuntimeError(f"SSH port forwarding exited early with code {proxy_proc.returncode}")

    remote_client = None
    try:
        if args.setup_codex:
            remote_client = open_ssh_client(target.host, target.port, target.user, key_path)
            if not args.skip_remote_internet_check:
                check_remote_internet(remote_client, args.remote_proxy_port)
            ensure_remote_codex(remote_client, args.remote_proxy_port)
            configure_remote_codex_proxy(remote_client, args.remote_proxy_port)
            if not args.skip_codex_logout:
                logout_remote_codex(remote_client)
            run_remote_codex_device_login(
                remote_client,
                args.remote_proxy_port,
                args.device_login_host,
                args.device_login_port,
                args.device_login_timeout,
            )

        if not args.no_vscode:
            start_vscode(alias, args.remote_path)

        if args.exit_after_setup:
            print("Setup completed; exiting because --exit-after-setup was set.")
            return 0

        if not proxy_proc:
            print("Setup completed.")
            return 0

        print("Setup completed. Keeping SSH port forwarding alive; press Ctrl+C to stop.")
        while proxy_proc.poll() is None:
            time.sleep(1)
        return proxy_proc.returncode or 0
    except KeyboardInterrupt:
        print("\nStopping...")
        return 130
    finally:
        if remote_client:
            remote_client.close()
        if proxy_proc and proxy_proc.poll() is None:
            proxy_proc.terminate()
            try:
                proxy_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy_proc.kill()
        if device_server:
            device_server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
