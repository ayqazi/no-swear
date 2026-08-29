import re
import secrets
import shlex
import string
import subprocess
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from .cli.logging import Logger


@dataclass(frozen=True)
class RemoteHost:
    user: str | None
    host: str
    port: str | None

    @property
    def destination(self) -> str:
        if self.user is None:
            return self.host
        return f"{self.user}@{self.host}"


def parse_remote_host(value: str) -> RemoteHost:
    prefix = "ssh://"
    if not value.startswith(prefix):
        raise ValueError("remote host must start with ssh://")

    authority = value[len(prefix) :]
    if not authority:
        raise ValueError("remote host is required")

    user = None
    if "@" in authority:
        user, authority = authority.split("@", 1)

    port = None
    port_match = re.search(r":(\d+)$", authority)
    if port_match:
        port = port_match.group(1)
        host = authority[: port_match.start()]
    else:
        host = authority

    if not host or ":" in host:
        raise ValueError("remote host must contain a non-empty non-IPv6 host")
    return RemoteHost(user=user, host=host, port=port)


def remote_temp_path(path: str, suffix: str) -> str:
    parent = PurePosixPath(path).parent
    random_part = "".join(
        secrets.choice(string.ascii_letters + string.digits) for _ in range(6)
    )
    return str(parent / f".no-swear.{random_part}{suffix}")


class RemoteRunner:
    def __init__(self, remote_host: RemoteHost, logger: Logger):
        self.remote_host = remote_host
        self.logger = logger

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        remote_command = shlex.join(command)
        ssh_command = ["ssh", "-o", "BatchMode=yes"]
        if self.remote_host.port is not None:
            ssh_command.extend(["-p", self.remote_host.port])
        ssh_command.extend([self.remote_host.destination, remote_command])
        return self._run_transport(ssh_command, "ssh_command")

    def download(
        self, remote_path: str, local_path: str
    ) -> subprocess.CompletedProcess[str]:
        scp_command = self._scp_prefix()
        scp_command.extend(
            [
                f"{self.remote_host.destination}:{remote_path}",
                local_path,
            ]
        )
        return self._run_transport(scp_command, "scp_download")

    def upload(
        self, local_path: str, remote_path: str
    ) -> subprocess.CompletedProcess[str]:
        scp_command = self._scp_prefix()
        scp_command.extend(
            [
                local_path,
                f"{self.remote_host.destination}:{remote_path}",
            ]
        )
        return self._run_transport(scp_command, "scp_upload")

    def _scp_prefix(self) -> list[str]:
        command = ["scp", "-B", "-o", "BatchMode=yes"]
        if self.remote_host.port is not None:
            command.extend(["-P", self.remote_host.port])
        return command

    def _run_transport(self, command: list[str], operation: str):
        t0 = time.perf_counter()
        command_text = shlex.join(command)
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as error:
            elapsed = time.perf_counter() - t0
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            self._write_transport_log(operation, command_text, stdout, stderr)
            self.logger.error(
                "remote_transport_failed",
                {
                    "operation": operation,
                    "elapsed_sec": f"{elapsed:.3f}",
                    "returncode": str(error.returncode),
                    "command": command_text,
                },
                stderr or str(error),
            )
            raise
        except OSError as error:
            elapsed = time.perf_counter() - t0
            self._write_transport_log(operation, command_text, "", str(error))
            self.logger.error(
                "remote_transport_failed",
                {
                    "operation": operation,
                    "elapsed_sec": f"{elapsed:.3f}",
                    "command": command_text,
                },
                str(error),
            )
            raise

        elapsed = time.perf_counter() - t0
        self._write_transport_log(
            operation, command_text, result.stdout or "", result.stderr or ""
        )
        self.logger.info(
            "remote_transport_complete",
            {"operation": operation, "elapsed_sec": f"{elapsed:.3f}"},
        )
        return result

    def _write_transport_log(
        self, operation: str, command: str, stdout: str, stderr: str
    ):
        log_path = self.logger.workdir / "logs" / "remote_transport.log"
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"operation: {operation}\n")
            log_file.write(f"command: {command}\n")
            log_file.write("stdout:\n")
            log_file.write(stdout)
            if stdout and not stdout.endswith("\n"):
                log_file.write("\n")
            log_file.write("stderr:\n")
            log_file.write(stderr)
            if stderr and not stderr.endswith("\n"):
                log_file.write("\n")
            log_file.write("\n")
