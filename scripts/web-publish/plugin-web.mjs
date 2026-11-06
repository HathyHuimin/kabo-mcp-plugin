#!/usr/bin/env node
// 插件 skill 的 Web 通道（kabo-agent-service DEPLOY.md「插件 skill 的 Web 通道」）。由本仓
// .github/workflows/web-publish.yml 在发版时调用（2026-11-06 从 creator-skills scripts/skill-registry/ 搬来，逻辑不变）。
//
// 2026-11-05 用户裁定：桌面端与 kabo-alpha 冻结在 production 频道的旧版本，只有 Web 改跑
// kabo-mcp-plugin 的 skill。所以这里**从不改任何频道 index**：插件包只写进 bundles/（按内容寻址、
// 只新建），再写 Web 专用的可变 overlay：
//   - channels/production/overlays/web-plugin/index.json  生产 Web（插件发版时 CI 自动写）
//   - channels/production/overlays/web-plugin/test.json   pre 上登记的测试账号看的那份
//   - channels/production/overlays/web-plugin/test-accounts.json  pre 测试账号名单
// 每次写 overlay 前先把新内容存一份 history/<sha256>.json（只新建），`restore` 按摘要回到任一历史版本。
//
// 子命令：
//   publish      --plugin-root <dir> --plugin-commit <sha> [--target index|test] [--skills a,b] [--hold a,b]
//   restore      --sha <history 摘要> [--target index|test]
//   clear        [--target index|test]          写一份空 overlay（Web 回到只读实时索引）
//   show         [--target index|test]
//   released                                   打印生产 overlay 当前对应的插件 commit
//   test-accounts add <user_id> [--note text] | remove <user_id> | list
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

import { buildPluginBundle, bundleObjectName, stableJson } from './bundle.mjs';
import { GcsObjectStore, validateIndex } from './store.mjs';

export const OVERLAY_PREFIX = 'channels/production/overlays/web-plugin/';
export const TARGETS = { index: `${OVERLAY_PREFIX}index.json`, test: `${OVERLAY_PREFIX}test.json` };
export const TEST_ACCOUNTS_OBJECT = `${OVERLAY_PREFIX}test-accounts.json`;
// 每个 skill 在任一 overlay 上用过的最高版本。overlay 被 clear、某个 skill 被暂扣再放回、或先发到 test 目标时，
// 只看「频道 + 当前 overlay」会把用过的版本号再发一次（内容却不同），/health 就分不出新旧。
export const VERSIONS_OBJECT = `${OVERLAY_PREFIX}versions.json`;
const historyObject = (sha) => `${OVERLAY_PREFIX}history/${sha}.json`;
// 复刻在 Web 上由 creator-remake-web（production 频道）承担；插件的 remake-viral-video 走别的工具面，不进 Web。
export const EXCLUDED = new Set(['remake-viral-video']);

const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/;
const COMMIT = /^[0-9a-f]{40}$/;
const SKILL_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const sha256 = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');

function fail(message) {
  throw new Error(`plugin_web_failed: ${message}`);
}

function compare(left, right) {
  const a = SEMVER.exec(left).slice(1).map(Number);
  const b = SEMVER.exec(right).slice(1).map(Number);
  for (let i = 0; i < 3; i += 1) if (a[i] !== b[i]) return a[i] - b[i];
  return 0;
}

function target(name = 'index') {
  const object = TARGETS[name];
  if (!object) fail(`target must be one of ${Object.keys(TARGETS).join(', ')}`);
  return object;
}

async function readIndex(store, object, channel = 'production') {
  const raw = await store.read(object);
  if (!raw) return null;
  return { index: validateIndex(JSON.parse(raw.bytes), channel), generation: raw.generation };
}

/** 可读到的频道里该 skill 的最大版本（Internal 发布身份读不到 production 时只看读得到的那份）。 */
function liveVersion(id, indexes) {
  const versions = indexes
    .map((index) => index?.skills?.[id])
    .filter((entry) => entry && !entry.tombstone && SEMVER.test(entry.version ?? ''))
    .map((entry) => entry.version);
  return versions.length ? versions.sort(compare).at(-1) : null;
}

