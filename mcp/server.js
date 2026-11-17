#!/usr/bin/env node
'use strict';

// Minimal dependency-free MCP stdio server for RomTools discovery/configuration.
// Tools are intentionally read-mostly: it never runs mkfs or modifies partitions.
// It only locates the RomTools install, sets its workdir/env, and lists what exists.

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { execFileSync } = require('child_process');

const CONFIG_PATH = path.join(__dirname, 'config.json');
const REQUIRED = ['tool/functions', 'tool/bin', 'input', 'output', 'ROM_WORKDIR'];

function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } catch {
    return {};
  }
}

function saveConfig(cfg) {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2) + '\n', 'utf8');
}

function root() {
  return (process.env.ROMTOOLS_ROOT || loadConfig().root || '').replace(/\/+$/, '');
}

function validateRoot(candidate) {
  const r = String(candidate || '').trim().replace(/\/+$/, '');
  if (!r) return { valid: false, root: r, missing: ['<empty>'], reason: 'no RomTools root configured' };
  if (!path.isAbsolute(r)) return { valid: false, root: r, missing: [r], reason: 'root must be an absolute path' };
  const missing = REQUIRED.filter(p => !fs.existsSync(path.join(r, p)));
  return {
    valid: missing.length === 0,
    root: r,
    missing,
    reason: missing.length ? 'path does not look like a RomTools install' : 'ok'
  };
}

function readConf(r) {
  const out = {};
  const p = path.join(r, 'tool', 'romtools.conf');
  if (!fs.existsSync(p)) return out;
  for (const line of fs.readFileSync(p, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^([A-Za-z0-9_]+)=(.*)$/);
    if (m) out[m[1]] = m[2];
  }
  return out;
}

function writeConf(r, updates) {
  const p = path.join(r, 'tool', 'romtools.conf');
  if (!fs.existsSync(p)) throw new Error('tool/romtools.conf not found');
  const keys = Object.keys(updates);
  if (!keys.length) return readConf(r);
  let text = fs.readFileSync(p, 'utf8');
  for (const k of keys) {
    const re = new RegExp('^' + k + '=.*$', 'm');
    const replacement = k + '=' + updates[k];
    text = re.test(text) ? text.replace(re, replacement) : text.replace(/\r?\n?$/, '\n' + replacement + '\n');
  }
  fs.writeFileSync(p, text, 'utf8');
  return readConf(r);
}

function dirsUnder(p) {
  if (!fs.existsSync(p)) return [];
  return fs.readdirSync(p, { withFileTypes: true })
    .filter(e => e.isDirectory())
    .map(e => e.name)
    .sort();
}

function safeProject(name) {
  const n = String(name || '');
  if (!n || n.includes('/') || n.includes('..') || n.includes('\\')) return null;
  return n;
}

function lineCount(file) {
  if (!fs.existsSync(file)) return null;
  const s = fs.readFileSync(file, 'utf8');
  return s.split(/\r?\n/).length - (s.endsWith('\n') ? 1 : 0);
}

function configReport(r, project, fsType, part) {
  const base = path.join(r, 'ROM_WORKDIR', project);
  const dirFor = { erofs: 'erofs', ext4: 'EXT4', f2fs: 'F2FS' };
  const tree = path.join(base, dirFor[fsType] || 'erofs');
  const cfg = path.join(tree, 'config');
  const nested = fsType === 'ext4' ? path.join(cfg, part) : cfg;
  const fc = path.join(nested, part + '_file_contexts');
  const fs_ = path.join(nested, part + '_fs_config');
  return {
    file_contexts: fs.existsSync(fc) ? fc : null,
    file_contexts_lines: lineCount(fc),
    fs_config: fs.existsSync(fs_) ? fs_ : null,
    fs_config_lines: lineCount(fs_)
  };
}

function treeFor(root, project, fsType) {
  const map = { erofs: 'erofs', ext4: 'EXT4', f2fs: 'F2FS' };
  return path.join(root, 'ROM_WORKDIR', project, map[fsType]);
}

