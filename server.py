"""
netemu-control: a web control panel for netemu
(https://github.com/5G-MAG/6G-Testbed/tree/main/netemu).

Wraps netemu's NetworkEmulator with a REST API and a static web UI so
network impairment profiles can be applied, cleared, created, and cycled
through on a timer without a terminal.

Run: see README.md (./run.sh handles venv setup and launching). All settings
come from config.yaml (optional, see config.example.yaml) and can be overridden
per run with NETEMU_CONTROL_* environment variables.
"""

import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from netemu import NetworkEmulator
from netemu.exceptions import NetEmuError, ProfileNotFoundError
from netemu.profile import NetworkProfile

BASE_DIR = Path(__file__).parent
CONFIG_PATH = Path(os.environ.get("NETEMU_CONTROL_CONFIG", BASE_DIR / "config.yaml"))

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8090,
    # "auto" = whatever interface holds the default route. On a multi-homed host (e.g. WiFi for
    # internet plus a wired LAN to a test device) that is usually NOT the interface your test
    # traffic uses, so set this explicitly there.
    "interface": "auto",
    "bidirectional": True,
    "ifb_device": "ifb0",
    "profiles_path": "profiles.yaml",
    "custom_profiles_path": "custom_profiles.yaml",
}
# Env var overrides, for one-off runs and containers: NETEMU_CONTROL_<KEY>. NETEMU_INTERFACE and
# PORT are kept as aliases since earlier versions of run.sh documented them.
ENV_ALIASES = {"interface": ["NETEMU_INTERFACE"], "port": ["PORT"]}


def _load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update({k: v for k, v in (yaml.safe_load(CONFIG_PATH.read_text()) or {}).items() if v is not None})
    for key in DEFAULTS:
        for var in [f"NETEMU_CONTROL_{key.upper()}", *ENV_ALIASES.get(key, [])]:
            if os.environ.get(var):
                cfg[key] = os.environ[var]
                break
    cfg["port"] = int(cfg["port"])
    if isinstance(cfg["bidirectional"], str):
        cfg["bidirectional"] = cfg["bidirectional"].lower() in ("1", "true", "yes")
    for key in ("profiles_path", "custom_profiles_path"):
        p = Path(cfg[key])
        cfg[key] = p if p.is_absolute() else BASE_DIR / p
    return cfg


CONFIG = _load_config()
PROFILES_PATH = CONFIG["profiles_path"]
# User-created profiles live in their own file, separate from the curated profiles.yaml that
# ships with the tool, so custom ones never get clobbered or committed by accident and never
# rewrite (and lose the comments in) the shipped file.
CUSTOM_PROFILES_PATH = CONFIG["custom_profiles_path"]
# Remembers the last interface picked via the UI, gitignored (machine-specific). Only used when
# config/env leave the interface on "auto": auto-detection can silently resolve to a different
# interface across restarts, which would leave an impairment active on the old one with nothing
# tracking or clearing it.
INTERFACE_STATE_PATH = BASE_DIR / ".interface_state"
STATIC_DIR = BASE_DIR / "static"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

app = FastAPI(title="Network Emulator Dashboard")


def _resolve_initial_interface() -> str:
    if CONFIG["interface"] != "auto":
        return CONFIG["interface"]
    if INTERFACE_STATE_PATH.exists():
        saved = INTERFACE_STATE_PATH.read_text().strip()
        if saved:
            return saved
    return "auto"


def _persist_interface(iface: str) -> None:
    INTERFACE_STATE_PATH.write_text(iface.strip() + "\n")


_initial_interface = _resolve_initial_interface()
emulator = NetworkEmulator(
    interface=_initial_interface,
    profiles_path=str(PROFILES_PATH),
    bidirectional=CONFIG["bidirectional"],
    ifb_device=CONFIG["ifb_device"],
)
# Persist immediately: an explicit NETEMU_INTERFACE now "sticks" for future restarts too, not
# just for this one, and if "auto" resolved to something concrete that gets remembered as well
# (this is exactly what needs to happen for the fix above to actually prevent the failure mode).
_persist_interface(emulator.interface)
_builtin_names = set(emulator.profiles.keys())
_custom_profiles: dict[str, dict] = {}

if CUSTOM_PROFILES_PATH.exists():
    _loaded = yaml.safe_load(CUSTOM_PROFILES_PATH.read_text()) or {}
    _custom_profiles = _loaded.get("profiles", {}) or {}
    for _name, _data in _custom_profiles.items():
        emulator.profiles[_name] = NetworkProfile.from_dict(_name, _data)


def _save_custom_profiles() -> None:
    CUSTOM_PROFILES_PATH.write_text(
        yaml.safe_dump({"profiles": _custom_profiles}, sort_keys=False)
    )


