const CHANNELS = new Set(['internal', 'production']);

function fail(message) {
  throw new Error(`skill_registry_publish_failed: ${message}`);
}

function objectUrl(bucket, object, media = false, generation) {
  const query = new URLSearchParams();
  if (media) query.set('alt', 'media');
  if (generation !== undefined) query.set('generation', String(generation));
  return `https://storage.googleapis.com/storage/v1/b/${encodeURIComponent(bucket)}/o/${encodeURIComponent(object)}?${query}`;
}

export class GcsObjectStore {
  constructor({ bucket, accessToken, fetchImpl = fetch }) {
    if (!bucket || !accessToken) fail('bucket and accessToken are required');
    this.bucket = bucket;
    this.accessToken = accessToken;
    this.fetch = fetchImpl;
  }

  headers(contentType) {
    return { Authorization: `Bearer ${this.accessToken}`, ...(contentType ? { 'Content-Type': contentType } : {}) };
  }

  async read(object, generation) {
    const metadataResponse = await this.fetch(objectUrl(this.bucket, object, false, generation), { headers: this.headers() });
    if (metadataResponse.status === 404) return null;
    if (!metadataResponse.ok) fail(`metadata read ${object} returned ${metadataResponse.status}`);
    const metadata = await metadataResponse.json();
    const resolvedGeneration = Number(metadata.generation);
    if (!Number.isSafeInteger(resolvedGeneration) || resolvedGeneration < 1) fail('invalid object generation');
    const contentResponse = await this.fetch(objectUrl(this.bucket, object, true, resolvedGeneration), { headers: this.headers() });
    if (!contentResponse.ok) fail(`object read ${object} returned ${contentResponse.status}`);
    return { bytes: Buffer.from(await contentResponse.arrayBuffer()), generation: resolvedGeneration };
  }

  async write(object, bytes, ifGenerationMatch) {
    const query = new URLSearchParams({ uploadType: 'media', name: object, ifGenerationMatch: String(ifGenerationMatch) });
    const response = await this.fetch(`https://storage.googleapis.com/upload/storage/v1/b/${encodeURIComponent(this.bucket)}/o?${query}`, {
      method: 'POST',
      headers: this.headers('application/json'),
      body: bytes,
    });
    if (response.status === 412) return { ok: false, conflict: true };
    if (!response.ok) fail(`object write ${object} returned ${response.status}: ${(await response.text()).slice(0, 300)}`);
    const metadata = await response.json();
    return { ok: true, generation: Number(metadata.generation) };
  }
}

export class MemoryObjectStore {
  constructor() {
    this.objects = new Map();
    this.nextGeneration = 1;
  }

  async read(object, generation) {
    const versions = this.objects.get(object) ?? [];
    const found = generation === undefined ? versions.at(-1) : versions.find((item) => item.generation === Number(generation));
    return found ? { bytes: Buffer.from(found.bytes), generation: found.generation } : null;
  }

  async write(object, bytes, ifGenerationMatch) {
    const versions = this.objects.get(object) ?? [];
    const current = versions.at(-1);
    const actual = current?.generation ?? 0;
    if (actual !== Number(ifGenerationMatch)) return { ok: false, conflict: true };
    const value = { bytes: Buffer.from(bytes), generation: this.nextGeneration++ };
    this.objects.set(object, [...versions, value]);
    return { ok: true, generation: value.generation };
  }
}

export function emptyIndex(channel, now) {
  if (!CHANNELS.has(channel)) fail(`unknown channel: ${channel}`);
  return { format_version: 1, channel, updated_at: now.toISOString(), skills: {} };
}

export function validateIndex(value, expectedChannel) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.format_version !== 1 || value.channel !== expectedChannel || !value.skills || typeof value.skills !== 'object' || Array.isArray(value.skills)) fail(`invalid ${expectedChannel} index`);
  if (!Number.isFinite(Date.parse(value.updated_at))) fail('index.updated_at must be an ISO date-time');
  for (const [id, entry] of Object.entries(value.skills)) {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) || !entry || typeof entry !== 'object') fail(`invalid index entry: ${id}`);
    if (!/^\d+\.\d+\.\d+$/.test(entry.version ?? '') || !/^sha256:[0-9a-f]{64}$/.test(entry.content_checksum ?? '')) fail(`invalid version/checksum for ${id}`);
    const checksum = entry.content_checksum.slice(7);
    if (entry.object !== `bundles/${id}/${entry.version}/${checksum}.json`) fail(`object path does not match id/version/checksum for ${id}`);
  }
  return value;
}