/**
 * 版本：内容和 overlay 里现有条目相同就原样沿用；变了就取「频道现行版本」与「overlay 现有版本」中
 * 较大的那个，补丁号 +1。所以 Web 上的版本号接着线上往上走，每次插件发版只给真正变了的 skill 升版。
 */
export function versionFor(id, checksum, liveIndexes, current, declared = null, used = []) {
  const existing = current?.skills?.[id];
  if (existing && existing.content_checksum === checksum) return existing.version;
  // used：别的 overlay 目标与 versions.json 里记下的该 skill 版本；只抬高下限，不让一个频道里没有的 skill 首发。
  const live = [liveVersion(id, liveIndexes), existing?.version].filter(Boolean);
  if (!live.length) return null;
  const candidates = [...live, ...used.filter((version) => SEMVER.test(version ?? ''))];
  if (!candidates.length) return null;
  const latest = candidates.sort(compare).at(-1);
  // SKILL.md 声明了 metadata.version（脚本会读它，例如内容日历）：用它，但必须高于线上与 overlay 的版本。
  if (declared) {
    if (!SEMVER.test(declared) || compare(declared, latest) <= 0)
      fail(`${id}: SKILL.md metadata.version ${declared} must be above the live version ${latest}; bump it in kabo-mcp-plugin`);
    return declared;
  }
  const [major, minor, patch] = SEMVER.exec(latest).slice(1).map(Number);
  return `${major}.${minor}.${patch + 1}`;
}

async function writeOverlay(store, object, overlay) {
  const bytes = Buffer.from(stableJson(validateIndex(overlay, 'production')));
  const digest = sha256(bytes);
  const history = historyObject(digest);
  const saved = await store.read(history);
  if (!saved) {
    const created = await store.write(history, bytes, 0);
    if (!created.ok) fail(`history object appeared concurrently: ${history}`);
  } else if (!saved.bytes.equals(bytes)) {
    fail(`history collision: ${history}`);
  }
  const current = await store.read(object);
  if (current?.bytes.equals(bytes)) return { object, sha256: digest, generation: current.generation, unchanged: true };
  const written = await store.write(object, bytes, current?.generation ?? 0);
  if (!written.ok) fail(`${object} changed while writing; rerun`);
  const readback = await store.read(object);
  if (!readback?.bytes.equals(bytes)) fail(`readback mismatch for ${object}`);
  return { object, sha256: digest, generation: readback.generation, unchanged: false };
}

/** 插件仓某个 commit 的 skill → 插件包（bundles/，只新建）→ 写 overlay。不碰任何频道 index。 */
export const RELEASE_OBJECT = `${OVERLAY_PREFIX}release.json`;

/**
 * `bundleStore` 写 bundles/（CI 里是 Internal 发布身份，只有它能写 bundles/）；`store` 读频道、写
 * overlay（CI 里是 Production 发布身份，能写 channels/production/ 而不能写 bundles/）。本地两者同一个。
 */