function resolveProjectStrict(root, name) {
  const all = dirsUnder(path.join(root, 'ROM_WORKDIR'));
  const n = String(name || '').toLowerCase();
  const exact = all.filter(p => p.toLowerCase() === n);
  if (exact.length === 1) return exact[0];
  const partial = all.filter(p => p.toLowerCase().includes(n));
  throw new Error(
    'Ambiguous or unknown project "' + name + '". Candidates: ' + (partial.join(', ') || 'none') +
    '. ASK the user which project is meant before doing any work.'
  );
}

function runBash(root, script, extraEnv) {
  try {
    return execFileSync('bash', ['-c', script], {
      cwd: root,
      env: Object.assign({}, process.env, extraEnv || {}),
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
      timeout: 0
    });
  } catch (e) {
    const detail = String(e.stderr || e.stdout || e.message || '').trim();
    throw new Error('RomTools command failed: ' + detail.slice(-4000));
  }
}

function tailLines(text, n) {
  const lines = String(text || '').split(/\r?\n/).filter(Boolean);
  return lines.slice(-n);
}

function addedLines(file, before) {
  if (!file || !fs.existsSync(file)) return [];
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  if (lines[lines.length - 1] === '') lines.pop();
  return before == null ? lines : lines.slice(before);
}

function missingContexts(lines, partDir) {
  const policyDir = path.join(partDir, 'etc', 'selinux');
  if (!fs.existsSync(policyDir)) return null; // cannot verify
  const tokens = new Set();
  for (const f of fs.readdirSync(policyDir)) {
    if (!/sepolicy\.cil$/.test(f)) continue;
    for (const t of fs.readFileSync(path.join(policyDir, f), 'utf8').split(/[^A-Za-z0-9_]+/)) {
      if (t) tokens.add(t);
    }
  }
  const missing = [];
  for (const line of lines) {
    const m = String(line).match(/u:object_r:([A-Za-z0-9_]+):s0/);
    if (m && !tokens.has(m[1]) && !missing.includes(m[1])) missing.push(m[1]);
  }
  return missing;
}

const INSTRUCTIONS = [
  'RomTools discovery/config tools. Prefer these over long shell pipelines.',
  'Use romtools_set_workdir first if no root is configured; romtools_get_config to verify.',
  'Use romtools_find_project / romtools_list_projects to resolve vague project names and ASK the user when ambiguous.',
  'Use romtools_find_script / romtools_list_scripts before running any custom script.',
  'repack_partition and unpack_image execute RomTools functions and enforce the rules: explicit project/fs, output only in <root>/output, autogen-first, and added-only context checks.',
  'Always tell the user the full absolute output path returned by a repack. Follow the romtools SKILL for all remaining policy.'
].join(' ');

