---
name: romtools
description: Complete workflow for the RomTools installation at /mnt/project/RomTools. Use whenever the user mentions romtools, repacking or unpacking Android partitions, EROFS / ext4 / F2FS images, gen_fs-config / gen_file-contexts autogeneration, sepolicy file-context fixes, or Android boot / vendor_boot / recovery image editing. Covers repacking unpacked project directories into /mnt/project/RomTools/output with no stray files.
---

# RomTools — complete workdir workflow

RomTools lives at `/mnt/project/RomTools`. Every operation is a bash function sourced from `tool/functions`. Read the non-negotiable rules first; they apply to all three operations below.

## Non-negotiable rules (read before doing anything)

1. **Unpacked files live only in `ROM_WORKDIR/<project>/...`.** Never create unpacked partition trees in `output/`, `input/`, `tool/tmp/`, `/tmp`, `$HOME`, or anywhere else. A new project means a new directory under `ROM_WORKDIR/`, created with the tool's `prj_create`/`prj_open` flow (not an ad-hoc `mkdir` somewhere).
2. **Packed output goes only to `/mnt/project/RomTools/output`.** In `tool/functions` this is `DIROUT=$DIRTOOLS/output`; export it explicitly on every repack. **Never** point `DIROUT` at `ROM_WORKDIR/output` — that is a stale pattern in the old `repack.sh` and creates stray output.
3. **No stray files.** One `.img` per repack, named `<partition>.<fstype>.img`. Verify with the filesystem checks in section 0.3 / section 4. Do not leave unpacked trees, temp images, or `build/` directories in `tool/`, `input/`, or the repo root.
4. **Autogenerate configs, never hand-write them.** `gen_fs-config` and `gen_file-contexts` (shipped in `tool/bin/`) generate the missing entries. Always run them first through the embedded repack flow. Only *after* autogen, if a generated context is wrong or does not exist in sepolicy, do a **targeted replacement inside the generated file**.
5. **Contexts that autogen adds must exist in sepolicy.** Validate only the newly added lines (section 2) against `<partition>/etc/selinux/*sepolicy.cil`. Pre-existing entries are presumed valid — do not rescan the whole file. The `tool/romtools.conf` default `--vbin` is `vendor_qti_init_shell_exec`, which does **not** exist on this vendor; replace it with an existing type such as `vendor_shell_exec` (or fix `vendor_odm_bin_contexts_def` in `romtools.conf` so future autogen emits the right one).
6. **Never use destructive git undo** (`git reset --hard`, `git clean -f`, `git checkout --`, forced restores) in this workdir. These are real ROM porting trees with unrelated uncommitted work. `git status` is also **not** a repack-verification tool here (see section 4).

## Use the `romtools` MCP — mandatory when available, manual fallback only

When the `romtools` MCP server is available, **use it** for RomTools discovery, configuration, repack and unpack. Do not hand-type the shell equivalents just because they are familiar. Manual shell commands in this skill are a **fallback** for agents/sessions with no MCP support, or when the MCP fails.

Discovery / config tools:
- `romtools_get_config()` — verify root, output dir, `romtools.conf` and `ready_for_execution`. Call this first.
- `romtools_set_workdir({path})` / `romtools_get_env()` / `romtools_set_env({...})` — root and env setup.
- `romtools_list_projects()` / `romtools_find_project({name})` — project disambiguation; on no match these return the available list and the choices.
- `romtools_set_project({name})` — **required after discovery**: selects the active project. Every repack/unpack tool refuses to run until this has been done, and refuses a `project` argument that does not match the active project. Re-run it when switching projects.
- `romtools_create_project({name, confirm: true})` — creates an empty project dir under `ROM_WORKDIR/` and selects it. Only after the user explicitly confirms; never auto-create.
- `romtools_list_partitions({project, fs})` — partitions plus config/context line counts (§0.3 baseline).
- `romtools_list_scripts()` / `romtools_find_script({keyword})` / `romtools_list_binaries()` — script and utility discovery.
- `romtools_list_output()` — output dir with full paths.

