"""Speedtest wrapper. Runs download-only or upload-only tests."""
import logging
from datetime import datetime

import speedtest

log = logging.getLogger(__name__)


def local_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SpeedtestRunner:
    def __init__(self, server_id=None, server_ids=None, fallback=True, timeout=15, secure=True):
        candidates = server_ids if server_ids is not None else server_id
        self.server_ids = self._normalize_server_ids(candidates)
        self.server_id = self.server_ids[0] if self.server_ids else None
        self.fallback = self._normalize_bool(fallback, "fallback")
        self.timeout = self._normalize_timeout(timeout)
        self.secure = self._normalize_bool(secure, "secure")
        self._cached_server = None  # reuse same server within a cycle

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

    def _new_speedtest(self):
        return speedtest.Speedtest(secure=self.secure, timeout=self.timeout)

    def _pick_server(self, s):
        """Select server, with optional fallback. Returns the chosen server dict."""
        last_error = None
        for server_id in self.server_ids:
            try:
                s.get_servers([server_id])
                if not s.servers:
                    raise speedtest.NoMatchedServers()
                return s.get_best_server()
            except Exception as e:
                last_error = e
                log.warning("Server %s unavailable (%s)", server_id, e)
                continue

        if self.server_ids and not self.fallback:
            raise last_error or speedtest.NoMatchedServers()

        if self.server_ids:
            log.warning("All configured servers unavailable; falling back to auto")
        s.get_servers([])
        return s.get_best_server()

    def new_cycle(self):
        """Call at the start of each cycle to re-evaluate the best server."""
        self._cached_server = None

    def run(self, mode: str) -> dict:
        """mode: 'download' or 'upload'. Returns result dict."""
        if mode not in ("download", "upload"):
            raise ValueError("mode must be 'download' or 'upload'")
        ts = local_timestamp()

        s = self._new_speedtest()

        if self._cached_server is None:
            self._cached_server = self._pick_server(s)
            log.debug("Selected server: %s (%s)", self._cached_server["id"], self._cached_server.get("name"))
        else:
            # Re-use the server selected at the start of this cycle
            cached_id = self._cached_server["id"]
            try:
                s.get_servers([cached_id])
                if not s.servers:
                    raise speedtest.NoMatchedServers()
                s.get_best_server()
            except Exception as e:
                log.warning("Cached server %s unavailable (%s); selecting a fresh server", cached_id, e)
                s = self._new_speedtest()
                self._cached_server = self._pick_server(s)

        if mode == "download":
            val = s.download() / 1e6
        else:
            val = s.upload() / 1e6

        r = s.results
        server = getattr(r, "server", {}) or {}
        ping = getattr(r, "ping", None)
        bytes_transferred = r.bytes_received if mode == "download" else r.bytes_sent
        return {
            "timestamp": ts,
            "test_type": mode,
            "download_mbps": round(val, 3) if mode == "download" else "",
            "upload_mbps":   round(val, 3) if mode == "upload"    else "",
            "ping_ms": round(float(ping), 2) if ping is not None else "",
            "bytes_transferred": bytes_transferred,
            "server_id_used":   server.get("id", ""),
            "server_name":      server.get("name", ""),
        }
