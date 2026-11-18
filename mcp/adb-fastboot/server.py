#!/usr/bin/env python3
"""MCP server for structured, chainable adb and fastboot operations."""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mcp.server.mcpserver import MCPServer

ADB = os.environ.get("ADB_FASTBOOT_ADB", "adb")
FASTBOOT = os.environ.get("ADB_FASTBOOT_FASTBOOT", "fastboot")
DEFAULT_TIMEOUT = float(os.environ.get("ADB_FASTBOOT_DEFAULT_TIMEOUT", "30"))
WORKDIR = os.environ.get("ADB_FASTBOOT_WORKDIR", "/mnt/project/RomTools/output")
MAX_OUTPUT = 12000
FLASH_DIRS = [p for p in os.environ.get("ADB_FASTBOOT_FLASH_DIRS", "").split(":") if p]

STATE: dict[str, Any] = {
    "serial": os.environ.get("ADB_FASTBOOT_SERIAL") or None,
    "last_mode": None,
    "history": [],
}

server = MCPServer(
    name="adb-fastboot",
    title="ADB / Fastboot device control",
    description="Structured adb and fastboot tools with chained steps across reboot modes.",
    instructions="Use device_status first. For reboot chains use run_steps with explicit wait_for_device steps.",
)

PARTITION_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class ToolError(Exception):
    pass