Execution tools:
- `romtools_repack_partition({project, partition, fs, compression?, legacy?})` — runs the correct repack function, sets `DIROUT` to `<root>/output`, autogen-first, and returns `output` (full path), `autogen_added_file_contexts`, `autogen_added_fs_config` and `missing_contexts`. Requires explicit `fs`; validates project/partition and compression before running.
- `romtools_unpack_image({project, image, fs?})` — detects fs with `blkid` if omitted, refuses to overwrite an existing tree, returns `unpacked_dir`.
- `romtools_list_boot_images({project})` — boot build trees and whether each contains its `<name>.img`.
- `romtools_unpack_boot_image({project, image})` — non-interactive bbootimg unpack into `<project>/boot/<name>`; preserves the source image and restores vbmeta.
- `romtools_repack_boot_image({project, name})` — non-interactive bbootimg pack, **signed by default**; an unsigned-only result is removed and reported as an error. Set `allow_unsigned: true` only if the user explicitly asked for unsigned.

The MCP enforces the mechanical rules (explicit project/fs, output only in `output/`, autogen-first, added-only context checks). This skill still supplies the **judgement** rules: ask on ambiguity, signed boot images, cleanup, and reporting the full output path. Never treat an MCP failure as permission to skip a rule — fall back to the documented shell flow and keep the same rules.

## Reporting the result (always)

When a RomTools execution finishes, end the response with the **full absolute output path** so the user can check it immediately. Exact format:

```
Repack done...
Output : /mnt/project/RomTools/output/system.erofs.img
```

- Use the real absolute path of the file actually produced — never a relative path or a placeholder.
- For other operations use the same pattern with the operation name (`Unpack done...`, `Boot repack done...`), always followed by `Output : <absolute path>`.
- Report the real filename, e.g. `<name>.signed.img` for a signed boot image (unsigned only when explicitly requested).

## Workdir map

| Path | Purpose |
|---|---|
| `/mnt/project/RomTools/` | tool root; source `tool/functions` from here |
| `tool/functions` | all repack/unpack/project functions |
| `tool/bin/` | `mkfs.erofs`, `extract.erofs`, `make_ext4fs`, `mkfs.f2fs`, `sload.f2fs`, `gen_fs-config`, `gen_file-contexts`, `payload`, … |
| `tool/bin/booteditor/bbootimg/bbootimg.jar` | cfig Android boot image editor (unpack/pack boot, vendor_boot, recovery) |
| `tool/romtools.conf` | `allocated_threads`, `vendor_odm_bin_contexts_def`, `other_bin_contexts_def`, `last_prj` |
| `input/` | source `.img` files for unpack / bootimg |
| `output/` | **only** place for packed `.img` (`DIROUT`) |
| `ROM_WORKDIR/<project>/erofs/` | unpacked EROFS trees + `config/` (`<part>_fs_config`, `<part>_file_contexts`, `<part>_fs_options`) |
| `ROM_WORKDIR/<project>/EXT4/`, `F2FS/`, `super/`, `boot/` | other unpacked trees (`u_bootdir` = `<project>/boot`); ext4 config files are nested in `config/<part>/` |
| `tool/custom_scripts/` | helper scripts (menu 10) — **list before use**, see §0.4; still obey rules above |

## 0. Pre-flight — resolve the request before any write

### 0.1 Resolve the project — never guess a vague name

Users say things like "repack oos system.img" while `ROM_WORKDIR/` contains `oos`, `oos15_old`, `oos16`, `XOS16PORT`, `XOS163`, … . Do **not** pick the first match, assume the short name, or silently expand it.

Call `romtools_find_project({name: "<what the user said>"})`, then `romtools_set_project({name: "<resolved project>"})` — execution tools are blocked until the project is selected. `romtools_list_projects()` shows everything. Without the MCP, fall back to:

```bash
find /mnt/project/RomTools/ROM_WORKDIR -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
```

