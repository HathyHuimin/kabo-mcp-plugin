import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import {
  PLUGIN_ARTIFACT_FORMAT_VERSION,
  computeChecksum,
} from './artifact-checksum.mjs';

export const SOURCE_REPOSITORY = 'kabo-sh/creator-skills';
export const SOURCE_REPOSITORY_ALIASES = new Set([
  'kabo-sh/creator-skills',
  'kabo-sh/kabo-plugins-internal',
]);
// kabo-mcp-plugin 的 skill 目录原样打包（creator-skills#886；用户 2026-11-02 裁定不生成 manifest）。
// 包里只有 SKILL.md、references/、scripts/，名称、描述、依赖的 /mcp-pub 工具都在 SKILL.md frontmatter；
// 版本由发布方按线上版本往上接，信封的 manifest 一格为 null。读方：kabo-agent-service
// src/skill/registry-format.ts（PLUGIN_SOURCE_REPOSITORY / parsePluginSkillHeader），两边规则要一致。
export const PLUGIN_SOURCE_REPOSITORY = 'kabo-sh/kabo-mcp-plugin';
export const BUNDLE_FORMAT_VERSION = 1;
const SKILL_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const SEMVER = /^\d+\.\d+\.\d+$/;
const COMMIT = /^[0-9a-f]{40}$/;
const CHECKSUM = /^sha256:([0-9a-f]{64})$/;
const ALLOWED_TOP_LEVEL = new Set([
  'SKILL.md',
  'manifest.json',
  'agents',
  'assets',
  'references',
  'scripts',
]);
const SECRET_CONTENT = [
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
  /\bgh[pousr]_[A-Za-z0-9_]{20,}\b/,
  /\bGOCSPX-[A-Za-z0-9_-]{20,}\b/,
];

function fail(message) {
  throw new Error(`skill_registry_bundle_invalid: ${message}`);
}

export function stableJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function compareUtf8(left, right) {
  return Buffer.compare(Buffer.from(left, 'utf8'), Buffer.from(right, 'utf8'));
}

function assertManifest(manifest, id) {
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) fail('manifest must be an object');
  if (manifest.name !== id) fail(`manifest.name ${JSON.stringify(manifest.name)} must equal ${JSON.stringify(id)}`);
  if (!SEMVER.test(manifest.version ?? '')) fail(`manifest.version must be x.y.z, got ${JSON.stringify(manifest.version)}`);
  if (manifest.description !== undefined && typeof manifest.description !== 'string') fail('manifest.description must be a string');
  if (manifest.tags !== undefined && (!Array.isArray(manifest.tags) || manifest.tags.some((item) => typeof item !== 'string'))) {
    fail('manifest.tags must be an array of strings');
  }
  // Bundle format v1 predates dependency metadata, so structural reads must continue to parse
  // immutable legacy bundles. The release compatibility gate is intentionally stricter: missing
  // metadata is unverifiable and blocks publish/promotion, with break-glass available only for a
  // deliberate historical restore.
  if (manifest.min_plugin_version !== undefined && !SEMVER.test(manifest.min_plugin_version)) {
    fail(`manifest.min_plugin_version must be x.y.z, got ${JSON.stringify(manifest.min_plugin_version)}`);
  }
  if (manifest.required !== undefined && (!manifest.required || typeof manifest.required !== 'object' || Array.isArray(manifest.required))) {
    fail('manifest.required must be an object');
  }
  if (manifest.required?.server_api_min !== undefined && !SEMVER.test(manifest.required.server_api_min)) {
    fail(`manifest.required.server_api_min must be x.y.z, got ${JSON.stringify(manifest.required.server_api_min)}`);
  }
  if (manifest.required?.tools !== undefined && (!Array.isArray(manifest.required.tools) || manifest.required.tools.some((item) => typeof item !== 'string'))) {
    fail('manifest.required.tools must be an array of strings');
  }
  if (Array.isArray(manifest.required?.tools) && new Set(manifest.required.tools).size !== manifest.required.tools.length) {
    fail('manifest.required.tools cannot contain duplicates');
  }
  if (manifest.required?.connectors !== undefined) assertConnectorDeclaration(manifest.required.connectors);
  if (manifest.deferred_files !== undefined) assertDeferredDeclaration(manifest.deferred_files);
}

