import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { buildPluginBundle, stableJson, validateBundle } from './web-publish/bundle.mjs';

// 插件包格式：与 kabo-agent-service src/skill/registry-format.ts 读方一致（从 creator-skills tests/skill-registry-plugin-source.mjs 搬来）。
const COMMIT = 'b'.repeat(40);


function pluginRepo(skills) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plugin-source-'));
  for (const [id, frontmatter] of Object.entries(skills)) {
    const dir = path.join(root, 'skills', id);
    fs.mkdirSync(path.join(dir, 'scripts'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'SKILL.md'), `---\nname: ${id}\ndescription: "A ${id} fixture"\n${frontmatter}---\n# Fixture\n`);
    fs.writeFileSync(path.join(dir, 'scripts', 'static_host.py'), 'print("ok")\n');
  }
  return root;
}

test('plugin bundle carries no manifest, keeps SKILL.md bytes and is accepted by validateBundle', () => {
  const root = pluginRepo({ 'alpha-skill': '' });
  const bundle = buildPluginBundle({ skillDirectory: path.join(root, 'skills', 'alpha-skill'), sourceCommit: COMMIT, version: '1.2.6' });
  assert.equal(bundle.manifest, null);
  assert.deepEqual(bundle.source, { repository: 'kabo-sh/kabo-mcp-plugin', commit: COMMIT });
  assert.deepEqual(bundle.files.map(file => file.path), ['SKILL.md', 'scripts/static_host.py']);
  assert.equal(validateBundle(JSON.parse(stableJson(bundle))).id, 'alpha-skill');
  const tampered = structuredClone(bundle); tampered.files.push({ path: 'manifest.json', content_base64: Buffer.from('{}').toString('base64') });
  assert.throws(() => validateBundle(tampered), /must not contain manifest.json|checksum mismatch|sorted/);
  assert.throws(() => validateBundle({ ...bundle, manifest: {} }), /manifest: null/);
});

test('plugin metadata.version must equal the Registry version; a mismatch names the fix', () => {
  const root = pluginRepo({ 'beta-skill': 'metadata:\n  version: "0.4.10"\n' });
  const directory = path.join(root, 'skills', 'beta-skill');
  assert.throws(() => buildPluginBundle({ skillDirectory: directory, sourceCommit: COMMIT, version: '0.4.11' }), /metadata.version 0.4.10 does not match bundle version 0.4.11/);
  assert.equal(buildPluginBundle({ skillDirectory: directory, sourceCommit: COMMIT, version: '0.4.10' }).version, '0.4.10');
  const wrongName = pluginRepo({ 'gamma-skill': '' });
  fs.renameSync(path.join(wrongName, 'skills', 'gamma-skill'), path.join(wrongName, 'skills', 'delta-skill'));
  assert.throws(() => buildPluginBundle({ skillDirectory: path.join(wrongName, 'skills', 'delta-skill'), sourceCommit: COMMIT, version: '1.0.0' }), /name "gamma-skill" must equal "delta-skill"/);
});