- **One exact match** → state which project you found, then proceed.
- **Multiple matches or no exact match** → **ASK the user which project**. Show the candidate list. "Sounds like" is not permission to continue.
- **No match at all** → ask the user to either pick one of the available projects (`romtools_set_project`) or explicitly confirm creating a new project. If `ROM_WORKDIR/` is empty, ask whether to create the folder via `romtools_create_project({name, confirm: true})` or whether they will create it themselves. Never create silently — `create_project` requires `confirm: true` and the tool refuses without it.
- Also confirm the partition when it is abbreviated or ambiguous. If the user says `system.img`, the unpacked directory is `system` (strip `.img`) and the output will be `system.<fstype>.img`.

### 0.2 Resolve the target filesystem — honor what the user said

- User explicitly says **ext4** → use `repack_ext4_cmd`. Do **not** run `repack_erofs_cmd`.
- User explicitly says **f2fs** → use `repack_f2fs_cmd`.
- User explicitly says **erofs** (or asks for a `.erofs.img`) → use `repack_erofs_cmd`.
- No type stated → check which unpacked tree actually holds the partition: `<project>/erofs/<part>`, `<project>/EXT4/<part>`, `<project>/F2FS/<part>`. If exactly one exists, use it. If none or more than one → **ASK**. Never default to erofs.

Misread to avoid: "repack oos system to ext4" must produce `output/system.ext4.img` through the ext4 writer, not `output/system.erofs.img`.

### 0.3 Baseline snapshot (before the repack)

```bash
ls -lt /mnt/project/RomTools/output                          # baseline output
touch /tmp/opencode/romtools_repack_check                    # scratch marker, outside the repo
wc -l <project>/erofs/config/<part>_fs_config                # baseline line count (erofs)
wc -l <project>/erofs/config/<part>_file_contexts            # baseline line count (erofs)
```

`romtools_list_partitions({project, fs})` returns the config/context paths and their current line counts — use it for this baseline when the MCP is available. Note whether the config/context files already exist — that is fine. Autogen only fills gaps and **appends** new entries to the end of the file (verified behavior of both shipped tools), so those line counts let you validate only what was added. The marker and the validation temp file created later are scratch only — they must be removed in the cleanup step at the end (section 4).

### 0.4 Custom scripts — list before using one

`tool/custom_scripts/` holds helper scripts (main menu `10`). Scripts change over time and names are not always obvious — do not assume this skill enumerates them all. When a request refers to a script (e.g. "run the secpatch script", "normalize props", "merge tr partitions"), do this first:

Call `romtools_list_scripts()` (and `romtools_find_script({keyword})` for a name hint). Without the MCP, fall back to:

```bash
ls -1 /mnt/project/RomTools/tool/custom_scripts/
```

- Match by keyword over the **currently listed** names, then **read the candidate script before running it** (`head`/`sed` is enough) to confirm what it does and what arguments it needs.
- If more than one script matches, or none matches cleanly → **ASK the user which one**, showing the candidates. Do not guess. Example: "secpatch" currently maps to `update_secpatch.sh`, but confirm whenever several look plausible.
- Scripts often need concrete inputs (for example an exact old string and new string for a prop/patch replacement, or the expected patch behavior). **Ask the user for those values** — never invent them or assume a default.
- Scripts obey the same rules as everything else: unpacked files stay under `ROM_WORKDIR/<project>/`, packed output only in `output/`, no stray files.

## Environment bootstrap (same shell as the repack)

```bash
cd /mnt/project/RomTools && source tool/functions
prj_setenv /mnt/project/RomTools/ROM_WORKDIR/oos   # sets PRJDIR, erofsdir, ext4dir, f2fsdir, superdir, u_bootdir
set_usable_threads                                 # exports usable_threads from romtools.conf (16 here)
set_bin_files_autogencontext                       # exports --vbin / --sbin defaults from romtools.conf
export DIROUT=/mnt/project/RomTools/output
```

`prj_setenv <project_dir>` expands to `<project_dir>/erofs`, `<project_dir>/EXT4`, `<project_dir>/F2FS`, `<project_dir>/super`, `<project_dir>/boot`. The interactive equivalent is menu `p` (switch project) or starting `romtools` and opening a project.

## 1. Repack an unpacked partition → `@output/`

