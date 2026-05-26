import datetime
import sys
from pathlib import Path


SEVERITIES = frozenset({"ERROR", "WARN", "INFO"})


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


class Logger:
    def __init__(self, workdir: Path):
        log_dir = workdir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._open_log(log_dir / "main.log")
        self._workdir = workdir

    @property
    def workdir(self) -> Path:
        return self._workdir

    @staticmethod
    def _open_log(path: Path):
        try:
            return open(path, "a", encoding="utf-8")
        except OSError as e:
            print(f"ERROR {now()} log_failed  path={path}, error={e}  Cannot open log file", file=sys.stderr)
            raise

    def error(self, event: str, params: dict | None = None, text: str = ""):
        self.log("ERROR", event, params, text)

    def warn(self, event: str, params: dict | None = None, text: str = ""):
        self.log("WARN", event, params, text)

    def info(self, event: str, params: dict | None = None, text: str = ""):
        self.log("INFO", event, params, text)

    def log(self, severity: str, event: str, params: dict | None = None, text: str = ""):
        if severity not in SEVERITIES:
            severity = "INFO"
        parts = [severity, now(), event]
        if params:
            parts.append(", ".join(f"{k}={v}" for k, v in params.items()))
        if text:
            parts.append(text)
        self._log_file.write("  ".join(parts) + "\n")
        self._log_file.flush()

    def close(self):
        self._log_file.close()