def _clip(text: str, limit: int = MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [clipped {len(text) - limit} chars]", True


def _run(argv: list[str], timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "error": f"executable not found: {argv[0]}", "command": shlex.join(argv)}
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"timeout after {timeout}s",
            "command": shlex.join(argv),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    stdout, out_clipped = _clip(proc.stdout)
    stderr, err_clipped = _clip(proc.stderr)
    result: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "command": shlex.join(argv),
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    if out_clipped or err_clipped:
        result["truncated"] = True
    return result


def _record(tool: str, args: dict[str, Any], ok: bool) -> None:
    STATE["history"].append(
        {"tool": tool, "args": {k: v for k, v in args.items() if k != "confirm"}, "ok": ok, "at": int(time.time())}
    )
    del STATE["history"][:-50]


def _parse_devices(stdout: str, skip_header: bool) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for line in stdout.splitlines()[1:] if skip_header else stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        entry: dict[str, Any] = {"serial": parts[0], "state": parts[1]}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                entry[key] = value
        devices.append(entry)
    return devices


def _probe_adb(timeout: float = 3) -> dict[str, Any]:
    result = _run([ADB, "devices", "-l"], timeout=timeout)
    error = result.get("error") or (result.get("stderr") or "adb devices failed").strip() if not result["ok"] else None
    return {
        "ok": result["ok"],
        "error": error,
        "devices": _parse_devices(result.get("stdout", ""), True),
        "elapsed_ms": result.get("elapsed_ms"),
    }


def _probe_fastboot(timeout: float = 3) -> dict[str, Any]:
    result = _run([FASTBOOT, "devices", "-l"], timeout=timeout)
    error = result.get("error") or (result.get("stderr") or "fastboot devices failed").strip() if not result["ok"] else None
    return {
        "ok": result["ok"],
        "error": error,
        "devices": _parse_devices(result.get("stdout", ""), False),
        "elapsed_ms": result.get("elapsed_ms"),
    }


def _probe_both(timeout: float = 3) -> dict[str, Any]:
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        adb_future = pool.submit(_probe_adb, timeout)
        fb_future = pool.submit(_probe_fastboot, timeout)
        adb_probe = adb_future.result()
        fb_probe = fb_future.result()
    adb_devs, fb_devs = adb_probe["devices"], fb_probe["devices"]
    adb_states = {d["state"] for d in adb_devs}
    if fb_devs:
        state = "fastboot"
    elif "device" in adb_states:
        state = "adb"
    elif adb_states & {"recovery", "sideload"}:
        state = "adb_recovery"
    elif "unauthorized" in adb_states:
        state = "adb_unauthorized"
    elif adb_states & {"offline", "no permissions"}:
        state = "adb_offline"
    elif fb_probe["error"] and "timeout" in str(fb_probe["error"]).lower():
        state = "mid_reboot_or_offline"
    elif not adb_probe["ok"] and not fb_probe["ok"]:
        state = "transport_error"
    elif not adb_devs and not fb_devs:
        state = "no_device"
    else:
        state = "unknown"
    return {
        "ok": True,
        "state": state,
        "adb": adb_probe,
        "fastboot": fb_probe,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _list_adb() -> list[dict[str, Any]]:
    return _probe_adb(10)["devices"]


def _list_fastboot() -> list[dict[str, Any]]:
    return _probe_fastboot(10)["devices"]


def _resolve_serial(serial: str | None, mode: str | None = None) -> str:
    chosen = serial or STATE["serial"]
    if chosen:
        return chosen
    candidates = _list_adb() if mode == "adb" else _list_fastboot() if mode == "fastboot" else _list_adb() + _list_fastboot()
    serials = list(dict.fromkeys(d["serial"] for d in candidates))
    if not serials:
        raise ToolError("no device found; connect one or pass serial / call set_target")
    if len(serials) > 1:
        raise ToolError(f"multiple devices {serials}; pass serial or call set_target")
    return serials[0]


def _detect(serial: str | None = None) -> dict[str, Any]:
    probe = _probe_both(10)
    adb_devs, fb_devs = probe["adb"]["devices"], probe["fastboot"]["devices"]
    if serial:
        adb_devs = [d for d in adb_devs if d["serial"] == serial]
        fb_devs = [d for d in fb_devs if d["serial"] == serial]
    if fb_devs:
        mode = "fastboot"
    elif any(d["state"] in ("device", "recovery", "sideload") for d in adb_devs):
        mode = "adb"
    else:
        mode = "offline"
    snapshot = {"mode": mode, "adb": adb_devs, "fastboot": fb_devs}
    if mode != "offline":
        STATE["last_mode"] = mode
    return snapshot


def _wait_for_mode(
    mode: str, serial: str | None = None, timeout: float = 120, interval: float = 2, stable_for: float = 0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    required = max(1, math.ceil(stable_for / interval)) if stable_for else 1
    hits = 0
    last = {"mode": "offline", "adb": [], "fastboot": []}
    while True:
        last = _detect(serial)
        if last["mode"] == mode:
            hits += 1
            if hits >= required:
                return {"ok": True, "mode": mode, "stable_polls": hits, "device": last}
        else:
            hits = 0
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "error": f"device did not reach {mode} within {timeout}s",
                "device": last,
            }
        time.sleep(interval)


def _adb_reboot(
    target: str = "android",
    serial: str | None = None,
    wait: bool = True,
    timeout: float = 120,
    stable_for: float = 0,
) -> dict[str, Any]:
    targets = {
        "android": ["reboot"],
        "bootloader": ["reboot", "bootloader"],
        "fastboot": ["reboot", "fastboot"],
        "fastbootd": ["reboot", "fastboot"],
        "recovery": ["reboot", "recovery"],
        "edl": ["reboot", "edl"],
    }
    if target not in targets:
        raise ToolError(f"unknown target {target!r}; use {sorted(targets)}")
    s = _resolve_serial(serial)
    result = _run([ADB, "-s", s, *targets[target]], timeout=30)
    if not result["ok"]:
        return result
    STATE["last_mode"] = None
    if wait:
        expected = "adb" if target == "android" else "fastboot"
        waited = _wait_for_mode(expected, s, timeout=timeout, stable_for=stable_for)
        result["wait"] = waited
        result["ok"] = waited["ok"]
    result["serial"] = s
    return result


def _adb_logcat(
    lines: int = 200,
    tag: str | None = None,
    priority: str | None = None,
    clear: bool = False,
    serial: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    s = _resolve_serial(serial, mode="adb")
    argv = [ADB, "-s", s, "logcat"]
    if clear:
        argv.append("-c")
    else:
        argv += ["-d", "-t", str(lines)]
        if tag:
            argv += ["-s", f"{tag}:{priority or 'V'}"]
        elif priority:
            argv += ["-s", f"*:{priority}"]
    return _run(argv, timeout=timeout)


def _adb_shell(command: str, su: bool = False, serial: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    if not command.strip():
        raise ToolError("empty command")
    s = _resolve_serial(serial, mode="adb")
    inner = "su -c " + shlex.quote(command) if su else command
    return _run([ADB, "-s", s, "shell", inner], timeout=timeout)


def _adb_file(
    action: str, local: str | None = None, remote: str | None = None, serial: str | None = None, timeout: float = 120
) -> dict[str, Any]:
    if action not in ("push", "pull", "install"):
        raise ToolError("action must be push, pull or install")
    s = _resolve_serial(serial, mode="adb")
    if action == "push":
        if not local or not remote:
            raise ToolError("push needs local and remote")
        path = _host_path(local)
        if not os.path.isfile(path):
            raise ToolError(f"local file not found: {path}")
        return _run([ADB, "-s", s, "push", path, remote], timeout=timeout)
    if action == "pull":
        if not remote or not local:
            raise ToolError("pull needs remote and local")
        path = _host_path(local)
        if not os.path.isdir(path):
            raise ToolError(f"local directory not found: {path}")
        return _run([ADB, "-s", s, "pull", remote, path], timeout=timeout)
    if not local:
        raise ToolError("install needs local")
    path = _host_path(local)
    if not os.path.isfile(path):
        raise ToolError(f"apk not found: {path}")
    return _run([ADB, "-s", s, "install", "-r", path], timeout=timeout)


def _host_path(path: str) -> str:
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(WORKDIR, expanded)
    return os.path.abspath(expanded)


def _flash_path(file_path: str) -> str:
    path = _host_path(file_path)
    if not os.path.isfile(path):
        raise ToolError(f"image not found: {path}")
    if FLASH_DIRS and not any(path.startswith(os.path.abspath(d) + os.sep) for d in FLASH_DIRS):
        raise ToolError(f"path outside ADB_FASTBOOT_FLASH_DIRS: {path}")
    return path


def _fastboot_flash(
    partition: str, file_path: str, serial: str | None = None, confirm: bool = False, timeout: float = 120
) -> dict[str, Any]:
    if not PARTITION_RE.match(partition):
        raise ToolError(f"invalid partition name: {partition!r}")
    path = _flash_path(file_path)
    s = serial or STATE["serial"] or "<serial>"
    argv = [FASTBOOT, "-s", s, "flash", partition, path]
    if not confirm:
        return {"ok": False, "executed": False, "confirm_required": True, "would_run": shlex.join(argv)}
    return _run([FASTBOOT, "-s", _resolve_serial(serial, mode="fastboot"), "flash", partition, path], timeout=timeout)


def _fastboot_erase(partition: str, serial: str | None = None, confirm: bool = False, timeout: float = 60) -> dict[str, Any]:
    if not PARTITION_RE.match(partition):
        raise ToolError(f"invalid partition name: {partition!r}")
    s = serial or STATE["serial"] or "<serial>"
    argv = [FASTBOOT, "-s", s, "erase", partition]
    if not confirm:
        return {"ok": False, "executed": False, "confirm_required": True, "would_run": shlex.join(argv)}
    return _run([FASTBOOT, "-s", _resolve_serial(serial, mode="fastboot"), "erase", partition], timeout=timeout)


def _fastboot_getvar(variable: str = "all", serial: str | None = None, timeout: float = 30) -> dict[str, Any]:
    s = _resolve_serial(serial, mode="fastboot")
    result = _run([FASTBOOT, "-s", s, "getvar", variable], timeout=timeout)
    values: dict[str, str] = {}
    for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        if ":" not in line or line.startswith("(bootloader)"):
            continue
        key, value = line.split(":", 1)
        if not key.strip() or key.strip() in ("finished", "FAILED"):
            continue
        values[key.strip()] = value.strip()
    result["variables"] = values
    for key in ("stdout", "stderr"):
        result.pop(key, None)
    return result


def _fastboot_reboot(
    target: str = "", serial: str | None = None, wait: bool = False, timeout: float = 120
) -> dict[str, Any]:
    targets = {"": ["reboot"], "android": ["reboot"], "bootloader": ["reboot", "bootloader"], "fastbootd": ["reboot", "fastbootd"], "recovery": ["reboot", "recovery"], "edl": ["reboot", "edl"]}
    if target not in targets:
        raise ToolError(f"unknown target {target!r}; use {sorted(targets)}")
    s = _resolve_serial(serial, mode="fastboot")
    result = _run([FASTBOOT, "-s", s, *targets[target]], timeout=30)
    if result["ok"]:
        STATE["last_mode"] = None
    if result["ok"] and wait:
        waited = _wait_for_mode("adb" if target in ("", "android") else "fastboot", s, timeout=timeout)
        result["wait"] = waited
        result["ok"] = waited["ok"]
    result["serial"] = s
    return result


def _fastboot_raw(args: list[str], serial: str | None = None, confirm: bool = False, timeout: float = 60) -> dict[str, Any]:
    if not args or not all(isinstance(a, str) for a in args):
        raise ToolError("args must be a non-empty list of strings")
    destructive = {"flash", "erase", "oem", "flashing", "format"}
    if args[0] in destructive and not confirm:
        return {
            "ok": False,
            "executed": False,
            "confirm_required": True,
            "would_run": shlex.join([FASTBOOT, *args]),
        }
    s = _resolve_serial(serial, mode="fastboot")
    return _run([FASTBOOT, "-s", s, *args], timeout=timeout)


KEY_CODES = {
    "home": 3, "back": 4, "call": 5, "endcall": 6, "power": 26, "volume_up": 24,
    "volume_down": 25, "menu": 82, "enter": 66, "tab": 61, "space": 62,
    "search": 84, "camera": 27, "focus": 80, "media_play": 85, "media_pause": 127,
    "media_next": 87, "media_prev": 88, "wakeup": 221, "sleep": 223,
}


def _ui_dump(serial: str | None = None, clickable_only: bool = False, limit: int = 300) -> dict[str, Any]:
    s = _resolve_serial(serial, mode="adb")
    remote = "/sdcard/window.xml"
    dump = _run([ADB, "-s", s, "shell", "uiautomator", "dump", remote], timeout=30)
    if not dump["ok"]:
        return dump
    cat = _run([ADB, "-s", s, "shell", "cat", remote], timeout=30)
    if not cat["ok"]:
        return cat
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(cat["stdout"])
    except Exception as exc:
        return {"ok": False, "error": f"UI XML parse failed: {exc}"}
    nodes: list[dict[str, Any]] = []
    bounds_re = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
    for node in root.iter("node"):
        attrs = node.attrib
        bounds = bounds_re.match(attrs.get("bounds", ""))
        entry: dict[str, Any] = {}
        for key in ("text", "resource-id", "content-desc", "class", "package"):
            if attrs.get(key):
                entry[key] = attrs[key]
        clickable = attrs.get("clickable") == "true"
        entry["clickable"] = clickable
        if attrs.get("enabled") == "false":
            entry["enabled"] = False
        if bounds:
            x1, y1, x2, y2 = (int(v) for v in bounds.groups())
            entry["center"] = [(x1 + x2) // 2, (y1 + y2) // 2]
            entry["bounds"] = [x1, y1, x2, y2]
        if clickable_only and not clickable:
            continue
        nodes.append(entry)
    result: dict[str, Any] = {"ok": True, "count": len(nodes), "nodes": nodes[:limit]}
    if len(nodes) > limit:
        result["truncated"] = len(nodes) - limit
    return result


def _ui_input(action: str, x: int = 0, y: int = 0, x2: int = 0, y2: int = 0, duration: int = 300, value: str = "", serial: str | None = None) -> dict[str, Any]:
    s = _resolve_serial(serial, mode="adb")
    if action == "tap":
        argv = ["input", "tap", str(int(x)), str(int(y))]
    elif action == "swipe":
        argv = ["input", "swipe", str(int(x)), str(int(y)), str(int(x2)), str(int(y2)), str(int(duration))]
    elif action == "long_press":
        argv = ["input", "swipe", str(int(x)), str(int(y)), str(int(x)), str(int(y)), "800"]
    elif action == "text":
        argv = ["input", "text", shlex.quote(value.replace(" ", "%s"))]
    elif action == "key":
        key = value.lower()
        code = KEY_CODES.get(key, key)
        if not str(code).isdigit():
            raise ToolError(f"unknown key {value!r}; use a keycode or {sorted(KEY_CODES)}")
        argv = ["input", "keyevent", str(code)]
    else:
        raise ToolError("action must be tap, swipe, long_press, text or key")
    return _run([ADB, "-s", s, "shell", " ".join(argv)], timeout=30)


def _ui_click(text: str | None = None, resource_id: str | None = None, content_desc: str | None = None, serial: str | None = None) -> dict[str, Any]:
    dump = _ui_dump(serial)
    if not dump["ok"]:
        return dump
    matches: list[dict[str, Any]] = []
    for node in dump["nodes"]:
        if "center" not in node:
            continue
        for key, wanted in (("text", text), ("resource-id", resource_id), ("content-desc", content_desc)):
            if not wanted:
                continue
            actual = node.get(key, "")
            if actual == wanted or wanted.lower() in actual.lower():
                matches.append(node)
                break
    if not matches:
        return {"ok": False, "error": "no matching UI node", "candidates": dump["nodes"][:40]}
    matches.sort(key=lambda n: (not n.get("clickable", False), len(n.get("text", ""))))
    target = matches[0]
    x, y = target["center"]
    tap = _ui_input("tap", x=x, y=y, serial=serial)
    return {"ok": tap["ok"], "matched": target, "alternatives": len(matches) - 1, "tap": tap}


def _dumpsys(service: str, args: str = "", grep: str | None = None, lines: int = 0, serial: str | None = None) -> dict[str, Any]:
    if not re.match(r"^[A-Za-z0-9_.-]+$", service):
        raise ToolError(f"invalid service name: {service!r}")
    command = "dumpsys " + service + (" " + args if args else "")
    if grep:
        command += " | grep -i " + shlex.quote(grep)
    if lines:
        command += f" | head -n {int(lines)}"
    return _adb_shell(command, su=False, serial=serial, timeout=60)


def _dmesg(lines: int = 200, grep: str | None = None, clear: bool = False, su: bool = True, serial: str | None = None) -> dict[str, Any]:
    command = "dmesg -c" if clear else "dmesg"
    if not clear:
        if grep:
            command += " | grep -i " + shlex.quote(grep)
        command += f" | tail -n {int(lines)}"
    return _adb_shell(command, su=su, serial=serial, timeout=60)


STEP_TOOLS: dict[str, Any] = {
    "device_status": lambda a: _device_status(),
    "device_probe": lambda a: _device_probe(a.get("timeout", 3), a.get("serial")),
    "ui_dump": lambda a: _ui_dump(a.get("serial"), a.get("clickable_only", False), a.get("limit", 300)),
    "ui_input": lambda a: _ui_input(a.get("action", ""), a.get("x", 0), a.get("y", 0), a.get("x2", 0), a.get("y2", 0), a.get("duration", 300), a.get("value", ""), a.get("serial")),
    "ui_click": lambda a: _ui_click(a.get("text"), a.get("resource_id"), a.get("content_desc"), a.get("serial")),
    "dumpsys": lambda a: _dumpsys(a.get("service", ""), a.get("args", ""), a.get("grep"), a.get("lines", 0), a.get("serial")),
    "dmesg": lambda a: _dmesg(a.get("lines", 200), a.get("grep"), a.get("clear", False), a.get("su", True), a.get("serial")),
    "adb_shell": lambda a: _adb_shell(a.get("command", ""), a.get("su", False), a.get("serial"), a.get("timeout", DEFAULT_TIMEOUT)),
    "adb_logcat": lambda a: _adb_logcat(a.get("lines", 200), a.get("tag"), a.get("priority"), a.get("clear", False), a.get("serial"), a.get("timeout", DEFAULT_TIMEOUT)),
    "adb_reboot": lambda a: _adb_reboot(a.get("target", "android"), a.get("serial"), a.get("wait", True), a.get("timeout", 120), a.get("stable_for", 0)),
    "adb_file": lambda a: _adb_file(a.get("action", ""), a.get("local"), a.get("remote"), a.get("serial"), a.get("timeout", 120)),
    "adb_push": lambda a: _adb_file("push", a.get("local"), a.get("remote"), a.get("serial"), a.get("timeout", 120)),
    "adb_pull": lambda a: _adb_file("pull", a.get("local", "."), a.get("remote"), a.get("serial"), a.get("timeout", 120)),
    "fastboot_getvar": lambda a: _fastboot_getvar(a.get("variable", "all"), a.get("serial"), a.get("timeout", 30)),
    "fastboot_flash": lambda a: _fastboot_flash(a.get("partition", ""), a.get("file_path", ""), a.get("serial"), a.get("confirm", False), a.get("timeout", 120)),
    "fastboot_erase": lambda a: _fastboot_erase(a.get("partition", ""), a.get("serial"), a.get("confirm", False), a.get("timeout", 60)),
    "fastboot_reboot": lambda a: _fastboot_reboot(a.get("target", ""), a.get("serial"), a.get("wait", False), a.get("timeout", 120)),
    "fastboot_raw": lambda a: _fastboot_raw(a.get("args", []), a.get("serial"), a.get("confirm", False), a.get("timeout", 60)),
    "wait_for_device": lambda a: _wait_for_mode(a.get("mode", "fastboot"), a.get("serial"), a.get("timeout", 120), a.get("interval", 2), a.get("stable_for", 0)),
}


def _device_status() -> dict[str, Any]:
    snapshot = _detect(STATE["serial"])
    return {
        "ok": True,
        "target_serial": STATE["serial"],
        "mode": snapshot["mode"],
        "adb_devices": snapshot["adb"],
        "fastboot_devices": snapshot["fastboot"],
        "history": STATE["history"][-10:],
    }


PROBE_HINTS = {
    "adb": "reachable over adb",
    "fastboot": "in fastboot/fastbootd; use fastboot_* tools",
    "adb_recovery": "adb in recovery/sideload",
    "adb_unauthorized": "accept the USB debugging prompt on the device",
    "adb_offline": "mid-reboot or USB/driver issue; call wait_for_device",
    "mid_reboot_or_offline": "fastboot probe timed out; wait_for_device then retry",
    "transport_error": "both adb and fastboot probes failed; check platform-tools and USB",
    "no_device": "nothing attached; device off or bootloader not responding",
}


def _device_probe(timeout: float = 3, serial: str | None = None) -> dict[str, Any]:
    probe = _probe_both(timeout)
    if serial:
        for key in ("adb", "fastboot"):
            probe[key]["devices"] = [d for d in probe[key]["devices"] if d["serial"] == serial]
    probe["target_serial"] = serial or STATE["serial"]
    probe["last_known_mode"] = STATE["last_mode"]
    probe["recent"] = STATE["history"][-3:]
    probe["hint"] = PROBE_HINTS.get(probe["state"], "")
    return probe


@server.tool(description="Show pinned serial, current adb/fastboot mode, device lists and recent tool history.")
def device_status() -> str:
    return json.dumps(_device_status(), ensure_ascii=False)


@server.tool(description="Quick parallel adb+fastboot probe with short timeouts. Reports state, per-transport errors/timings and last known mode.")
def device_probe(timeout: float = 3, serial: str | None = None) -> str:
    return json.dumps(_device_probe(timeout, serial), ensure_ascii=False)


@server.tool(description="Pin a serial for all later calls, or pass null to auto-select when exactly one device is present.")
def set_target(serial: str | None) -> str:
    STATE["serial"] = serial or None
    return json.dumps({"ok": True, "serial": STATE["serial"], "device": _detect(STATE["serial"])}, ensure_ascii=False)


@server.tool(description="List devices visible to adb, with their state and product info.")
def adb_devices() -> str:
    return json.dumps({"devices": _list_adb()}, ensure_ascii=False)


@server.tool(description="List devices visible to fastboot.")
def fastboot_devices() -> str:
    return json.dumps({"devices": _list_fastboot()}, ensure_ascii=False)


@server.tool(description="Wait until the device appears in adb or fastboot mode. Polls both, so it spans reboot transitions.")
def wait_for_device(mode: str = "fastboot", serial: str | None = None, timeout: float = 120, interval: float = 2, stable_for: float = 0) -> str:
    if mode not in ("adb", "fastboot", "offline"):
        raise ToolError("mode must be adb, fastboot or offline")
    result = _wait_for_mode(mode, serial, timeout, interval, stable_for)
    _record("wait_for_device", {"mode": mode, "stable_for": stable_for}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Reboot an adb device to android, bootloader, fastboot, recovery or edl; optionally wait for the target mode.")
def adb_reboot(target: str = "android", serial: str | None = None, wait: bool = True, timeout: float = 120, stable_for: float = 0) -> str:
    result = _adb_reboot(target, serial, wait, timeout, stable_for)
    _record("adb_reboot", {"target": target}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Read logcat with -d. Use clear=true to flush the buffer; tag/priority filter otherwise.")
def adb_logcat(lines: int = 200, tag: str | None = None, priority: str | None = None, clear: bool = False, serial: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    result = _adb_logcat(lines, tag, priority, clear, serial, timeout)
    _record("adb_logcat", {"lines": lines, "clear": clear}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Run one adb shell command. su=true wraps it as a single 'su -c' call so root is not dropped.")
def adb_shell(command: str, su: bool = False, serial: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    result = _adb_shell(command, su, serial, timeout)
    _record("adb_shell", {"command": command, "su": su}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Run one command as root via a single 'su -c <command>' adb argument.")
def su_shell(command: str, serial: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    result = _adb_shell(command, su=True, serial=serial, timeout=timeout)
    _record("su_shell", {"command": command}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Read kernel log with dmesg; grep and tail applied on device. Uses root by default; clear=true flushes the ring buffer.")
def dmesg(lines: int = 200, grep: str | None = None, clear: bool = False, su: bool = True, serial: str | None = None, timeout: float = 60) -> str:
    result = _dmesg(lines, grep, clear, su, serial)
    result["timeout"] = timeout
    _record("dmesg", {"lines": lines, "grep": grep, "clear": clear}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Run dumpsys <service> with optional args, device-side grep and head. service must be a simple name like battery or window.")
def dumpsys(service: str, args: str = "", grep: str | None = None, lines: int = 0, serial: str | None = None) -> str:
    result = _dumpsys(service, args, grep, lines, serial)
    _record("dumpsys", {"service": service, "grep": grep}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Dump the current UI hierarchy via uiautomator, parsed into nodes with text, ids and tap centers.")
def ui_dump(serial: str | None = None, clickable_only: bool = False, limit: int = 300) -> str:
    result = _ui_dump(serial, clickable_only, limit)
    _record("ui_dump", {"clickable_only": clickable_only}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Inject touch/keyboard input: action tap, swipe, long_press, text or key. Coordinates are true device pixels.")
def ui_input(action: str, x: int = 0, y: int = 0, x2: int = 0, y2: int = 0, duration: int = 300, value: str = "", serial: str | None = None) -> str:
    result = _ui_input(action, x, y, x2, y2, duration, value, serial)
    _record("ui_input", {"action": action, "value": value}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Find a UI node by text/resource-id/content-desc and tap its center in one call. Returns the node it matched.")
def ui_click(text: str | None = None, resource_id: str | None = None, content_desc: str | None = None, serial: str | None = None) -> str:
    result = _ui_click(text, resource_id, content_desc, serial)
    _record("ui_click", {"text": text, "resource_id": resource_id}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="adb push / pull / install -r. local is a host path, remote is a device path.")
def adb_file(action: str, local: str | None = None, remote: str | None = None, serial: str | None = None, timeout: float = 120) -> str:
    result = _adb_file(action, local, remote, serial, timeout)
    _record("adb_file", {"action": action}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="adb push: copy a host file to a device path. Relative host paths resolve under ADB_FASTBOOT_WORKDIR.")
def adb_push(local: str, remote: str, serial: str | None = None, timeout: float = 120) -> str:
    result = _adb_file("push", local, remote, serial, timeout)
    _record("adb_push", {"local": local, "remote": remote}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="adb pull: copy a device path into a host directory (default: ADB_FASTBOOT_WORKDIR).")
def adb_pull(remote: str, local: str = ".", serial: str | None = None, timeout: float = 120) -> str:
    result = _adb_file("pull", local, remote, serial, timeout)
    _record("adb_pull", {"local": local, "remote": remote}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Read fastboot variables. variable='all' returns a parsed dict of bootloader values.")
def fastboot_getvar(variable: str = "all", serial: str | None = None, timeout: float = 30) -> str:
    return json.dumps(_fastboot_getvar(variable, serial, timeout), ensure_ascii=False)


@server.tool(description="Flash an image. Dry-run by default; set confirm=true to execute.")
def fastboot_flash(partition: str, file_path: str, serial: str | None = None, confirm: bool = False, timeout: float = 120) -> str:
    result = _fastboot_flash(partition, file_path, serial, confirm, timeout)
    _record("fastboot_flash", {"partition": partition, "file_path": file_path, "confirm": confirm}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Erase a partition. Dry-run by default; set confirm=true to execute.")
def fastboot_erase(partition: str, serial: str | None = None, confirm: bool = False, timeout: float = 60) -> str:
    result = _fastboot_erase(partition, serial, confirm, timeout)
    _record("fastboot_erase", {"partition": partition, "confirm": confirm}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Reboot out of fastboot. target='' or 'android' returns to Android; 'bootloader' stays in fastboot.")
def fastboot_reboot(target: str = "", serial: str | None = None, wait: bool = False, timeout: float = 120) -> str:
    result = _fastboot_reboot(target, serial, wait, timeout)
    _record("fastboot_reboot", {"target": target}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Run any other fastboot subcommand with argv tokens. Destructive subcommands require confirm=true.")
def fastboot_raw(args: list[str], serial: str | None = None, confirm: bool = False, timeout: float = 60) -> str:
    result = _fastboot_raw(args, serial, confirm, timeout)
    _record("fastboot_raw", {"args": args, "confirm": confirm}, result["ok"])
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="Execute a chain of steps in one call, spanning adb and fastboot. Each step is {tool, args, label?}. Returns per-step results.")
def run_steps(steps: list[dict[str, Any]], stop_on_error: bool = True, max_output_per_step: int = 2000) -> str:
    if not steps:
        raise ToolError("steps must be a non-empty list")
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, step in enumerate(steps, 1):
        name = step.get("tool")
        if name not in STEP_TOOLS:
            results.append({"index": index, "tool": name, "ok": False, "error": f"unknown tool {name!r}; allowed: {sorted(STEP_TOOLS)}"})
            break
        args = step.get("args") or {}
        if not isinstance(args, dict):
            results.append({"index": index, "tool": name, "ok": False, "error": "args must be an object"})
            break
        step_start = time.monotonic()
        try:
            outcome = STEP_TOOLS[name](args)
            ok = bool(outcome.get("ok"))
        except Exception as exc:  # noqa: BLE001 - step errors must be returned, not raised to the client
            outcome = {"error": str(exc)}
            ok = False
        clipped = dict(outcome)
        for key in ("stdout", "stderr"):
            if isinstance(clipped.get(key), str) and len(clipped[key]) > max_output_per_step:
                clipped[key] = clipped[key][:max_output_per_step] + f"\n... [clipped {len(outcome[key]) - max_output_per_step} chars]"
        results.append(
            {
                "index": index,
                "label": step.get("label", ""),
                "tool": name,
                "ok": ok,
                "elapsed_ms": int((time.monotonic() - step_start) * 1000),
                "result": clipped,
            }
        )
        _record(name, args, ok)
        if not ok and stop_on_error:
            break
    return json.dumps(
        {
            "ok": all(r["ok"] for r in results),
            "steps": results,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "device": _detect(STATE["serial"]),
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    server.run(transport="stdio")