Pick the writer from the section 0.2 decision — never silently substitute erofs:

| Target | Function | Output | Config files |
|---|---|---|---|
| erofs | `repack_erofs_cmd <part> <compression> <legacy> [-q]` | `$DIROUT/<part>.erofs.img` | `<project>/erofs/config/<part>_fs_config`, `<part>_file_contexts` |
| ext4 | `repack_ext4_cmd <part> <size>` | `$DIROUT/<part>.ext4.img` | `<project>/EXT4/config/<part>/<part>_fs_config`, `<part>_file_contexts` |
| f2fs | `repack_f2fs_cmd <part>` | `$DIROUT/<part>.f2fs.img` | `<project>/F2FS/config/<part>_fs_config`, `<part>_file_contexts` |

For ext4, `<size>` in bytes comes from `<project>/EXT4/config/<part>/<part>_size.txt` (the same-size flow) or from the user — do not invent a size. Example: `repack_ext4_cmd system "$(cat "$ext4dir/config/system/system_size.txt")"`. For f2fs the size is computed from the folder size by the function.

**EROFS arguments** (for `repack_erofs_cmd` only):

- `$1` — partition directory name under `$erofsdir/` (e.g. `vendor`, `system`, `product`, `system_ext`).
- `$2` — compressor: `lz4`, `lz4hc[,level]`, `lzma[,level]`, `deflate[,level]`, `zstd[,level]`. Device convention: **non-legacy `lz4hc` (level 9 by default, i.e. `lz4hc` == `lz4hc,9`)**.
- `$3` — `0` = modern kernel (no `-E legacy-compress`), `1` = legacy-compress for old kernels. Use `0` here.
- `$4` — optional flags forwarded to **both** `gen_fs-config` and `gen_file-contexts`; pass `-q` for quiet. Omit to see their output.

The function runs the whole flow in order: `gen_fs-config` → `gen_file-contexts` → `mkfs.erofs`. With the MCP available, call `romtools_repack_partition({project, partition, fs, compression, legacy})` instead of the shell below; it returns the verified output path and only the autogen-added entries. Manual fallback (verified test command for this workdir):

```bash
cd /mnt/project/RomTools && source tool/functions && \
prj_setenv /mnt/project/RomTools/ROM_WORKDIR/oos && \
set_usable_threads && set_bin_files_autogencontext && \
export DIROUT=/mnt/project/RomTools/output && \
repack_erofs_cmd vendor lz4hc 0 -q
# output: /mnt/project/RomTools/output/vendor.erofs.img
```

The interactive counterpart is menu `4` → "Repack EXT4/F2FS/EROFS image", which asks the same compression/legacy questions and produces `$DIROUT/<part>.erofs.img`.

## 2. Working with EROFS partitions

**Unpack.** Put `<name>.img` in `input/`. With the MCP, call `romtools_unpack_image({project, image, fs?})` — it detects the fs type with `blkid` when omitted and refuses to overwrite an existing tree. Manual fallback: menu `4` → "Unpack EXT4/F2FS/EROFS image", or:

```bash
tool/bin/extract.erofs -T${usable_threads} -i input/<name>.img -o "$erofsdir" -x
```

**Autogen configs (always first).** Files: `$erofsdir/config/<part>_fs_config` and `$erofsdir/config/<part>_file_contexts`. These are already invoked by `repack_erofs_cmd` with `-q`; standalone form:

```bash
$BIN/gen_fs-config -t $usable_threads -p "$erofsdir/<part>" \
  -c "$erofsdir/config/<part>_fs_config" -q
$BIN/gen_file-contexts -t $usable_threads -a \
  --vbin "$vendor_odm_bin_fcontexts" --sbin "$other_bin_fcontexts" \
  -f erofs -p "$erofsdir/<part>" \
  -c "$erofsdir/config/<part>_file_contexts" -q
```

`-a` = generate all missing contexts (`-b` = only `/bin`); `-q` = quiet. Both tools only **add** missing entries, appended at the end of the file, so they are safe to re-run on a complete config.

