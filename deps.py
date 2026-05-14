"""Auto-install missing pip packages before the main imports run."""
import importlib
import importlib.util
import subprocess
import sys

_REQUIRED = {
    "speedtest": "speedtest-cli",
    "yaml": "PyYAML",
    "matplotlib": "matplotlib",
}


def _pip_install(packages):
    # In a virtualenv --user is not needed (and would error); outside it may be.
    in_venv = sys.prefix != sys.base_prefix
    cmd = [sys.executable, "-m", "pip", "install", *packages]
    if not in_venv:
        cmd.append("--user")
    try:
        subprocess.check_call(cmd)
        return True
    except subprocess.CalledProcessError:
        return False


def ensure(modules=None):
    """Install the packages needed by the caller.

    ``modules`` is a list of import names from _REQUIRED. Leaving it empty keeps
    the original behavior of checking every project dependency.
    """
    required = _REQUIRED
    if modules is not None:
        if isinstance(modules, str):
            modules = [modules]
        unknown = sorted(set(modules) - set(_REQUIRED))
        if unknown:
            raise ValueError(f"Unknown dependency module(s): {', '.join(unknown)}")
        required = {mod: _REQUIRED[mod] for mod in modules}

    missing = [pip for mod, pip in required.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return
    print(f"[deps] Installing missing packages: {', '.join(missing)}")
    if _pip_install(missing):
        print("[deps] Done.")
    else:
        print(
            "[deps] Auto-install failed. Please install manually:\n"
            f"  pip install {' '.join(missing)}\n"
            "  or inside a venv:\n"
            "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
        )
        sys.exit(1)