export async function publish({ store, bundleStore = store, pluginRoot, pluginCommit, pluginTag = null, targetName = 'index', skills, hold = [], now = new Date() }) {
  if (!COMMIT.test(pluginCommit ?? '')) fail('plugin commit must be 40 lowercase hex characters');
  const object = target(targetName);
  const readChannel = async (channel) => {
    try {
      return (await readIndex(store, `channels/${channel}/index.json`, channel))?.index ?? null;
    } catch (error) {
      if (/returned 403/.test(error.message)) return null;
      throw error;
    }
  };
  const live = [await readChannel('production'), await readChannel('internal')];
  if (!live.some(Boolean)) fail('cannot read any live channel index');
  const current = (await readIndex(store, object))?.index ?? null;
  // 另一个目标（生产 / test）的 overlay：同一份内容在那边已经有版本号，就用同一个号和同一个包。
  const others = await Promise.all(Object.entries(TARGETS).filter(([name]) => name !== targetName)
    .map(async ([, other]) => (await readIndex(store, other))?.index ?? null));
  const versionsRaw = await store.read(VERSIONS_OBJECT);
  const highwater = versionsRaw ? JSON.parse(versionsRaw.bytes) : { format_version: 1, skills: {} };
  const available = fs.readdirSync(path.join(pluginRoot, 'skills'))
    .filter((id) => SKILL_ID.test(id) && fs.existsSync(path.join(pluginRoot, 'skills', id, 'SKILL.md')));
  // hold：本次不进 overlay 的 skill（Web 继续用 Registry 原版本），由工作流的 PLUGIN_WEB_HOLD 传入。
  const wanted = (skills?.length ? skills : available.filter((id) => !EXCLUDED.has(id))).filter((id) => !hold.includes(id));
  const unknown = wanted.filter((id) => !available.includes(id));
  if (unknown.length) fail(`not in the plugin at ${pluginCommit}: ${unknown.join(', ')}`);
  // 先全部打包、算版本，任何一个不合格整批不写。
  const skipped = [];
  const bundles = [];
  const kept = {};
  for (const id of wanted) {
    const directory = path.join(pluginRoot, 'skills', id);
    const probe = buildPluginBundle({ skillDirectory: directory, sourceCommit: pluginCommit, version: '0.0.1', probe: true });
    const existing = current?.skills?.[id];
    // 内容没变：原样沿用 overlay 里那一条（包已在桶里，source.commit 记的是首次发它的那个 commit）。
    if (existing && existing.content_checksum === probe.content_checksum) { kept[id] = existing; continue; }
    const twin = others.map((index) => index?.skills?.[id]).find((entry) => entry?.content_checksum === probe.content_checksum);
    if (twin && (await bundleStore.read(twin.object))) { kept[id] = twin; continue; }
    const used = [...others.map((index) => index?.skills?.[id]?.version), highwater.skills?.[id]].filter(Boolean);
    const version = versionFor(id, probe.content_checksum, live, current, probe.declared_version, used);
    // 频道里从没有过的 skill（如插件独有的）不在这里首发：Web 上没有它的入口与 chip 策略。
    if (!version) { skipped.push({ id, reason: 'not live on any channel' }); continue; }
    bundles.push(buildPluginBundle({ skillDirectory: directory, sourceCommit: pluginCommit, version }));
  }
  if (!bundles.length && !Object.keys(kept).length) fail('nothing to publish');
  for (const bundle of bundles) {
    const objectName = bundleObjectName(bundle);
    const bytes = Buffer.from(stableJson(bundle));
    await bundleStore.write(objectName, bytes, 0);
    const saved = await bundleStore.read(objectName);
    // 同名对象已在桶里：名字里已经有 id、版本与内容校验和，所以只要信封里这三项一致就是同一份内容
    // （别的 commit 先打过同样的文件，source.commit 不同而已），沿用桶里那份。
    const same = saved && (saved.bytes.equals(bytes) || (() => {
      const existing = JSON.parse(saved.bytes);
      return existing.id === bundle.id && existing.version === bundle.version &&
        existing.content_checksum === bundle.content_checksum && existing.source?.repository === bundle.source.repository;
    })());
    if (!same) fail(`bundle object collision or readback mismatch: ${objectName}`);
  }
  const overlay = {
    format_version: 1,
    channel: 'production',
    updated_at: now.toISOString(),
    skills: Object.fromEntries([
      ...Object.entries(kept),
      ...bundles.map((bundle) => [bundle.id, {
        version: bundle.version,
        object: bundleObjectName(bundle),
        content_checksum: bundle.content_checksum,
      }]),
    ].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))),
  };
  const written = await writeOverlay(store, object, overlay);
  // 记下每个 skill 用过的最高版本（只升不降）。
  const nextHighwater = { format_version: 1, skills: { ...(highwater.skills ?? {}) } };
  for (const [id, entry] of Object.entries(overlay.skills)) {
    const prior = nextHighwater.skills[id];
    if (!prior || compare(entry.version, prior) > 0) nextHighwater.skills[id] = entry.version;
  }
  if (stableJson(nextHighwater) !== stableJson(highwater)) {
    const saved = await store.write(VERSIONS_OBJECT, Buffer.from(stableJson(nextHighwater)), versionsRaw?.generation ?? 0);
    if (!saved.ok) fail(`${VERSIONS_OBJECT} changed while writing; rerun`);
  }
  if (targetName === 'index') {
    // 记下这份生产 overlay 来自插件的哪个发版：定时任务据此判断「新发版还没上 Web」。
    const record = Buffer.from(stableJson({ plugin_commit: pluginCommit, plugin_tag: pluginTag, overlay_sha256: written.sha256, published_at: now.toISOString() }));
    const previous = await store.read(RELEASE_OBJECT);
    const saved = await store.write(RELEASE_OBJECT, record, previous?.generation ?? 0);
    if (!saved.ok) fail(`${RELEASE_OBJECT} changed while writing; rerun`);
  }
  return {
    plugin_commit: pluginCommit,
    channels_unchanged: true,
    overlay: written,
    previous: current ? Object.fromEntries(Object.entries(current.skills).map(([id, entry]) => [id, entry.version])) : {},
    skills: overlay.skills,
    skipped,
  };
}