**Validate only what autogen just added** — scanning the full file wastes context and the existing entries are presumed valid. Use the baseline line counts from section 0.3:

```bash
FC="$erofsdir/config/<part>_file_contexts"
CFG="$erofsdir/config/<part>_fs_config"
OLD_CTX=<count from 0.3>; OLD_CFG=<count from 0.3>

echo "--- newly added fs_config entries ---"
tail -n +$((OLD_CFG+1)) "$CFG"
echo "--- newly added file_contexts entries ---"
tail -n +$((OLD_CTX+1)) "$FC" | tee /tmp/opencode/<part>_new_contexts

while read -r line; do
  ctx=$(grep -oE 'u:object_r:[a-zA-Z0-9_]+:s0' <<<"$line" | sed 's/u:object_r://;s/:s0//')
  [ -z "$ctx" ] && continue
  grep -qrwE "$ctx" "$erofsdir/<part>"/etc/selinux/*sepolicy.cil 2>/dev/null \
    || echo "MISSING: $ctx"
done < /tmp/opencode/<part>_new_contexts
```

- If the line counts did not change, autogen added nothing — skip validation entirely.
- If an added context is `MISSING`, confirm it is not a platform type from `plat_sepolicy.cil` (not unpacked here), then do a targeted `sed` on the generated file and re-run `repack_erofs_cmd`. Only added lines are in scope; never rewrite pre-existing entries.
- Use whole-word grep (`-w`), not `\(type ...\)`: this vendor's CIL declares types inside `typeattributeset` blocks, so a `(type X)` regex falsely reports existing types as missing.

Do not author `<part>_fs_config` / `<part>_file_contexts` from scratch, and do not replace a bad context with another one that is also absent from the CIL.

**mkfs details (what `repack_erofs_cmd` builds):**

```bash
tool/bin/mkfs.erofs -z<compression> -T <epoch> --workers=$usable_threads \
  --mount-point=/<part> \
  --fs-config-file="$erofsdir/config/<part>_fs_config" \
  --file-contexts="$erofsdir/config/<part>_file_contexts" \
  "$DIROUT/<part>.erofs.img" "$erofsdir/<part>/"
```

`$erofsdir/config/<part>_fs_options` records the previous build's mkfs line (UUID + options). It is a reference for matching a stock image, not a file to hand-edit. `-T` is the build time; `-U` UUID is not passed by the repack functions. Legacy flag `1` adds `-E legacy-compress` — keep it `0` for this device.

## 3. Android boot image tools (cfig / bbootimg)

Source images: put `<name>.img` (boot, vendor_boot, recovery, dtbo, vbmeta) in `input/`. Unpacked trees: `$u_bootdir/<name>/` = `ROM_WORKDIR/<project>/boot/<name>/`. Packed output: `$DIROUT/<name>.signed.img` or `<name>.unsigned.img`.

Interactive: main menu `0` → `1` Repack Image / `2` Unpack Image. Functions `unpack_bootimg` / `repack_bootimg` wrap `java -jar tool/bin/booteditor/bbootimg/bbootimg.jar unpack|pack`. With the MCP, use `romtools_list_boot_images`, `romtools_unpack_boot_image` and `romtools_repack_boot_image` instead — they are non-interactive and carry the same signed-only rule.

```bash
cd /mnt/project/RomTools && source tool/functions
prj_setenv /mnt/project/RomTools/ROM_WORKDIR/bootimg   # u_bootdir = .../bootimg/boot
export DIROUT=/mnt/project/RomTools/output
# then use menu 0, or call unpack_bootimg / repack_bootimg directly
```