const TOOLS = [
  {
    name: 'get_config',
    description: 'Show the configured RomTools root, whether it is a valid install, its output dir, and current romtools.conf values. Call this first.',
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'set_workdir',
    description: 'Set/verify the RomTools installation root (the directory containing tool/functions, tool/bin, input, output and ROM_WORKDIR). Validates before saving.',
    inputSchema: {
      type: 'object',
      properties: { path: { type: 'string', description: 'Absolute path to the RomTools root' } },
      required: ['path']
    }
  },
  {
    name: 'get_env',
    description: 'Read allocated_threads and the autogen --vbin/--sbin context defaults from tool/romtools.conf.',
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'set_env',
    description: 'Update romtools.conf values (allocated_threads, vendor_odm_bin_contexts_def, other_bin_contexts_def) without touching other settings.',
    inputSchema: {
      type: 'object',
      properties: {
        allocated_threads: { type: 'string', description: 'Thread count for autogen/mkfs, e.g. "16"' },
        vendor_odm_bin_contexts_def: { type: 'string', description: 'Context for vendor/odm /bin autogen' },
        other_bin_contexts_def: { type: 'string', description: 'Context for other /bin autogen' }
      }
    }
  },
  {
    name: 'list_projects',
    description: 'List every project directory under ROM_WORKDIR with the partition trees it contains. Replaces the long find pipeline.',
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'find_project',
    description: 'Resolve a possibly vague project name. Returns an exact match when unique, otherwise the candidate list so the agent must ask the user.',
    inputSchema: {
      type: 'object',
      properties: { name: { type: 'string', description: 'Name or partial name the user said' } },
      required: ['name']
    }
  },
  {
    name: 'list_partitions',
    description: 'List unpacked partitions for a project/filesystem, with config/context file presence and line counts (baseline for added-only validation).',
    inputSchema: {
      type: 'object',
      properties: {
        project: { type: 'string' },
        fs: { type: 'string', enum: ['erofs', 'ext4', 'f2fs'], description: 'Defaults to erofs' }
      },
      required: ['project']
    }
  },
  {
    name: 'list_scripts',
    description: 'List the custom scripts currently available in tool/custom_scripts. Call before using any script.',
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'find_script',
    description: 'Match a keyword against currently available custom scripts. Returns candidates; ask the user if more than one matches.',
    inputSchema: {
      type: 'object',
      properties: { keyword: { type: 'string' } },
      required: ['keyword']
    }
  },
  {
    name: 'list_output',
    description: 'List packed images in the RomTools output directory with size and modified time.',
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'repack_partition',
    description: 'Execute a RomTools repack of an unpacked partition. Requires explicit project, partition and fs (erofs/ext4/f2fs); never guesses. Runs embedded autogen, writes only to <root>/output, and returns the full output path plus only newly added config/context lines and any missing sepolicy contexts.',
    inputSchema: {
      type: 'object',
      properties: {
        project: { type: 'string' },
        partition: { type: 'string' },
        fs: { type: 'string', enum: ['erofs', 'ext4', 'f2fs'] },
        compression: { type: 'string', description: 'erofs only; default lz4hc (level 9)' },
        legacy: { type: 'string', description: 'erofs only; "0" default, "1" for old kernels' }
      },
      required: ['project', 'partition', 'fs']
    }
  },
  {
    name: 'unpack_image',
    description: 'Unpack an image from input/ into the chosen project tree in ROM_WORKDIR. Detects erofs/ext4/f2fs with blkid unless fs is given. Refuses to overwrite an existing unpacked folder. Note: RomTools consumes the source image from input/ after a successful unpack.',
    inputSchema: {
      type: 'object',
      properties: {
        project: { type: 'string' },
        image: { type: 'string', description: 'Image filename in input/, with or without .img' },
        fs: { type: 'string', enum: ['erofs', 'ext4', 'f2fs'] }
      },
      required: ['project', 'image']
    }
  },
  {
    name: 'list_binaries',
    description: 'List the executable utilities shipped in tool/bin (mkfs.erofs, extract.erofs, gen_fs-config, bbootimg.jar, etc.).',
    inputSchema: { type: 'object', properties: {} }
  }
];