def _check_sudo_for_tc() -> bool:
    """
    netemu's own check_sudo() runs `sudo -n true`, which fails under a scoped
    sudoers entry (NOPASSWD limited to tc/ip/modprobe, as its own README
    recommends) even though the commands it actually needs work fine. Check
    the real thing instead.
    """
    try:
        result = subprocess.run(["sudo", "-n", "tc", "-V"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False

_lock = threading.Lock()
_pattern_thread: Optional[threading.Thread] = None
_pattern_stop = threading.Event()
_pattern_state = {"running": False, "sequence": [], "interval_s": 0, "loop": True, "step": 0, "current": None}


class ApplyRequest(BaseModel):
    profile: str
    interface: Optional[str] = None


class InterfaceRequest(BaseModel):
    interface: str


class PatternRequest(BaseModel):
    sequence: list[str]
    interval_s: float = 30
    loop: bool = True


class ProfileRequest(BaseModel):
    name: str
    description: str = ""
    delay_ms: int = Field(0, ge=0)
    jitter_ms: int = Field(0, ge=0)
    loss_pct: float = Field(0.0, ge=0, le=100)
    rate_mbit: Optional[float] = Field(None, gt=0)
    corruption_pct: float = Field(0.0, ge=0, le=100)
    reorder_pct: float = Field(0.0, ge=0, le=100)
    duplicate_pct: float = Field(0.0, ge=0, le=100)


def _profile_dict(name: str) -> dict:
    p = emulator.get_profile(name)
    return {
        "name": name,
        "description": p.description,
        "delay_ms": p.delay_ms,
        "jitter_ms": p.jitter_ms,
        "loss_pct": p.loss_pct,
        "rate_mbit": p.rate_mbit,
        "corruption_pct": p.corruption_pct,
        "reorder_pct": p.reorder_pct,
        "duplicate_pct": p.duplicate_pct,
        "is_custom": name in _custom_profiles,
    }


def _stop_pattern_locked() -> None:
    global _pattern_thread
    _pattern_stop.set()
    if _pattern_thread and _pattern_thread.is_alive():
        _pattern_thread.join(timeout=2)
    _pattern_thread = None
    _pattern_state.update({"running": False, "current": None})


def _pattern_loop(sequence: list[str], interval_s: float, loop: bool) -> None:
    _pattern_state.update(
        {"running": True, "sequence": sequence, "interval_s": interval_s, "loop": loop, "step": 0}
    )
    try:
        while not _pattern_stop.is_set():
            for i, name in enumerate(sequence):
                if _pattern_stop.is_set():
                    return
                _pattern_state["step"] = i
                _pattern_state["current"] = name
                try:
                    with _lock:
                        emulator.apply_profile(name)
                except NetEmuError:
                    pass
                if _pattern_stop.wait(interval_s):
                    return
            if not loop:
                return
    finally:
        _pattern_state["running"] = False


@app.get("/api/profiles")
def list_profiles():
    return [_profile_dict(name) for name in emulator.list_profiles()]


@app.get("/api/status")
def get_status():
    with _lock:
        status = emulator.get_status()
    status["sudo_ok"] = _check_sudo_for_tc()
    status["pattern"] = dict(_pattern_state)
    return status


@app.post("/api/interface")
def set_interface(req: InterfaceRequest):
    _stop_pattern_locked()
    with _lock:
        emulator.clear()
        emulator.interface = req.interface
        emulator._ifb_initialized = False
    _persist_interface(req.interface)
    return get_status()


@app.post("/api/apply")
def apply(req: ApplyRequest):
    _stop_pattern_locked()
    if req.interface and req.interface != emulator.interface:
        emulator.interface = req.interface
    try:
        with _lock:
            emulator.apply_profile(req.profile)
    except ProfileNotFoundError:
        raise HTTPException(404, f"no such profile: {req.profile}")
    except NetEmuError as e:
        raise HTTPException(500, str(e))
    return get_status()


@app.post("/api/clear")
def clear():
    _stop_pattern_locked()
    with _lock:
        emulator.clear()
    return get_status()


@app.post("/api/pattern")
def start_pattern(req: PatternRequest):
    global _pattern_thread
    if not req.sequence:
        raise HTTPException(400, "sequence must not be empty")
    unknown = [n for n in req.sequence if n not in emulator.profiles]
    if unknown:
        raise HTTPException(404, f"unknown profiles: {unknown}")
    if req.interval_s <= 0:
        raise HTTPException(400, "interval_s must be positive")
    _stop_pattern_locked()
    _pattern_stop.clear()
    _pattern_thread = threading.Thread(
        target=_pattern_loop, args=(req.sequence, req.interval_s, req.loop), daemon=True
    )
    _pattern_thread.start()
    return {"started": True}


@app.post("/api/pattern/stop")
def stop_pattern():
    _stop_pattern_locked()
    with _lock:
        emulator.clear()
    return get_status()


@app.post("/api/profiles")
def create_profile(req: ProfileRequest):
    """Create or update a custom profile, stored separately from the shipped profiles.yaml."""
    name = req.name.strip().lower()
    if not NAME_RE.match(name):
        raise HTTPException(400, "name must be lowercase alphanumeric with - or _, max 64 chars")
    if name in _builtin_names and name not in _custom_profiles:
        raise HTTPException(409, f"'{name}' is a built-in profile name, choose another")
    data = req.model_dump(exclude={"name"})
    _custom_profiles[name] = data
    emulator.profiles[name] = NetworkProfile.from_dict(name, data)
    _save_custom_profiles()
    return _profile_dict(name)


@app.delete("/api/profiles/{name}")
def delete_profile(name: str):
    if name not in _custom_profiles:
        raise HTTPException(404, f"no such custom profile: {name}")
    del _custom_profiles[name]
    del emulator.profiles[name]
    _save_custom_profiles()
    return {"deleted": name}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    print(f"netemu-control on http://{CONFIG['host']}:{CONFIG['port']}/ (interface: {emulator.interface})")
    uvicorn.run(app, host=CONFIG["host"], port=CONFIG["port"])