- **Unpack** picks a `.img` from `input/`, runs `bbootimg.jar unpack`, and moves the result to `$u_bootdir/<name>/` — that folder **is** the build tree and contains the original `<name>.img` plus the extracted contents. The source image is consumed from `input/`.
- **Repack** picks a folder in `$u_bootdir/`. It expects `<name>/<name>.img` and the build tree in that same folder, runs `bbootimg.jar pack`, and restores the build tree. `vbmeta.img` is moved aside automatically during packing.
- **Signed images only, unless told otherwise.** `repack_bootimg` writes `$DIROUT/<name>.signed.img` when the editor produces a signed image; if it only gets a `.clear` image it falls back to `$DIROUT/<name>.unsigned.img`. Always verify the `.signed.img` exists and report that path. If only `.unsigned.img` appears, **stop and tell the user** — do not silently deliver an unsigned image, and do not change keys/build-tree signing config to force a signature without asking. Build unsigned **only** when the user explicitly asks for unsigned.
- Never let a `build/` directory or intermediate `.img` remain in `tool/bin/booteditor/` — `unpack_bootimg`/`repack_bootimg` move them back; verify `tool/bin/booteditor/` has only its shipped files afterwards.

## 4. Strict rules checklist (erofs / ext4 / bootimg — all operations)

Before running:

- [ ] `romtools_get_config` reported `ready_for_execution: true` (root + active project set); project was resolved via `romtools_find_project`/`romtools_list_projects` and selected with `romtools_set_project` (or the shell fallback only if MCP unavailable), and **asked about** when vague or ambiguous — never guessed.
- [ ] Repack/unpack was executed through the MCP tools when available; manual shell was used only as a fallback.
- [ ] Target filesystem came from the user's explicit request or from the single matching unpacked tree — never defaulted to erofs.
- [ ] If a custom script was needed, `tool/custom_scripts/` was listed first, the candidate was read, ambiguity was asked about, and required inputs (old/new strings etc.) came from the user.
- [ ] Target unpacked tree is under `/mnt/project/RomTools/ROM_WORKDIR/<project>/...` — not `input/`, `output/`, `tool/tmp/`, `/tmp`, or `$HOME`.
- [ ] `export DIROUT=/mnt/project/RomTools/output` is set explicitly; nothing will write `ROM_WORKDIR/output` or a project-local `output/`.
- [ ] For EROFS/ext4/F2FS: autogen (`gen_fs-config` + `gen_file-contexts`) will run before `mkfs`/`make_ext4fs`/`sload.f2fs` — via `repack_*_cmd` or explicitly. No hand-written config/context files.
- [ ] Baseline line counts recorded for the config/context files; only newly added entries will be validated.

After running:

- [ ] Exactly one new `.img` in `/mnt/project/RomTools/output`, named `<partition>.<fstype>.img` — verify with `ls -lt /mnt/project/RomTools/output`.
- [ ] No new unpacked directories or leftover `build/` / temp `.img` outside `ROM_WORKDIR/<project>/`.
- [ ] Boot image repack produced `<name>.signed.img` — via `romtools_repack_boot_image` with default signed enforcement (or shell fallback); unsigned only if explicitly requested.

Use a **filesystem** check for stray output, not git:

```bash
find /mnt/project/RomTools -newer /tmp/opencode/romtools_repack_check \
  -not -path '*/ROM_WORKDIR/*' -not -path '*/output/*' \
  -not -path '*/input/*' -not -path '*/.git/*' -print
# expected: no output. Anything listed was created by this run outside the allowed trees.
```

### Cleanup (do this last, after all checks)

- [ ] Scratch marker and validation temp file from this run removed.

Remove the scratch files this run created. Substitute the real partition name for `<part>`:

```bash
rm -f /tmp/opencode/romtools_repack_check \
      /tmp/opencode/<part>_new_contexts
```

Do not delete anything else in `/tmp/opencode` — other sessions may own files there. Only the marker and the `*_new_contexts` file from this repack are yours. The line counts from section 0.3 were recorded in the agent's notes, not as files, so they need no cleanup.

**Do not run `git -C /mnt/project/RomTools status --short` as part of repack verification.** RomTools is a git-tracked main repo whose `ROM_WORKDIR/` holds 10 GB+ of unpacked ROMs (partly tracked, partly untracked) and whose `output/` is gitignored, so `git status` just dumps unrelated deleted/untracked files and tells you nothing about where the repack wrote. The only place git status belongs in this workflow is the global rule for inspecting changes **before an undo/revert** — never for repack output checks.
