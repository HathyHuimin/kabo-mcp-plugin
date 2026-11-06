import crypto from 'node:crypto';

/**
 * Byte-compatible copy of the Plugin signed-artifact checksum helpers.
 *
 * Live Plugin code lives in kabo-sh/kabo-plugins (`scripts/lib/common.js`).
 * This file exists so Skill bundles can be built without a Plugin tree in
 * this repository. Changing the algorithm here without the Plugin/server
 * is a protocol break.
 */

export const PLUGIN_ARTIFACT_FORMAT_VERSION = 1;

export function sha256hex(data) {
  return crypto.createHash('sha256').update(data).digest('hex');
}

function assertArtifactPath(p) {
  const reject = (why) => {
    throw new Error(`illegal signed-artifact path (${why}): ${JSON.stringify(p)}`);
  };
  if (typeof p !== 'string' || p.length === 0) reject('empty');
  if (p.includes('\\') || p.includes('\0') || p.includes('\n')) reject('illegal character');
  if (p.startsWith('/')) reject('absolute');
  for (const seg of p.split('/')) {
    if (seg === '' || seg.startsWith('.')) reject('illegal segment');
  }
}

export function canonicalManifest(files, formatVersion = null) {
  if (formatVersion === null || formatVersion === undefined) {
    const sorted = [...files].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
    let manifest = '';
    for (const f of sorted) {
      manifest += f.path + '\0' + sha256hex(f.content) + '\n';
    }
    return Buffer.from(manifest, 'utf8');
  }
  if (formatVersion !== PLUGIN_ARTIFACT_FORMAT_VERSION) {
    throw new Error(`unsupported_artifact_format: ${JSON.stringify(formatVersion)}`);
  }
  for (const f of files) assertArtifactPath(f.path);
  const sorted = [...files].sort(
    (a, b) => Buffer.compare(Buffer.from(a.path, 'utf8'), Buffer.from(b.path, 'utf8')),
  );
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i].path === sorted[i - 1].path) {
      throw new Error(`duplicate signed-artifact path: ${JSON.stringify(sorted[i].path)}`);
    }
  }
  let manifest = `kabo.artifact-format\0${formatVersion}\n`;
  for (const f of sorted) {
    manifest += f.path + '\0' + sha256hex(f.content) + '\n';
  }
  return Buffer.from(manifest, 'utf8');
}

export function computeChecksum(files, formatVersion = null) {
  return sha256hex(canonicalManifest(files, formatVersion));
}
