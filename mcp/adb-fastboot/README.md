# adb-fastboot MCP

Structured, chainable adb + fastboot tools for agents. One server covers both
device modes, so a reboot chain does not need the agent to write a fragile
multi-command shell line or manually bridge the adb → fastboot gap.

## Why

Existing MCP servers split the job: adb servers (`droidmind`, `minhalvp`,
`iksnerd/adb-mcp`) do not do fastboot; `fastboot-mcp` does fastboot only and is
untested on hardware; `mitoolkit` has both but is Xiaomi-unlock-focused and
exposes no arbitrary flash tool. This server covers the full chain:

```
run_steps([
  {"tool":"adb_reboot",     "args":{"target":"fastboot","wait":true,"stable_for":10}},
  {"tool":"device_probe",   "args":{"timeout":3}},
  {"tool":"fastboot_getvar","args":{"variable":"product"}},
  {"tool":"fastboot_flash", "args":{"partition":"vendor","file_path":"vendor.erofs.img","confirm":true}},
  {"tool":"fastboot_reboot","args":{"target":"","wait":true}}
])
```

`stable_for` replaces a fixed `sleep 30`: `adb_reboot(wait:true)` polls until the
device appears in fastboot, then `stable_for:10` requires it to stay visible for
~10 s so the connection settles before the flash. Relative image paths resolve
against `ADB_FASTBOOT_WORKDIR` (default `/mnt/project/RomTools/output`, where
`vendor.erofs.img` lives).

Each step runs as a separate `subprocess` argv (never a shell string), returns
a structured result, and `adb_reboot` / `wait_for_device` poll both `adb devices`
and `fastboot devices` so a mode transition is handled in one call.

## Install / run

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py            # stdio MCP
```

OpenCode config entry (`~/.config/opencode/opencode.json`, under `mcp.servers`):

```json
"adb-fastboot": {
  "type": "local",
  "command": ["/mnt/project/romtools-skill/mcp/adb-fastboot/.venv/bin/python",
              "/mnt/project/romtools-skill/mcp/adb-fastboot/server.py"],
  "enabled": true
}
```

Environment:

| Variable | Default | Meaning |
|---|---|---|
| `ADB_FASTBOOT_ADB` | `adb` | adb binary |
| `ADB_FASTBOOT_FASTBOOT` | `fastboot` | fastboot binary |
| `ADB_FASTBOOT_SERIAL` | unset | pin a target serial at startup |
| `ADB_FASTBOOT_DEFAULT_TIMEOUT` | `30` | per-command timeout (seconds) |
| `ADB_FASTBOOT_WORKDIR` | `/mnt/project/RomTools/output` | base dir for relative host paths |
| `ADB_FASTBOOT_FLASH_DIRS` | unset | colon-separated host dirs that `flash` paths must live under |

## Tools

| Tool | Purpose |
|---|---|
| `device_status` | pinned serial, current mode, both device lists, recent history |
| `device_probe` | quick parallel adb+fastboot probe with short timeouts; state, per-transport errors, timings, hint |
| `set_target` | pin a serial, or null for auto-select (only when one device is present) |
| `adb_devices` / `fastboot_devices` | parsed device lists |
| `wait_for_device` | poll adb + fastboot until a mode appears; `stable_for` keeps it stable |
| `adb_reboot` | reboot to android/bootloader/fastboot/recovery/edl, optional wait + `stable_for` |
| `adb_logcat` | `logcat -d -t N` with tag/priority filter, or `-c` clear |
| `adb_shell` | one shell command; `su=true` wraps as one `su -c '...'` argument |
| `su_shell` | same, with root forced on |
| `dmesg` | kernel log with device-side grep/tail; `clear=true` flushes |
| `dumpsys` | `dumpsys <service>` with optional args, grep and head |
| `ui_dump` | uiautomator hierarchy as nodes with text, ids, clickable, tap centers |
| `ui_input` | tap, swipe, long_press, text, key |
| `ui_click` | find node by text/id/desc and tap its center in one call |
| `adb_push` | `adb push <local> <remote>`; relative host path resolves under `ADB_FASTBOOT_WORKDIR` |
| `adb_pull` | `adb pull <remote> <local>`; local defaults to `ADB_FASTBOOT_WORKDIR` |
| `adb_file` | `push`, `pull`, `install -r` (also usable in `run_steps`) |
| `fastboot_getvar` | `getvar all` parsed into a dict |
| `fastboot_flash` | flash a partition; dry-run unless `confirm=true` |
| `fastboot_erase` | erase a partition; dry-run unless `confirm=true` |
| `fastboot_reboot` | leave fastboot (android/bootloader/fastbootd/recovery/edl) |
| `fastboot_raw` | other subcommands with argv tokens; destructive ones need `confirm=true` |
| `run_steps` | execute a chain of the above in one call, with per-step results |

Serial resolution uses the pinned serial if set; otherwise the single attached
device is auto-selected. With several devices and no pin, the tool errors with
the list instead of guessing. Destructive fastboot calls return a `would_run`
preview by default.

## Concise results

`fastboot_flash`, `fastboot_erase`, `fastboot_raw`, `adb_push`, `adb_pull` and
`adb_file` return a short `summary` instead of the raw progress log, e.g.
`vendor sparse 5/5 31.671s OKAY`, plus `ok` and `elapsed_ms`. Pass
`verbose=true` to include the clipped raw stdout/stderr; failures always include
the raw tail and command. `run_steps` applies the same compaction per step.
Log-producing tools (`adb_logcat`, `dmesg`, `dumpsys`, `ui_dump`) keep their full
output — that output is the result.

## Registered with these agents

MCP server entry (`adb-fastboot`) is installed in:

- `~/.config/opencode/opencode.json` → `mcp.servers`
- `~/.claude.json` → `mcpServers` (Claude Code)
- `~/.gemini/settings.json` → `mcpServers` (Gemini CLI)
- `~/.gemini/config/mcp_config.json` → `mcpServers` (Antigravity / agy)
- `~/.hermes/config.yaml` → `mcp_servers` (Hermes)

The skill is installed via `~/.agents/skills/adb-fastboot-mcp/SKILL.md`
(symlinked to this repo) and linked into the skills dirs of OpenCode, Claude,
Gemini, Antigravity, Hermes and Codex. Edits to `SKILL.md` propagate to all.

## Verify

`selftest.py` lists tools over MCP stdio without touching a device. With a
device attached it also runs read-only status/logcat checks.

```sh
.venv/bin/python selftest.py
```
