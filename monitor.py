import deps; deps.ensure(["speedtest", "yaml"])  # auto-install missing packages before other imports

import csv
import logging
import os
import re
import sys
import time
from collections.abc import Mapping
from datetime import datetime

import yaml

from runner import SpeedtestRunner

log = logging.getLogger(__name__)

CSV_FIELDS = [
    "timestamp", "test_type",
    "download_mbps", "upload_mbps", "ping_ms",
    "server_id_target", "server_id_used", "server_name",
    "status", "error",
]

DEFAULT_CONFIG = {
    "server_id": None,
    "server_fallback": True,
    "speedtest_timeout": 15,
    "interval": 60,
    "download_rounds": 1,
    "upload_rounds": 5,
    "output_dir": "results",
}

PROVIDERS = {
    "softbank": "SoftBank",
    "kddi": "KDDI",
    "docomo": "Docomo",
}

NETWORK_TYPES = {
    "4g": "4G",
    "5g": "5G",
}

_SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_UNDERSCORES = re.compile(r"_+")
_CHOICE_KEY_CHARS = re.compile(r"[\s_-]+")


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def default_config():
    return DEFAULT_CONFIG.copy()


def _coerce_bool(value, name):
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


def _coerce_int(value, name, minimum=None, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be an integer") from e
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return number


def _coerce_positive_number(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be a positive number") from e
    if number <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return int(number) if number.is_integer() else number


def _coerce_non_empty_string(value, name):
    if value is None:
        raise ValueError(f"{name} must be a non-empty string")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


def validate_config(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("config.yaml must contain a mapping of settings")

    allowed_keys = set(DEFAULT_CONFIG)
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        log.warning("Ignoring unknown config key(s): %s", ", ".join(unknown_keys))

    cfg = default_config()

    server_id = raw.get("server_id", cfg["server_id"])
    if server_id in (None, ""):
        cfg["server_id"] = None
    else:
        cfg["server_id"] = _coerce_int(server_id, "server_id", minimum=1)

    cfg["server_fallback"] = _coerce_bool(
        raw.get("server_fallback", cfg["server_fallback"]),
        "server_fallback",
    )
    cfg["speedtest_timeout"] = _coerce_positive_number(
        raw.get("speedtest_timeout", cfg["speedtest_timeout"]),
        "speedtest_timeout",
    )
    cfg["interval"] = _coerce_positive_number(raw.get("interval", cfg["interval"]), "interval")
    cfg["download_rounds"] = _coerce_int(
        raw.get("download_rounds", cfg["download_rounds"]),
        "download_rounds",
        minimum=0,
    )
    cfg["upload_rounds"] = _coerce_int(
        raw.get("upload_rounds", cfg["upload_rounds"]),
        "upload_rounds",
        minimum=0,
    )
    if cfg["download_rounds"] + cfg["upload_rounds"] == 0:
        raise ValueError("at least one of download_rounds or upload_rounds must be greater than 0")

    cfg["output_dir"] = _coerce_non_empty_string(raw.get("output_dir", cfg["output_dir"]), "output_dir")

    return cfg


def load_config(path="config.yaml"):
    if not os.path.exists(path):
        log.warning("Config file %s not found; using defaults", path)
        return default_config()
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"failed to parse {path}: {e}") from e
    except OSError as e:
        raise ValueError(f"failed to read {path}: {e}") from e
    return validate_config(raw)


def sanitize_label(value, fallback="unknown"):
    label = _coerce_non_empty_string(value, "label")
    label = label.replace(os.sep, "_")
    if os.altsep:
        label = label.replace(os.altsep, "_")
    label = _SAFE_FILENAME_CHARS.sub("_", label)
    label = _REPEATED_UNDERSCORES.sub("_", label).strip("._-")
    return label or fallback


def _format_allowed(choices):
    return "/".join(choices.values())


def _choice_key(value):
    value = _coerce_non_empty_string(value, "choice")
    return _CHOICE_KEY_CHARS.sub("", value).lower()


def normalize_provider(value):
    key = _choice_key(value)
    if key not in PROVIDERS:
        raise ValueError(f"provider must be one of {_format_allowed(PROVIDERS)}")
    return PROVIDERS[key]


def normalize_network(value):
    key = _choice_key(value)
    if key not in NETWORK_TYPES:
        raise ValueError(f"network must be one of {_format_allowed(NETWORK_TYPES)}")
    return NETWORK_TYPES[key]


def prompt_choice(prompt, choices):
    while True:
        raw = input(f"{prompt} [{_format_allowed(choices)}]: ").strip()
        try:
            key = _choice_key(raw)
            if key not in choices:
                raise ValueError
            return choices[key]
        except ValueError:
            print(f"Please choose one of: {_format_allowed(choices)}")


def make_csv_path(output_dir, provider, network, now=None):
    os.makedirs(output_dir, exist_ok=True)
    now = now or datetime.now()
    date = now.strftime("%Y%m%d")
    provider_label = sanitize_label(normalize_provider(provider), "provider")
    network_label = sanitize_label(normalize_network(network), "network")
    filename = f"{date}_{provider_label}_{network_label}.csv"
    return os.path.join(output_dir, filename)


def write_row(csv_path, row):
    new_file = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    row = {field: row.get(field, "") for field in CSV_FIELDS}
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow(row)


def run_one(mode, runner, csv_path):
    try:
        result = runner.run(mode)
        result.update({"status": "ok", "error": ""})
        write_row(csv_path, result)
        mbps = result["download_mbps"] if mode == "download" else result["upload_mbps"]
        log.info("%s: %s Mbps  ping=%sms  server=%s",
                 mode.upper(), mbps, result["ping_ms"], result["server_id_used"])
    except Exception as e:
        write_row(csv_path, {
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "test_type": mode,
            "server_id_target": getattr(runner, "server_id", "") or "",
            "status": "error",
            "error": str(e),
        })
        log.error("%s test failed: %s", mode, e)


def list_servers():
    import speedtest
    log.info("Fetching server list...")
    s = speedtest.Speedtest(secure=True)
    s.get_servers([])
    servers = sorted(
        [srv for sublist in s.servers.values() for srv in sublist],
        key=lambda x: x["d"],
    )
    print(f"\n{'ID':<8} {'Distance(km)':<14} {'Name':<30} {'Sponsor'}")
    print("-" * 70)
    for srv in servers[:30]:
        print(f"{srv['id']:<8} {srv['d']:<14.1f} {srv['name']:<30} {srv['sponsor']}")


def prompt_session_info():
    print("=" * 50)
    print("  Network Bandwidth Monitor")
    print("=" * 50)
    provider = prompt_choice("Provider", PROVIDERS)
    network = prompt_choice("Network type", NETWORK_TYPES)
    return provider, network


def main():
    configure_logging()

    if "--list-servers" in sys.argv:
        list_servers()
        return

    provider, network = prompt_session_info()

    try:
        cfg = load_config()
    except ValueError as e:
        log.error("Invalid configuration: %s", e)
        sys.exit(2)

    output_dir = cfg.get("output_dir", "results")
    csv_path = make_csv_path(output_dir, provider, network)

    interval = cfg.get("interval", 60)
    download_rounds = cfg.get("download_rounds", 1)
    upload_rounds = cfg.get("upload_rounds", 5)

    runner = SpeedtestRunner(
        server_id=cfg.get("server_id"),
        fallback=cfg.get("server_fallback", True),
        timeout=cfg.get("speedtest_timeout", 15),
    )

    print()
    log.info("Provider: %s | Network: %s", provider, network)
    log.info("Output:   %s", csv_path)
    log.info("Cycle: %d download + %d upload, interval=%ss",
             download_rounds, upload_rounds, interval)
    print()

    cycle = 0
    try:
        while True:
            cycle += 1
            log.info("--- Cycle %d ---", cycle)
            runner.new_cycle()

            for _ in range(download_rounds):
                run_one("download", runner, csv_path)
                time.sleep(interval)

            for _ in range(upload_rounds):
                run_one("upload", runner, csv_path)
                time.sleep(interval)

    except KeyboardInterrupt:
        log.info("Stopped after %d cycle(s). Results saved to %s", cycle, csv_path)


if __name__ == "__main__":
    main()