// `required.connectors` narrows what the platform shows a run of this Skill: the catalog filtered by
// skill_id lists only these operations, and search marks the Skill `connectors_ready` only when every
// one of them is implemented and ready. An empty list declares no dependency (no filtering).
const CONNECTOR_NAME = /^[a-z0-9][a-z0-9-]{0,63}$/;
const OPERATION_NAME = /^[a-z][a-z0-9_]{0,63}$/;
function assertConnectorDeclaration(connectors) {
  if (!Array.isArray(connectors)) fail('manifest.required.connectors must be an array');
  const seen = new Set();
  for (const entry of connectors) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) fail('manifest.required.connectors entries must be objects');
    const extra = Object.keys(entry).filter((key) => key !== 'connector_id' && key !== 'operations');
    if (extra.length) fail(`manifest.required.connectors entry has unknown keys: ${extra.join(', ')}`);
    if (typeof entry.connector_id !== 'string' || !CONNECTOR_NAME.test(entry.connector_id)) {
      fail(`manifest.required.connectors connector_id is invalid: ${JSON.stringify(entry.connector_id)}`);
    }
    if (seen.has(entry.connector_id)) fail(`manifest.required.connectors lists ${entry.connector_id} twice`);
    seen.add(entry.connector_id);
    if (!Array.isArray(entry.operations) || entry.operations.length === 0) {
      fail(`manifest.required.connectors ${entry.connector_id} needs a non-empty operations array`);
    }
    if (entry.operations.some((operation) => typeof operation !== 'string' || !OPERATION_NAME.test(operation))) {
      fail(`manifest.required.connectors ${entry.connector_id} has an invalid operation name`);
    }
    if (new Set(entry.operations).size !== entry.operations.length) {
      fail(`manifest.required.connectors ${entry.connector_id} lists an operation twice`);
    }
  }
}

// Deferred skill assets (cross-repo contract v1). The bundle still carries every file and the
// signature still covers every file; the server leaves the deferred set out of a download for a
// client new enough to fetch one file at a time. The server re-checks these rules and serves the
// full package when they fail, so a bad declaration is stopped here rather than silently ignored.
export const DEFERRED_FILE_MAX_BYTES = 65536;
const DEFERRED_ROOT = 'assets/';
const DEFERRED_FORBIDDEN = ['SKILL.md', 'manifest.json', 'scripts/', 'references/'];
// C0, DEL and C1 controls: the plugin client and the server refuse these in a deferred path too.
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f-\u009f]/;

function assertDeferredEntry(entry, field) {
  const where = `manifest.deferred_files.${field}`;
  if (typeof entry !== 'string' || entry.length === 0) fail(`${where} entries must be non-empty strings`);
  if (!entry.startsWith(DEFERRED_ROOT) || entry === DEFERRED_ROOT) {
    fail(`${where} entry must name a file or directory below ${DEFERRED_ROOT}: ${JSON.stringify(entry)}`);
  }
  const segments = (entry.endsWith('/') ? entry.slice(0, -1) : entry).split('/');
  if (CONTROL_CHARACTER.test(entry)) fail(`${where} entry contains a control character: ${JSON.stringify(entry)}`);
  if (entry.includes('\\') || entry.includes('\0') || segments.some((part) => part === '' || part === '.' || part === '..')) {
    fail(`${where} entry must be a normalized package-relative POSIX path: ${JSON.stringify(entry)}`);
  }
}

