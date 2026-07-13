import json
import argparse
import pathlib
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import vscode_remote_codex_proxy as proxy


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ProxyConfigTests(unittest.TestCase):
    def test_build_alias_is_stable_and_safe(self) -> None:
        self.assertEqual(
            proxy.build_alias("Ubuntu User", "gpu.example.com", 2222),
            "codex-ubuntu-user-gpu.example.com-2222",
        )

    def test_parse_forward_remote(self) -> None:
        rule = proxy.parse_forward("R:127.0.0.1:17860:127.0.0.1:7890")
        self.assertEqual(rule.ssh_flag, "-R")
        self.assertEqual(rule.ssh_spec, "127.0.0.1:17860:127.0.0.1:7890")
        self.assertEqual(rule.config_directive, "RemoteForward")
        self.assertEqual(rule.config_spec, "127.0.0.1:17860 127.0.0.1:7890")

    def test_parse_forward_rejects_bad_format(self) -> None:
        with self.assertRaises(ValueError):
            proxy.parse_forward("R:127.0.0.1:abc:127.0.0.1:7890")

    def test_map_local_port_creates_reverse_forward(self) -> None:
        rule = proxy.make_remote_local_port_forward(
            local_port=7890,
            remote_port=None,
            local_host="127.0.0.1",
            remote_host="127.0.0.1",
        )
        self.assertEqual(rule.ssh_flag, "-R")
        self.assertEqual(rule.ssh_spec, "127.0.0.1:7890:127.0.0.1:7890")

    def test_parse_ssh_command_with_port_user_and_host(self) -> None:
        target = proxy.parse_ssh_command("ssh -p 2222 root@gpu-a.example.com")
        self.assertEqual(target.host, "gpu-a.example.com")
        self.assertEqual(target.user, "root")
        self.assertEqual(target.port, 2222)

    def test_resolve_target_allows_explicit_overrides(self) -> None:
        args = argparse.Namespace(
            ssh_command="ssh -p 2222 root@gpu-a.example.com",
            host="override.example.com",
            user="ubuntu",
            port=2222,
        )
        target = proxy.resolve_target(args)
        self.assertEqual(target.host, "override.example.com")
        self.assertEqual(target.user, "ubuntu")
        self.assertEqual(target.port, 2222)

    def test_resolve_target_uses_ssh_command_defaults(self) -> None:
        args = argparse.Namespace(
            ssh_command="ssh -p 2222 root@gpu-a.example.com",
            host=None,
            user=None,
            port=None,
        )
        target = proxy.resolve_target(args)
        self.assertEqual(target.host, "gpu-a.example.com")
        self.assertEqual(target.user, "root")
        self.assertEqual(target.port, 2222)

    def test_extract_device_login_strips_ansi_codes(self) -> None:
        text = (
            "\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
            "Enter this one-time code\n"
            "\x1b[94m47W2-JQE7G\x1b[0m\n"
        )
        url, code = proxy.extract_device_login(text)
        self.assertEqual(url, "https://auth.openai.com/codex/device")
        self.assertEqual(code, "47W2-JQE7G")

    def test_load_remote_profiles_supports_multiple_hosts(self) -> None:
        data = {
            "remotes": [
                {
                    "ssh_command": "ssh -p 2222 root@gpu-a.example.com",
                    "alias": "example-gpu-a",
                    "password_env": "SERVER_A_PASSWORD",
                    "remote_path": "/root/project-a",
                },
                {
                    "ssh_command": "ssh ubuntu@gpu.example.com",
                    "password_env": "SERVER_B_PASSWORD",
                    "remote_path": "/home/ubuntu/project-b",
                },
            ]
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as handle:
            json.dump(data, handle)
            config_path = pathlib.Path(handle.name)
        try:
            profiles = proxy.load_remote_profiles(config_path)
        finally:
            config_path.unlink(missing_ok=True)

        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0].alias, "example-gpu-a")
        self.assertEqual(profiles[0].target.port, 2222)
        self.assertEqual(profiles[0].password_env, "SERVER_A_PASSWORD")
        self.assertEqual(profiles[1].alias, "codex-ubuntu-gpu.example.com-22")
        self.assertEqual(profiles[1].remote_path, "/home/ubuntu/project-b")

    def test_logout_remote_codex_uses_codex_logout(self) -> None:
        with mock.patch.object(proxy, "remote_exec", return_value=(0, "logged out\n", "")) as remote_exec:
            proxy.logout_remote_codex(object())

        command = remote_exec.call_args.args[1]
        self.assertIn("codex logout", command)

    def test_logout_remote_codex_does_not_raise_on_failure(self) -> None:
        with mock.patch.object(proxy, "remote_exec", return_value=(1, "", "not logged in\n")):
            proxy.logout_remote_codex(object())

    def test_parse_proxy_port_from_local_proxy_url(self) -> None:
        self.assertEqual(proxy.parse_proxy_port_from_url("http://127.0.0.1:7897"), 7897)
        self.assertEqual(proxy.parse_proxy_port_from_url("localhost:10809"), 10809)
        self.assertIsNone(proxy.parse_proxy_port_from_url("http://example.com:7897"))

    def test_detect_local_proxy_port_uses_first_working_candidate(self) -> None:
        with mock.patch.object(proxy, "can_use_http_proxy", side_effect=lambda port: port == 7897):
            self.assertEqual(proxy.detect_local_proxy_port([12345, 7897]), 7897)

    def test_configure_auto_proxy_sets_remote_proxy_and_mapping(self) -> None:
        args = argparse.Namespace(
            setup_codex=True,
            no_auto_proxy_detect=False,
            remote_proxy_port=None,
            proxy_port_candidate=[7897],
            map_local_port=[],
            forward=[],
        )
        with mock.patch.object(proxy, "can_use_http_proxy", side_effect=lambda port: port == 7897):
            proxy.configure_auto_proxy(args)

        self.assertEqual(args.remote_proxy_port, 7897)
        self.assertEqual(args.map_local_port, [7897])

    def test_configure_auto_proxy_adds_mapping_for_explicit_proxy_port(self) -> None:
        args = argparse.Namespace(
            setup_codex=True,
            no_auto_proxy_detect=False,
            remote_proxy_port=7897,
            proxy_port_candidate=[],
            map_local_port=[],
            forward=[],
        )
        proxy.configure_auto_proxy(args)
        self.assertEqual(args.map_local_port, [7897])

    def test_main_starts_vscode_after_codex_setup(self) -> None:
        events: list[str] = []
        server = mock.Mock()
        client = mock.Mock()

        with (
            mock.patch.object(proxy, "ensure_key"),
            mock.patch.object(proxy, "update_ssh_config"),
            mock.patch.object(proxy, "start_device_login_server", return_value=server),
            mock.patch.object(proxy, "wait_for_port", return_value=True),
            mock.patch.object(proxy, "open_ssh_client", return_value=client),
            mock.patch.object(proxy, "check_remote_internet", side_effect=lambda *_args, **_kwargs: events.append("internet")),
            mock.patch.object(proxy, "ensure_remote_codex", side_effect=lambda *_args, **_kwargs: events.append("codex")),
            mock.patch.object(proxy, "configure_remote_codex_proxy", side_effect=lambda *_args, **_kwargs: events.append("proxy-config")),
            mock.patch.object(proxy, "logout_remote_codex", side_effect=lambda *_args, **_kwargs: events.append("logout")),
            mock.patch.object(
                proxy,
                "run_remote_codex_device_login",
                side_effect=lambda *_args, **_kwargs: events.append("device-login"),
            ),
            mock.patch.object(proxy, "start_vscode", side_effect=lambda *_args, **_kwargs: events.append("vscode")),
        ):
            result = proxy.main(
                [
                    "--ssh-command",
                    "ssh root@example.com",
                    "--skip-key-install",
                    "--config-only-forward",
                    "--setup-codex",
                    "--device-login-port",
                    "17860",
                    "--remote-proxy-port",
                    "7897",
                    "--no-auto-proxy-detect",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(events, ["internet", "codex", "proxy-config", "logout", "device-login", "vscode"])
        server.shutdown.assert_called_once()


class DeviceLoginServerTests(unittest.TestCase):
    def test_device_login_get_opens_url_and_returns_code(self) -> None:
        port = free_port()
        with mock.patch("webbrowser.open") as open_browser:
            server = proxy.start_device_login_server("127.0.0.1", port, open_browser=True)
            try:
                url = (
                    f"http://127.0.0.1:{port}/device-login?"
                    + urllib.parse.urlencode({"url": "https://example.test/login", "code": "ABCD-EFGH"})
                )
                body = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
            finally:
                server.shutdown()
                server.server_close()

        open_browser.assert_called_once_with("https://example.test/login")
        self.assertIn("ABCD-EFGH", body)

    def test_device_login_post_json(self) -> None:
        port = free_port()
        with mock.patch("webbrowser.open") as open_browser:
            server = proxy.start_device_login_server("127.0.0.1", port, open_browser=True)
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/device-login",
                    data=json.dumps({"verification_uri": "https://example.test/device", "user_code": "ZXCV"}).encode(
                        "utf-8"
                    ),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                body = urllib.request.urlopen(request, timeout=5).read().decode("utf-8")
            finally:
                server.shutdown()
                server.server_close()

        open_browser.assert_called_once_with("https://example.test/device")
        self.assertIn("ZXCV", body)

    def test_device_login_requires_payload(self) -> None:
        port = free_port()
        server = proxy.start_device_login_server("127.0.0.1", port, open_browser=False)
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/device-login", timeout=5)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