function handle(name, args) {
  const r = root();
  const need = () => {
    const v = validateRoot(r);
    if (!v.valid) throw new Error('RomTools root not configured or invalid: ' + v.reason + '. Use romtools_set_workdir.');
    return v.root;
  };

  switch (name) {
    case 'get_config': {
      const v = validateRoot(r);
      const conf = v.valid ? readConf(r) : {};
      return {
        configured_root: r || null,
        source: process.env.ROMTOOLS_ROOT ? 'environment ROMTOOLS_ROOT' : (loadConfig().root ? 'mcp config.json' : 'none'),
        valid_install: v.valid,
        missing: v.missing,
        output_dir: v.valid ? path.join(r, 'output') : null,
        romtools_conf: conf
      };
    }
    case 'set_workdir': {
      const v = validateRoot(args.path);
      if (!v.valid) return { accepted: false, reason: v.reason, missing: v.missing, checked: v.root };
      const cfg = loadConfig();
      cfg.root = v.root;
      saveConfig(cfg);
      return { accepted: true, root: v.root, output_dir: path.join(v.root, 'output'), missing: [] };
    }
    case 'get_env':
      return { root: need(), romtools_conf: readConf(r) };
    case 'set_env': {
      need();
      const allowed = ['allocated_threads', 'vendor_odm_bin_contexts_def', 'other_bin_contexts_def'];
      const updates = {};
      for (const k of allowed) if (args[k] !== undefined) updates[k] = String(args[k]);
      return { root: r, updated: updates, romtools_conf: writeConf(r, updates) };
    }
    case 'list_projects': {
      const base = path.join(need(), 'ROM_WORKDIR');
      return {
        root: r,
        workdir: base,
        projects: dirsUnder(base).map(p => {
          const d = path.join(base, p);
          return {
            name: p,
            trees: dirsUnder(d).filter(t => ['erofs', 'EXT4', 'F2FS', 'boot', 'super'].includes(t))
          };
        })
      };
    }
    case 'find_project': {
      const base = path.join(need(), 'ROM_WORKDIR');
      const all = dirsUnder(base);
      const n = String(args.name || '').toLowerCase();
      const exact = all.filter(p => p.toLowerCase() === n);
      const partial = all.filter(p => p.toLowerCase().includes(n));
      const matches = exact.length ? exact : partial;
      return {
        query: args.name,
        exact_match: exact.length === 1 ? exact[0] : null,
        candidates: matches,
        ambiguous: matches.length > 1,
        found: matches.length > 0,
        instruction: matches.length === 1
          ? 'Confirm this project with the user, then proceed.'
          : 'ASK the user which project is meant before doing any work.'
      };
    }
    case 'list_partitions': {
      const project = safeProject(args.project);
      if (!project) throw new Error('invalid project name');
      const fsType = args.fs || 'erofs';
      if (!['erofs', 'ext4', 'f2fs'].includes(fsType)) throw new Error('fs must be erofs, ext4 or f2fs');
      const dirFor = { erofs: 'erofs', ext4: 'EXT4', f2fs: 'F2FS' };
      const tree = path.join(need(), 'ROM_WORKDIR', project, dirFor[fsType]);
      const parts = dirsUnder(tree).filter(p => p !== 'config');
      return {
        root: r,
        project,
        fs: fsType,
        tree,
        partitions: parts.map(p => ({ name: p, config: configReport(r, project, fsType, p) }))
      };
    }
    case 'list_scripts': {
      const dir = path.join(need(), 'tool', 'custom_scripts');
      return { root: r, dir, scripts: fs.readdirSync(dir, { withFileTypes: true }).filter(e => e.isFile()).map(e => e.name).sort() };
    }
    case 'find_script': {
      const dir = path.join(need(), 'tool', 'custom_scripts');
      const all = fs.readdirSync(dir, { withFileTypes: true }).filter(e => e.isFile()).map(e => e.name).sort();
      const k = String(args.keyword || '').toLowerCase();
      const candidates = all.filter(s => s.toLowerCase().includes(k));
      return {
        keyword: args.keyword,
        candidates,
        ambiguous: candidates.length > 1,
        instruction: candidates.length === 1
          ? 'Read this script before running it.'
          : 'ASK the user which script is meant; do not guess.'
      };
    }
    case 'list_output': {
      const out = path.join(need(), 'output');
      return {
        root: r,
        output_dir: out,
        images: fs.readdirSync(out, { withFileTypes: true })
          .filter(e => e.isFile() && /\.(img|bin)$/.test(e.name))
          .map(e => {
            const st = fs.statSync(path.join(out, e.name));
            return { name: e.name, size: st.size, modified: st.mtime.toISOString(), full_path: path.join(out, e.name) };
          })
          .sort((a, b) => b.modified.localeCompare(a.modified))
      };
    }
    case 'repack_partition': {
      const rootPath = need();
      const project = resolveProjectStrict(rootPath, args.project);
      const part = safeProject(args.partition);
      if (!part) throw new Error('invalid partition name');
      const fsType = args.fs;
      if (!fsType || !['erofs', 'ext4', 'f2fs'].includes(fsType)) {
        throw new Error('fs must be explicitly erofs, ext4 or f2fs — never default');
      }
      const tree = treeFor(rootPath, project, fsType);
      const partDir = path.join(tree, part);
      if (!fs.existsSync(partDir)) throw new Error('no unpacked partition at ' + partDir);
      const conf = readConf(rootPath);
      const before = configReport(rootPath, project, fsType, part);
      const comp = args.compression || 'lz4hc';
      const legacy = args.legacy || '0';
      if (!/^(lz4|lz4hc|lzma|deflate|zstd)(,([0-9]+|level=[0-9]+))?$/.test(comp)) {
        throw new Error('invalid compression "' + comp + '"; use lz4, lz4hc[,level], lzma[,level], deflate[,level] or zstd[,level]');
      }
      if (!/^[01]$/.test(legacy)) throw new Error('legacy must be "0" or "1"');
      let call, extra = {};
      if (fsType === 'erofs') {
        call = 'repack_erofs_cmd "$PART" "$COMP" "$LEGACY" -q';
      } else if (fsType === 'f2fs') {
        call = 'repack_f2fs_cmd "$PART"';
      } else {
        const sizeFile = path.join(rootPath, 'ROM_WORKDIR', project, 'EXT4', 'config', part, part + '_size.txt');
        if (!fs.existsSync(sizeFile)) {
          throw new Error('ext4 needs an explicit size; missing ' + sizeFile + '. Ask the user for the size in bytes.');
        }
        extra.SIZE = fs.readFileSync(sizeFile, 'utf8').trim();
        call = 'repack_ext4_cmd "$PART" "$SIZE"';
      }
      const script = [
        'set -o pipefail',
        'cd "$ROOT" || exit 1',
        'source tool/functions',
        'export PRJDIR="$ROOT/ROM_WORKDIR/$PROJECT"',
        'export erofsdir="$PRJDIR/erofs" ext4dir="$PRJDIR/EXT4" f2fsdir="$PRJDIR/F2FS" u_bootdir="$PRJDIR/boot" superdir="$PRJDIR/super"',
        'export DIROUT="$ROOT/output"',
        'export usable_threads="$THREADS"',
        'export vendor_odm_bin_fcontexts="$VBIN"',
        'export other_bin_fcontexts="$SBIN"',
        call + ' 2>&1'
      ].join('\n');
      const env = Object.assign({
        ROOT: rootPath, PROJECT: project, PART: part, COMP: comp, LEGACY: legacy,
        THREADS: conf.allocated_threads || '4',
        VBIN: conf.vendor_odm_bin_contexts_def || 'vendor_qti_init_shell_exec',
        SBIN: conf.other_bin_contexts_def || 'system_file'
      }, extra);
      const output = path.join(rootPath, 'output', part + '.' + fsType + '.img');
      let out;
      try {
        out = runBash(rootPath, script, env);
      } catch (e) {
        if (fs.existsSync(output) && fs.statSync(output).size === 0) fs.unlinkSync(output);
        throw e;
      }
      const after = configReport(rootPath, project, fsType, part);
      const addedCfg = addedLines(after.fs_config, before.fs_config_lines);
      const addedCtx = addedLines(after.file_contexts, before.file_contexts_lines);
      return {
        success: fs.existsSync(output),
        project, partition: part, fs: fsType,
        output,
        output_size: fs.existsSync(output) ? fs.statSync(output).size : null,
        autogen_added_fs_config: addedCfg,
        autogen_added_file_contexts: addedCtx,
        missing_contexts: addedCtx.length ? missingContexts(addedCtx, partDir) : [],
        tail: tailLines(out, 12)
      };
    }
    case 'unpack_image': {
      const rootPath = need();
      const project = resolveProjectStrict(rootPath, args.project);
      const image = String(args.image || '').replace(/\.img$/, '');
      if (!safeName(image)) throw new Error('invalid image name');
      const src = path.join(rootPath, 'input', image + '.img');
      if (!fs.existsSync(src)) throw new Error('image not found: ' + src);
      let fsType = args.fs;
      if (!fsType) {
        const detected = runBash(rootPath,
          'blkid -o value -s TYPE "$SRC" 2>/dev/null || sudo blkid -o value -s TYPE "$SRC" 2>/dev/null || true',
          { SRC: src }).trim();
        if (!['erofs', 'ext4', 'f2fs'].includes(detected)) {
          throw new Error('could not detect fs type (got "' + detected + '"); pass fs explicitly');
        }
        fsType = detected;
      }
      if (!['erofs', 'ext4', 'f2fs'].includes(fsType)) throw new Error('fs must be erofs, ext4 or f2fs');
      const tree = treeFor(rootPath, project, fsType);
      const target = path.join(tree, image);
      if (fs.existsSync(target)) throw new Error('refusing to overwrite existing unpacked tree: ' + target);
      const conf = readConf(rootPath);
      const script = [
        'cd "$ROOT" || exit 1',
        'source tool/functions',
        'export PRJDIR="$ROOT/ROM_WORKDIR/$PROJECT"',
        'export erofsdir="$PRJDIR/erofs" ext4dir="$PRJDIR/EXT4" f2fsdir="$PRJDIR/F2FS" u_bootdir="$PRJDIR/boot" superdir="$PRJDIR/super"',
        'export DIROUT="$ROOT/output"',
        'export usable_threads="$THREADS"',
        'export vendor_odm_bin_fcontexts="$VBIN"',
        'export other_bin_fcontexts="$SBIN"',
        'unpack_' + fsType + ' "$IMAGE" 2>&1'
      ].join('\n');
      const out = runBash(rootPath, script, {
        ROOT: rootPath, PROJECT: project, IMAGE: image,
        THREADS: conf.allocated_threads || '4',
        VBIN: conf.vendor_odm_bin_contexts_def || 'vendor_qti_init_shell_exec',
        SBIN: conf.other_bin_contexts_def || 'system_file'
      });
      return {
        success: fs.existsSync(target),
        project, image, fs: fsType,
        unpacked_dir: target,
        source_image_consumed: !fs.existsSync(src),
        tail: tailLines(out, 12)
      };
    }
    case 'list_binaries': {
      const dir = path.join(need(), 'tool', 'bin');
      return { root: r, dir, binaries: fs.readdirSync(dir, { withFileTypes: true }).filter(e => e.isFile()).map(e => e.name).sort() };
    }
    default:
      throw new Error('Unknown tool: ' + name);
  }
}