function assertDeferredDeclaration(declaration) {
  if (!declaration || typeof declaration !== 'object' || Array.isArray(declaration)) fail('manifest.deferred_files must be an object');
  const unknown = Object.keys(declaration).filter((key) => key !== 'include' && key !== 'exclude');
  if (unknown.length > 0) fail(`manifest.deferred_files has unknown keys: ${unknown.join(', ')}`);
  if (!Array.isArray(declaration.include) || declaration.include.length === 0) fail('manifest.deferred_files.include must be a non-empty array');
  if (declaration.exclude !== undefined && !Array.isArray(declaration.exclude)) fail('manifest.deferred_files.exclude must be an array');
  for (const field of ['include', 'exclude']) {
    const entries = declaration[field] ?? [];
    entries.forEach((entry) => assertDeferredEntry(entry, field));
    if (new Set(entries).size !== entries.length) fail(`manifest.deferred_files.${field} cannot contain duplicates`);
  }
}

function deferredEntryMatches(entry, filePath) {
  return entry.endsWith('/') ? filePath.startsWith(entry) : filePath === entry;
}

/** The deferred set D: files matching any include entry and no exclude entry, in UTF-8 path order. */
export function deferredPaths(manifest, paths) {
  const declaration = manifest?.deferred_files;
  if (declaration === undefined) return [];
  const exclude = declaration.exclude ?? [];
  return paths
    .filter((filePath) => declaration.include.some((entry) => deferredEntryMatches(entry, filePath)))
    .filter((filePath) => !exclude.some((entry) => deferredEntryMatches(entry, filePath)))
    .sort(compareUtf8);
}

function assertDeferredFiles(manifest, files) {
  const declaration = manifest.deferred_files;
  if (declaration === undefined) return;
  const paths = files.map((file) => file.path);
  for (const field of ['include', 'exclude']) {
    for (const entry of declaration[field] ?? []) {
      // A stale or misspelled entry is the usual way a declaration stops meaning what it says:
      // an exclude that matches nothing defers the very file it was written to keep.
      if (!paths.some((filePath) => deferredEntryMatches(entry, filePath))) {
        fail(`manifest.deferred_files.${field} entry matches no packaged file: ${entry}`);
      }
      if (field === 'exclude' && !declaration.include.some((include) => deferredEntryMatches(include, entry))) {
        fail(`manifest.deferred_files.exclude entry is not inside any include entry: ${entry}`);
      }
    }
  }
  const deferred = new Set(deferredPaths(manifest, paths));
  if (deferred.size === 0) fail('manifest.deferred_files defers no file; remove the field instead');
  const decoder = new TextDecoder('utf-8', { fatal: true });
  for (const file of files) {
    if (!deferred.has(file.path)) continue;
    if (CONTROL_CHARACTER.test(file.path)) fail(`deferred file path contains a control character: ${JSON.stringify(file.path)}`);
    if (!file.path.startsWith(DEFERRED_ROOT) || DEFERRED_FORBIDDEN.some((prefix) => file.path === prefix || file.path.startsWith(prefix))) {
      fail(`deferred file must stay in the download: ${file.path}`);
    }
    if (file.content.length > DEFERRED_FILE_MAX_BYTES) fail(`deferred file exceeds ${DEFERRED_FILE_MAX_BYTES} bytes: ${file.path}`);
    if (file.content.includes(0)) fail(`deferred file contains a NUL byte: ${file.path}`);
    try {
      decoder.decode(file.content);
    } catch {
      fail(`deferred file is not valid UTF-8: ${file.path}`);
    }
  }
}

function assertRuntimePath(relativePath) {
  const first = relativePath.split('/')[0];
  if (!ALLOWED_TOP_LEVEL.has(first)) fail(`non-runtime top-level path is not allowed: ${relativePath}`);
  if ((first === 'SKILL.md' || first === 'manifest.json') && relativePath !== first) {
    fail(`root runtime file cannot be a directory: ${relativePath}`);
  }
  if (relativePath === 'agents' || relativePath === 'assets' || relativePath === 'references' || relativePath === 'scripts') {
    fail(`directory entry cannot be packaged as a file: ${relativePath}`);
  }
}

