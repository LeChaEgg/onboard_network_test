"""Ookla Speedtest CLI wrapper."""
import json
import logging
import shutil
import subprocess
from datetime import datetime

log = logging.getLogger(__name__)


def local_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SpeedtestRunner:
    def __init__(self, server_id=None, server_ids=None, fallback=True, timeout=120, binary="speedtest"):
        candidates = server_ids if server_ids is not None else server_id
        self.server_ids = self._normalize_server_ids(candidates)
        self.server_id = self.server_ids[0] if self.server_ids else None
        self.fallback = self._normalize_bool(fallback, "fallback")
        self.timeout = self._normalize_timeout(timeout)
        self.binary = self._normalize_binary(binary)

    @staticmethod
    def _normalize_server_id(server_id):
        if server_id in (None, ""):
            return None
        try:
            server_id = int(server_id)
        except (TypeError, ValueError) as e:
            raise ValueError("server_id must be a positive integer or null") from e
        if server_id <= 0:
            raise ValueError("server_id must be a positive integer or null")
        return server_id

    @classmethod
    def _normalize_server_ids(cls, server_ids):
        if server_ids in (None, ""):
            return []
        if isinstance(server_ids, (str, int)):
            server_ids = [server_ids]

        normalized = []
        try:
            iterator = iter(server_ids)
        except TypeError as e:
            raise ValueError("server_ids must be a list of positive integers") from e

        for server_id in iterator:
            normalized_id = cls._normalize_server_id(server_id)
            if normalized_id is not None and normalized_id not in normalized:
                normalized.append(normalized_id)
        return normalized

    @staticmethod
    def _normalize_bool(value, name):
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "y", "on"}:
                return True
            if normalized in {"0", "false", "no", "n", "off"}:
                return False
        raise ValueError(f"{name} must be true or false")

    @staticmethod
    def _normalize_timeout(timeout):
        try:
            timeout = float(timeout)
        except (TypeError, ValueError) as e:
            raise ValueError("timeout must be a positive number of seconds") from e
        if timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        return timeout

    @staticmethod
    def _normalize_binary(binary):
        if not binary or not str(binary).strip():
            raise ValueError("speedtest_binary must be a non-empty command")
        return str(binary).strip()

    def _build_command(self, server_id=None):
        cmd = [
            self.binary,
            "--format=json",
            "--progress=no",
            "--accept-license",
            "--accept-gdpr",
        ]
        if server_id:
            cmd.extend(["--server-id", str(server_id)])
        return cmd

    def _run_command(self, server_id=None):
        if shutil.which(self.binary) is None:
            raise RuntimeError(
                f"Ookla CLI '{self.binary}' not found. Install it and ensure it is on PATH."
            )

        cmd = self._build_command(server_id)
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or f"speedtest exited with code {completed.returncode}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"failed to parse speedtest JSON output: {e}") from e

    def _run_with_candidates(self):
        last_error = None
        for server_id in self.server_ids:
            try:
                return self._run_command(server_id)
            except Exception as e:
                last_error = e
                log.warning("Server %s unavailable (%s)", server_id, e)

        if self.server_ids and not self.fallback:
            raise last_error or RuntimeError("all configured servers failed")

        if self.server_ids:
            log.warning("All configured servers unavailable; falling back to auto")
        return self._run_command()

    @staticmethod
    def _mbps(section):
        bandwidth = (section or {}).get("bandwidth")
        if bandwidth is None:
            return ""
        return round((float(bandwidth) * 8) / 1_000_000, 3)

    @staticmethod
    def _round(value, digits=2):
        if value is None:
            return ""
        return round(float(value), digits)

    @staticmethod
    def _bytes(section):
        value = (section or {}).get("bytes")
        return int(value) if value is not None else ""

    def run(self) -> dict:
        result = self._run_with_candidates()
        ping = result.get("ping") or {}
        download = result.get("download") or {}
        upload = result.get("upload") or {}
        download_latency = download.get("latency") or {}
        upload_latency = upload.get("latency") or {}
        server = result.get("server") or {}

        return {
            "timestamp": local_timestamp(),
            "download_mbps": self._mbps(download),
            "upload_mbps": self._mbps(upload),
            "idle_latency_ms": self._round(ping.get("latency")),
            "idle_jitter_ms": self._round(ping.get("jitter")),
            "download_latency_ms": self._round(download_latency.get("iqm")),
            "download_jitter_ms": self._round(download_latency.get("jitter")),
            "upload_latency_ms": self._round(upload_latency.get("iqm")),
            "upload_jitter_ms": self._round(upload_latency.get("jitter")),
            "packet_loss_percent": self._round(result.get("packetLoss"), digits=3),
            "download_bytes": self._bytes(download),
            "upload_bytes": self._bytes(upload),
            "server_id_used": server.get("id", ""),
            "server_name": server.get("name", ""),
            "server_location": server.get("location", ""),
            "server_country": server.get("country", ""),
        }