function respond(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, result }) + '\n');
}

function respondError(id, message) {
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, error: { code: -32000, message } }) + '\n');
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', line => {
  let req;
  try {
    req = JSON.parse(line);
  } catch {
    return;
  }
  if (req.id === undefined || req.id === null) return; // notification
  try {
    switch (req.method) {
      case 'initialize':
        respond(req.id, {
          protocolVersion: (req.params && req.params.protocolVersion) || '2025-06-18',
          capabilities: { tools: { listChanged: false } },
          serverInfo: { name: 'romtools', version: '1.0.0' },
          instructions: INSTRUCTIONS
        });
        break;
      case 'ping':
        respond(req.id, {});
        break;
      case 'tools/list':
        respond(req.id, { tools: TOOLS });
        break;
      case 'tools/call': {
        const args = (req.params && req.params.arguments) || {};
        try {
          const data = handle(req.params.name, args);
          respond(req.id, { content: [{ type: 'text', text: JSON.stringify(data, null, 2) }] });
        } catch (e) {
          respond(req.id, { isError: true, content: [{ type: 'text', text: 'romtools: ' + e.message }] });
        }
        break;
      }
      default:
        respondError(req.id, 'Method not found: ' + req.method);
    }
  } catch (e) {
    respondError(req.id, String(e && e.message || e));
  }
});