function collectFiles(directory, base = directory) {
  const output = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    // README.md 是 GitHub 上的设计审计材料，不属于运行时 prompt/package。允许
    // Skill 根目录保存它，但不签进 bundle，避免把内部实验说明暴露给执行上下文。
    if (directory === base && entry.name === 'README.md' && entry.isFile()) continue;
    // Python may create these while contract tests run before the publisher. They are
    // machine-specific build artifacts, not Skill source, and must never change a bundle.
    if (entry.name === '__pycache__' && entry.isDirectory()) continue;
    if (entry.name.endsWith('.pyc') && entry.isFile()) continue;
    if (entry.name.startsWith('.')) fail(`dot path is not allowed: ${path.relative(base, path.join(directory, entry.name))}`);
    const absolute = path.join(directory, entry.name);
    const stat = fs.lstatSync(absolute);
    if (stat.isSymbolicLink()) fail(`symbolic link is not allowed: ${path.relative(base, absolute)}`);
    if (stat.isDirectory()) {
      output.push(...collectFiles(absolute, base));
      continue;
    }
    if (!stat.isFile()) fail(`unsupported filesystem entry: ${path.relative(base, absolute)}`);
    const relative = path.relative(base, absolute).split(path.sep).join('/');
    assertRuntimePath(relative);
    const content = fs.readFileSync(absolute);
    for (const pattern of SECRET_CONTENT) {
      if (pattern.test(content.toString('utf8'))) fail(`secret-like content found in ${relative}`);
    }
    output.push({ path: relative, content });
  }
  return output.sort((left, right) => compareUtf8(left.path, right.path));
}

function filesFromBundle(bundle) {
  return bundle.files.map((file) => ({
    path: file.path,
    content: Buffer.from(file.content_base64, 'base64'),
  }));
}

export function checksumForFiles(files) {
  return `sha256:${computeChecksum(files, PLUGIN_ARTIFACT_FORMAT_VERSION)}`;
}

export function bundleObjectName(bundle) {
  const match = CHECKSUM.exec(bundle.content_checksum);
  if (!match) fail(`invalid content_checksum: ${JSON.stringify(bundle.content_checksum)}`);
  return `bundles/${bundle.id}/${bundle.version}/${match[1]}.json`;
}

export function validateBundle(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('bundle must be an object');
  const bundle = value;
  if (bundle.format_version !== BUNDLE_FORMAT_VERSION) fail(`unsupported format_version: ${JSON.stringify(bundle.format_version)}`);
  if (!SKILL_ID.test(bundle.id ?? '')) fail(`invalid id: ${JSON.stringify(bundle.id)}`);
  if (!SEMVER.test(bundle.version ?? '')) fail(`invalid version: ${JSON.stringify(bundle.version)}`);
  if (bundle.source?.repository === PLUGIN_SOURCE_REPOSITORY) return validatePluginBundle(bundle);
  assertManifest(bundle.manifest, bundle.id);
  if (bundle.manifest.version !== bundle.version) fail('bundle version must equal manifest.version');
  if (!Array.isArray(bundle.files) || bundle.files.length < 2) fail('files must contain at least manifest.json and SKILL.md');
  if (bundle.files.length > 256) fail('files cannot contain more than 256 entries');
  const seen = new Set();
  // Case-insensitive, normalising filesystems (macOS, Windows) would land two such paths on one
  // file, so a deferred fetch written later could overwrite a shipped file that passed verification.
  const folded = new Map();
  let totalBytes = 0;
  for (const file of bundle.files) {
    if (!file || typeof file !== 'object' || typeof file.path !== 'string' || typeof file.content_base64 !== 'string') fail('every file needs path and content_base64 strings');
    assertRuntimePath(file.path);
    const content = Buffer.from(file.content_base64, 'base64');
    if (content.toString('base64') !== file.content_base64) fail(`non-canonical base64 in ${file.path}`);
    if (content.length > 5 * 1024 * 1024) fail(`file exceeds 5 MiB: ${file.path}`);
    totalBytes += content.length;
    if (seen.has(file.path)) fail(`duplicate file path: ${file.path}`);
    seen.add(file.path);
    const key = file.path.normalize('NFC').toLowerCase();
    if (folded.has(key)) fail(`file paths collide on a case-insensitive filesystem: ${folded.get(key)} and ${file.path}`);
    folded.set(key, file.path);
  }
  if (totalBytes > 10 * 1024 * 1024) fail('bundle files exceed 10 MiB total');
  if (!seen.has('manifest.json') || !seen.has('SKILL.md')) fail('manifest.json and SKILL.md are required');
  const sortedPaths = [...seen].sort(compareUtf8);
  if (bundle.files.some((file, index) => file.path !== sortedPaths[index])) fail('files must be sorted by UTF-8 path bytes');
  const files = filesFromBundle(bundle);
  const expectedChecksum = checksumForFiles(files);
  if (bundle.content_checksum !== expectedChecksum) fail(`checksum mismatch: expected ${expectedChecksum}`);
  let manifestFile;
  try {
    manifestFile = JSON.parse(files.find((file) => file.path === 'manifest.json').content.toString('utf8'));
  } catch (error) {
    fail(`manifest.json is not valid JSON: ${error.message}`);
  }
  if (JSON.stringify(manifestFile) !== JSON.stringify(bundle.manifest)) fail('manifest field must equal manifest.json content');
  assertDeferredFiles(bundle.manifest, files);
  if (!SOURCE_REPOSITORY_ALIASES.has(bundle.source?.repository) || !COMMIT.test(bundle.source?.commit ?? '')) fail('source must contain the canonical repository and a 40-character commit');
  if (bundleObjectName(bundle) !== `bundles/${bundle.id}/${bundle.version}/${bundle.content_checksum.slice(7)}.json`) fail('derived object path mismatch');
  return bundle;
}