export async function restore({ store, sha, targetName = 'index' }) {
  if (!/^[0-9a-f]{64}$/.test(sha ?? '')) fail('sha must be 64 lowercase hex characters');
  const saved = await store.read(historyObject(sha));
  if (!saved) fail(`no history object ${sha}`);
  if (sha256(saved.bytes) !== sha) fail('history object does not match its name');
  return writeOverlay(store, target(targetName), JSON.parse(saved.bytes));
}

export async function clear({ store, targetName = 'index', now = new Date() }) {
  return writeOverlay(store, target(targetName), { format_version: 1, channel: 'production', updated_at: now.toISOString(), skills: {} });
}

export async function testAccounts({ store, action, userId, note }) {
  const raw = await store.read(TEST_ACCOUNTS_OBJECT);
  const current = raw ? JSON.parse(raw.bytes) : { format_version: 1, accounts: [] };
  if (action === 'list') return current;
  if (!userId || userId.length > 128) fail('user id is required');
  const others = current.accounts.filter((account) => account.user_id !== userId);
  const next = {
    format_version: 1,
    accounts: action === 'add'
      ? [...others, { user_id: userId, ...(note ? { note: note.slice(0, 200) } : {}) }]
      : action === 'remove' ? others : fail(`unknown test-accounts action ${action}`),
  };
  if (next.accounts.length > 50) fail('at most 50 test accounts');
  const written = await store.write(TEST_ACCOUNTS_OBJECT, Buffer.from(stableJson(next)), raw?.generation ?? 0);
  if (!written.ok) fail('test account list changed while writing; rerun');
  return next;
}

function option(name) {
  const index = process.argv.indexOf(name);
  return index === -1 ? undefined : process.argv[index + 1];
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  try {
    const store = new GcsObjectStore({ bucket: process.env.KABO_SKILL_REGISTRY_BUCKET || fail('KABO_SKILL_REGISTRY_BUCKET is required'), accessToken: process.env.GCS_ACCESS_TOKEN });
    const command = process.argv[2];
    const targetName = option('--target') ?? 'index';
    let result;
    if (command === 'publish') {
      const skills = (option('--skills') ?? '').split(',').map((item) => item.trim()).filter(Boolean);
      const bundleStore = process.env.GCS_BUNDLE_ACCESS_TOKEN
        ? new GcsObjectStore({ bucket: store.bucket, accessToken: process.env.GCS_BUNDLE_ACCESS_TOKEN })
        : store;
      const hold = (option('--hold') ?? '').split(',').map((item) => item.trim()).filter(Boolean);
      result = await publish({ store, bundleStore, pluginRoot: option('--plugin-root'), pluginCommit: option('--plugin-commit'), pluginTag: option('--plugin-tag') ?? null, targetName, skills, hold });
    } else if (command === 'released') {
      // 定时任务用：打印生产 overlay 当前对应的插件 commit（没有就空串）。
      const raw = await store.read(RELEASE_OBJECT);
      console.log(raw ? JSON.parse(raw.bytes).plugin_commit : '');
      process.exit(0);
    } else if (command === 'restore') {
      result = await restore({ store, sha: option('--sha'), targetName });
    } else if (command === 'clear') {
      result = await clear({ store, targetName });
    } else if (command === 'show') {
      result = (await readIndex(store, target(targetName))) ?? { absent: true };
    } else if (command === 'test-accounts') {
      result = await testAccounts({ store, action: process.argv[3], userId: process.argv[4], note: option('--note') });
    } else {
      fail('usage: plugin-web.mjs publish|restore|clear|show|test-accounts');
    }
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
