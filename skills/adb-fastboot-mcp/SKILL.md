---
name: adb-fastboot-mcp
description: Use the local adb-fastboot MCP server for structured, chainable adb/fastboot work — reboot to bootloader/fastboot, logcat, shell (including su), flash/erase, and multi-step chains across device modes. Use instead of hand-writing long adb/fastboot shell commands, when a reboot must be followed by fastboot actions, or when an adb command failed because of quoting/session mistakes.
---

# adb-fastboot MCP

MCP server name: `adb-fastboot`. It wraps `adb` and `fastboot` as typed tools and
can chain them in one call, including the adb→fastboot transition (it polls both
`adb devices` and `fastboot devices`). Shell strings are never used — every call
is an argv list.

## Start here

1. `device_probe({timeout:3})` — fast parallel adb+fastboot check: `state`, both
   transports' device lists, errors and timings, last known mode and a short hint.
   States: `adb`, `fastboot`, `adb_recovery`, `adb_unauthorized`, `adb_offline`,
   `mid_reboot_or_offline` (fastboot probe hung), `transport_error`, `no_device`.
   `device_status` is the slower sibling; it returns a compact mode/serial
   summary by default (pass `verbose:true` for full device lists and history).
   Both keep the output small — the `…` in this client's output comes from
   `JSON.stringify` wrapping the tool's JSON string, not from corruption.
2. If several devices are attached, `set_target({serial})`. On X6739 the identity
   is serial `102562537K104189`; ignore reported model/fingerprint props.
3. Read `/mnt/project/X6739/Status.md` and claim the device before touching adb;
   release it when done. No partition writes and no SELinux mode changes.

## Picking a tool

| Need | Tool |
|---|---|
| Device lists / who is attached | `device_probe`, `adb_devices`, `fastboot_devices`, `device_status` |
| App logs | `adb_logcat({lines, tag, priority, clear})` |
| Kernel log | `dmesg({lines, grep, clear, su:true})` — device-side grep/tail |
| Service state | `dumpsys({service:"battery"\|"window"\|..., args, grep, lines})` |
| One shell command | `adb_shell({command, su})` |
| Root command | `su_shell({command})` |
| Read the screen | `ui_dump({clickable_only})` — nodes with text, ids, tap centers |
| Touch / type | `ui_input({action:"tap"\|"swipe"\|"long_press"\|"text"\|"key", x, y, x2, y2, duration, value})` |
| Tap by label | `ui_click({text, resource_id, content_desc})` — dumps UI, matches, taps in one call |
| Push a file | `adb_push({local, remote})` — relative host path under `ADB_FASTBOOT_WORKDIR` |
| Pull a file | `adb_pull({remote, local})` — local defaults to `ADB_FASTBOOT_WORKDIR` |
| Install an APK | `adb_file({action:"install", local})` |
| Reboot out of Android | `adb_reboot({target:"android"\|"bootloader"\|"fastboot"\|"recovery"\|"edl", wait:true, stable_for:10})` |
| Wait after a mode change | `wait_for_device({mode:"fastboot"\|"adb", timeout, stable_for})` |
| Bootloader variables | `fastboot_getvar({variable:"all"})` |
| Flash / erase | `fastboot_flash`, `fastboot_erase` (dry-run unless `confirm:true`) |
| Leave fastboot | `fastboot_reboot({target, wait})` |
| Any other fastboot subcommand | `fastboot_raw({args:["oem","device-info"]})` |
| Multiple steps | `run_steps({steps:[...]})` |

## Chaining (the main reason to use this server)

`run_steps` takes a list of `{tool, args, label?}`; allowed tools are the same as
above. It returns per-step `ok`, `result`, `elapsed_ms`, stops on first failure by
default, and ends with a device-mode snapshot. `adb_reboot({wait:true})` already
waits for fastboot, so a typical boot-swap chain is:

This replaces the manual `adb reboot fastboot && sleep 30 && fastboot flash
vendor vendor.erofs.img && fastboot reboot`: `wait:true` waits for the device to
appear in fastboot, `stable_for:10` keeps polling until the fastbootd connection
has settled, and `fastboot_reboot(wait:true)` waits for adb to come back. No
fixed sleep, no relying on a stuck `fastboot devices` call.

```json
{
  "steps": [
    {"tool":"adb_reboot",      "args":{"target":"fastboot","wait":true,"stable_for":10}, "label":"enter fastboot"},
    {"tool":"device_probe",    "args":{"timeout":3}},
    {"tool":"fastboot_getvar", "args":{"variable":"product"}},
    {"tool":"fastboot_flash",  "args":{"partition":"vendor","file_path":"vendor.erofs.img","confirm":true}},
    {"tool":"fastboot_reboot", "args":{"target":"","wait":true}, "label":"back to Android"}
  ]
}
```

`vendor.erofs.img` resolves against `ADB_FASTBOOT_WORKDIR`
(`/mnt/project/RomTools/output` by default), so short filenames work.

Dry-run a flash first: omit `confirm` (or set `false`) to get a `would_run`
string without executing. Only set `confirm:true` when the operator has
authorized the flash.

## Conventions baked into the tools

- `su:true` builds one `su -c '<command>'` **single adb argument**. On this ROM,
  splitting `su -c` across adb arguments breaks root (`syntax error`); do not
  chain multiple privileged commands inside one call — use separate `adb_shell`
  calls.
- `fastboot_flash` requires an existing image file and a partition name matching
  `[A-Za-z0-9_.-]+`. If `ADB_FASTBOOT_FLASH_DIRS` is set, paths outside it are
  rejected before execution.
- With multiple devices and no pinned serial, tools error rather than guess.
- Flash/erase/push/pull results are compact: `summary` like `vendor sparse 5/5 31.671s OKAY`, plus `ok` and `elapsed_ms`. Add `verbose:true` only when raw output is needed; failures always include the error tail. `run_steps` compacts each step the same way. Log tools (`adb_logcat`, `dmesg`, `dumpsys`, `ui_dump`) keep full output.
- Other output is clipped (12 KB per call, 2 KB per chain step) to keep token use low.

## Verify the MCP itself

`/mnt/project/romtools-skill/mcp/adb-fastboot/selftest.py` lists all tools over
MCP stdio and runs read-only checks. Use it when the tools do not show up in the
client.