export function buildBundle({ skillDirectory, sourceCommit }) {
  if (!COMMIT.test(sourceCommit ?? '')) fail('sourceCommit must be a 40-character lowercase Git commit');
  const directory = path.resolve(skillDirectory);
  if (!fs.statSync(directory).isDirectory()) fail(`skill directory does not exist: ${directory}`);
  const id = path.basename(directory);
  if (!SKILL_ID.test(id)) fail(`skill directory name is not a valid id: ${id}`);
  const files = collectFiles(directory);
  const manifestFile = files.find((file) => file.path === 'manifest.json');
  const skillFile = files.find((file) => file.path === 'SKILL.md');
  if (!manifestFile || !skillFile) fail('skill directory must contain manifest.json and SKILL.md');
  const manifest = JSON.parse(manifestFile.content.toString('utf8'));
  assertManifest(manifest, id);
  return validateBundle({
    format_version: BUNDLE_FORMAT_VERSION,
    id,
    version: manifest.version,
    manifest,
    files: files.map((file) => ({ path: file.path, content_base64: file.content.toString('base64') })),
    content_checksum: checksumForFiles(files),
    source: { repository: SOURCE_REPOSITORY, commit: sourceCommit },
  });
}

export function sha256hex(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

const unquote = (value) => {
  const trimmed = value.trim();
  if (trimmed.length >= 2 && /^["']/.test(trimmed) && trimmed.endsWith(trimmed[0])) {
    const inner = trimmed.slice(1, -1);
    return trimmed[0] === '"' ? inner.replace(/\\(["\\])/g, '$1') : inner.replace(/''/g, "'");
  }
  return trimmed;
};

/** 与 agent-service parsePluginSkillHeader 同一规则：只认顶层单行标量与一层 `metadata:`。 */
export function parsePluginSkillHeader(markdown, id) {
  const block = /^---\n([\s\S]*?)\n---(?:\n|$)/.exec(markdown);
  if (!block) fail(`plugin SKILL.md has no frontmatter: ${id}`);
  const top = new Map();
  const metadata = new Map();
  let inMetadata = false;
  for (const line of block[1].split('\n')) {
    if (line.trim() === '') continue;
    const nested = /^\s+([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (nested && inMetadata) {
      metadata.set(nested[1], unquote(nested[2]));
      continue;
    }
    const entry = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (!entry) fail(`plugin SKILL.md frontmatter line is not a key: value pair (${id}): ${line}`);
    inMetadata = entry[1] === 'metadata' && entry[2].trim() === '';
    if (!inMetadata) top.set(entry[1], unquote(entry[2]));
  }
  if (top.get('name') !== id) fail(`plugin SKILL.md name ${JSON.stringify(top.get('name'))} must equal ${JSON.stringify(id)}`);
  if (!top.get('description')) fail(`plugin SKILL.md has no description: ${id}`);
  return { name: id, description: top.get('description'), version: metadata.get('version') ?? null };
}

function validatePluginBundle(bundle) {
  if (bundle.manifest !== null) fail('plugin-sourced bundle must carry manifest: null');
  if (!Array.isArray(bundle.files) || bundle.files.length < 1) fail('files must contain SKILL.md');
  if (bundle.files.length > 256) fail('files cannot contain more than 256 entries');
  const seen = new Set();
  const folded = new Map();
  let totalBytes = 0;
  for (const file of bundle.files) {
    if (!file || typeof file !== 'object' || typeof file.path !== 'string' || typeof file.content_base64 !== 'string') fail('every file needs path and content_base64 strings');
    assertRuntimePath(file.path);
    if (file.path === 'manifest.json') fail('plugin-sourced bundle must not contain manifest.json');
    const content = Buffer.from(file.content_base64, 'base64');
    if (content.toString('base64') !== file.content_base64) fail(`non-canonical base64 in ${file.path}`);
    if (content.length > 5 * 1024 * 1024) fail(`file exceeds 5 MiB: ${file.path}`);
    totalBytes += content.length;
    if (seen.has(file.path)) fail(`duplicate file path: ${file.path}`);
    seen.add(file.path);
    const key = file.path.normalize('NFC').toLowerCase();
    if (folded.has(key)) fail(`file paths collide on a case-insensitive filesystem: ${folded.get(key)} and ${file.path}`);
    folded.set(key, file.path);
  }
  if (totalBytes > 10 * 1024 * 1024) fail('bundle files exceed 10 MiB total');
  if (!seen.has('SKILL.md')) fail('SKILL.md is required');
  const sortedPaths = [...seen].sort(compareUtf8);
  if (bundle.files.some((file, index) => file.path !== sortedPaths[index])) fail('files must be sorted by UTF-8 path bytes');
  const files = filesFromBundle(bundle);
  if (bundle.content_checksum !== checksumForFiles(files)) fail(`checksum mismatch: expected ${checksumForFiles(files)}`);
  const header = parsePluginSkillHeader(files.find((file) => file.path === 'SKILL.md').content.toString('utf8'), bundle.id);
  if (header.version !== null && header.version !== bundle.version) {
    fail(`plugin SKILL.md metadata.version ${header.version} does not match bundle version ${bundle.version}: update or remove it in kabo-mcp-plugin`);
  }
  if (!COMMIT.test(bundle.source?.commit ?? '')) fail('source must contain a 40-character commit');
  return bundle;
}

/** 插件仓一个 skill 目录 → 插件来源的包。版本由调用方给（线上版本往上接），不读也不写 manifest。 */
export function buildPluginBundle({ skillDirectory, sourceCommit, version, probe = false }) {
  if (!COMMIT.test(sourceCommit ?? '')) fail('sourceCommit must be a 40-character lowercase Git commit');
  if (!SEMVER.test(version ?? '')) fail(`version must be x.y.z, got ${JSON.stringify(version)}`);
  const directory = path.resolve(skillDirectory);
  const id = path.basename(directory);
  if (!SKILL_ID.test(id)) fail(`skill directory name is not a valid id: ${id}`);
  const files = collectFiles(directory);
  const bundle = {
    format_version: BUNDLE_FORMAT_VERSION,
    id,
    version,
    manifest: null,
    files: files.map((file) => ({ path: file.path, content_base64: file.content.toString('base64') })),
    content_checksum: checksumForFiles(files),
    source: { repository: PLUGIN_SOURCE_REPOSITORY, commit: sourceCommit },
  };
  // 探测只为算内容校验和与读声明版本，版本号此时还没定，不比对 metadata.version。
  if (probe) return { ...bundle, declared_version: parsePluginSkillHeader(files.find((file) => file.path === 'SKILL.md')?.content.toString('utf8') ?? '', id).version };
  return validateBundle(bundle);
}
