// Build Plugin: Process Queue
//
// Runs in onPostBuild (which has NETLIFY_BLOBS_CONTEXT — build.command does not).
// Reads pending download jobs from the queue, downloads each, stores results.
//
// Why a build plugin (not a function):
//   - Functions cap at 30s sync / 15 min background
//   - Build process runs up to 15 min, can handle larger downloads in one shot
//   - 1 concurrent build on Free plan = serialized queue processing (no race conditions)
//
// Concurrency:
//   - Only ONE build can run at a time on Free plan, so no distributed locking needed
//   - Each job is marked "downloading" before download starts (so re-runs skip it)
//   - If a build crashes mid-download, the job stays in "downloading" state —
//     we should add a TTL check to requeue stale "downloading" jobs

import tls from 'node:tls';

const STORE_NAME = 'download-jobs';
const BUILD_TIMEOUT_MS = 14 * 60 * 1000; // 14 min (leave 1 min buffer under 15 min cap)
const PER_JOB_TIMEOUT_MS = 60_000; // 60s per job by default
const MAX_JOBS_PER_BUILD = 50;
const STALE_DOWNLOADING_MS = 10 * 60 * 1000; // requeue if "downloading" > 10 min

// Chrome 120 ClientHello spec (same as functions/download.mjs)
const CHROME_120_SPEC = {
  cipherSuites: [0x1301, 0x1302, 0x1303, 0xc02b, 0xc02f, 0xc02c, 0xc030, 0xcca9, 0xcca8, 0xc013, 0xc014, 0x009c, 0x009d, 0x002f, 0x0035],
  extensions: [
    { type: 0x0016 }, { type: 0x000b }, { type: 0xff01 }, { type: 0x0000 },
    { type: 0x0017 }, { type: 0x000d }, { type: 0x000a }, { type: 0x0023 },
    { type: 0x0010, alpnProtocols: ['h2', 'http/1.1'] }, { type: 0x002b },
    { type: 0x002d }, { type: 0x0033 }, { type: 0x001c }, { type: 0x0015 },
  ],
  supportedGroups: [0x001d, 0x0017, 0x0018],
  signatureAlgorithms: [0x0403, 0x0804, 0x0401, 0x0503, 0x0501, 0x0803, 0x0601, 0x0201],
  alpnProtocols: ['h2', 'http/1.1'],
};

let tlsImpersonate = null;
async function getTlsImpersonate() {
  if (tlsImpersonate !== null) return tlsImpersonate;
  try {
    tlsImpersonate = await import('tls-impersonate');
  } catch (e) {
    console.log(`  tls-impersonate not available: ${e.message}`);
    tlsImpersonate = false;
  }
  return tlsImpersonate;
}

async function getStore() {
  const { getStore: getStoreFn } = await import('@netlify/blobs');
  return getStoreFn(STORE_NAME);
}

async function setStatus(jobId, status, extra = {}) {
  const store = await getStore();
  const statusObj = {
    ...extra,
    job_id: jobId,
    status,
    updated_at: new Date().toISOString(),
  };
  await store.setJSON(`status/${jobId}`, statusObj);
  await store.setJSON('index/latest', { job_id: jobId, status, updated_at: statusObj.updated_at });
  return statusObj;
}

async function getStatus(jobId) {
  const store = await getStore();
  try {
    return await store.get(`status/${jobId}`, { type: 'json' });
  } catch {
    return null;
  }
}

async function setResult(jobId, body, contentType, metadata = {}) {
  const store = await getStore();
  await store.set(`result/${jobId}`, body, {
    metadata: {
      content_type: contentType,
      size: String(body.length),
      stored_at: new Date().toISOString(),
      ...metadata,
    },
  });
}

async function dequeueJob(jobId) {
  const store = await getStore();
  await store.delete(`queue/pending/${jobId}`);
}

async function listPendingJobs() {
  const store = await getStore();
  const list = await store.list();
  const queueKeys = (list.blobs || []).filter(b => b.key.startsWith('queue/pending/'));
  const jobs = [];
  for (const k of queueKeys) {
    try {
      const spec = await store.get(k.key, { type: 'json' });
      if (spec) jobs.push({ spec, key: k.key });
    } catch {}
  }
  // Sort by created_at ascending (oldest first)
  jobs.sort((a, b) => (a.spec.created_at || '').localeCompare(b.spec.created_at || ''));
  return jobs;
}

async function listStaleDownloadingJobs() {
  const store = await getStore();
  const list = await store.list();
  const statusKeys = (list.blobs || []).filter(b => b.key.startsWith('status/'));
  const stale = [];
  const now = Date.now();
  for (const k of statusKeys) {
    try {
      const s = await store.get(k.key, { type: 'json' });
      if (s && s.status === 'downloading' && s.updated_at) {
        const updatedMs = new Date(s.updated_at).getTime();
        if (now - updatedMs > STALE_DOWNLOADING_MS) {
          stale.push(s);
        }
      }
    } catch {}
  }
  return stale;
}

// === Download implementation (mirror of functions/download.mjs) ===

async function downloadUrl(targetUrl, method, userAgent, timeoutMs = PER_JOB_TIMEOUT_MS) {
  if (method === 'chrome_impersonate') {
    const imp = await getTlsImpersonate();
    if (!imp || !imp.isSupported()) {
      throw new Error('chrome_impersonate requested but tls-impersonate not available');
    }
    return downloadWithTlsImpersonate(targetUrl, userAgent, timeoutMs, imp);
  }
  // Default: undici fetch
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error('Download timeout')), timeoutMs);
  try {
    const r = await fetch(targetUrl, {
      headers: { 'User-Agent': userAgent, 'Accept': '*/*' },
      redirect: 'follow',
      signal: controller.signal,
    });
    const buf = Buffer.from(await r.arrayBuffer());
    return {
      status: r.status,
      body: buf,
      content_type: r.headers.get('content-type') || 'application/octet-stream',
      headers: Object.fromEntries(r.headers.entries()),
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function downloadWithTlsImpersonate(targetUrl, userAgent, timeoutMs, imp) {
  const { tlsOptions } = imp.impersonate(CHROME_120_SPEC);
  const url = new URL(targetUrl);
  const tlsSocket = tls.connect({
    host: url.hostname,
    port: parseInt(url.port || '443'),
    servername: url.hostname,
    ...tlsOptions,
  });
  const timeout = setTimeout(() => tlsSocket.destroy(new Error('TLS timeout')), timeoutMs);
  try {
    await new Promise((resolve, reject) => {
      tlsSocket.once('secureConnect', resolve);
      tlsSocket.once('error', reject);
    });
    const path = (url.pathname || '/') + (url.search || '');
    tlsSocket.write(
      `GET ${path} HTTP/1.1\r\nHost: ${url.hostname}\r\nUser-Agent: ${userAgent}\r\nAccept: */*\r\nConnection: close\r\n\r\n`
    );
    const chunks = [];
    await new Promise((resolve) => {
      tlsSocket.on('data', (c) => chunks.push(c));
      tlsSocket.on('end', resolve);
      tlsSocket.on('close', resolve);
      tlsSocket.on('error', resolve);
    });
    const raw = Buffer.concat(chunks).toString('utf8');
    const headerEnd = raw.indexOf('\r\n\r\n');
    const rawHeaders = headerEnd >= 0 ? raw.substring(0, headerEnd) : '';
    const body = headerEnd >= 0 ? raw.substring(headerEnd + 4) : raw;
    const status = parseInt(rawHeaders.match(/^HTTP\/[\d.]+ (\d+)/)?.[1] || '0');
    const ctMatch = rawHeaders.match(/^content-type:\s*(.+)$/im);
    return {
      status,
      body: Buffer.from(body, 'utf8'),
      content_type: ctMatch ? ctMatch[1].trim() : 'application/octet-stream',
      headers: {},
    };
  } finally {
    clearTimeout(timeout);
    tlsSocket.destroy();
  }
}

// === Job processor ===

async function processJob(jobSpec) {
  const { job_id, target_url, method, user_agent, timeout_ms } = jobSpec;
  const timeoutMs = Math.min(timeout_ms || PER_JOB_TIMEOUT_MS, PER_JOB_TIMEOUT_MS);

  // Mark as downloading
  await setStatus(job_id, 'downloading', { ...jobSpec, started_at: new Date().toISOString() });
  console.log(`JOB_START job_id=${job_id} url=${target_url} method=${method}`);

  try {
    const downloadStart = Date.now();
    const result = await downloadUrl(target_url, method, user_agent, timeoutMs);
    const downloadMs = Date.now() - downloadStart;

    // Store result
    await setResult(job_id, result.body, result.content_type, {
      target_url,
      method,
      upstream_status: String(result.status),
    });

    // Mark complete + dequeue
    await setStatus(job_id, 'complete', {
      ...jobSpec,
      download_ms: downloadMs,
      size: result.body.length,
      content_type: result.content_type,
      upstream_status: result.status,
      completed_at: new Date().toISOString(),
      result_blob_key: `result/${job_id}`,
    });
    await dequeueJob(job_id);

    console.log(`JOB_COMPLETE job_id=${job_id} status=${result.status} size=${result.body.length} ms=${downloadMs}`);
    return { ok: true, job_id, size: result.body.length, ms: downloadMs };

  } catch (e) {
    // Mark as error (keep in queue for retry — but with attempts counter)
    const attempts = (jobSpec.attempts || 0) + 1;
    const shouldRetry = attempts < 3; // max 3 attempts
    const newStatus = shouldRetry ? 'pending' : 'error';

    await setStatus(job_id, newStatus, {
      ...jobSpec,
      attempts,
      error: e.message,
      last_attempt_at: new Date().toISOString(),
    });

    if (shouldRetry) {
      // Re-queue with incremented attempts
      const store = await getStore();
      await store.setJSON(`queue/pending/${job_id}`, { ...jobSpec, attempts });
      console.log(`JOB_RETRY job_id=${job_id} attempt=${attempts} error=${e.message}`);
    } else {
      // Final failure — dequeue
      await dequeueJob(job_id);
      console.error(`JOB_ERROR job_id=${job_id} attempts=${attempts} error=${e.message}`);
    }
    return { ok: false, job_id, error: e.message };
  }
}

// === Plugin entry point ===

export default {
  onPostBuild: async ({ utils }) => {
    const buildStart = Date.now();
    console.log(`\n========== PROCESS_QUEUE_PLUGIN started ${new Date().toISOString()} ==========`);

    // Step 1: Requeue stale "downloading" jobs (from crashed builds)
    const staleJobs = await listStaleDownloadingJobs();
    if (staleJobs.length > 0) {
      console.log(`\n  Found ${staleJobs.length} stale "downloading" jobs (will requeue):`);
      for (const s of staleJobs) {
        const store = await getStore();
        await store.setJSON(`queue/pending/${s.job_id}`, {
          ...s,
          attempts: (s.attempts || 0) + 1,
          requeued_at: new Date().toISOString(),
        });
        await setStatus(s.job_id, 'pending', { ...s, requeued_at: new Date().toISOString() });
        console.log(`    - ${s.job_id} (stale since ${s.updated_at})`);
      }
    }

    // Step 2: List pending jobs
    const pendingJobs = await listPendingJobs();
    if (pendingJobs.length === 0) {
      console.log(`\n  No pending jobs in queue. Done.`);
      console.log(`\n========== PROCESS_QUEUE_PLUGIN_END (empty) ==========\n`);
      return;
    }

    console.log(`\n  Found ${pendingJobs.length} pending job(s). Processing up to ${MAX_JOBS_PER_BUILD}...`);

    // Step 3: Process jobs (with overall build timeout)
    let processed = 0;
    let succeeded = 0;
    let failed = 0;
    for (const { spec } of pendingJobs.slice(0, MAX_JOBS_PER_BUILD)) {
      // Check build timeout
      const elapsedMs = Date.now() - buildStart;
      if (elapsedMs > BUILD_TIMEOUT_MS - PER_JOB_TIMEOUT_MS) {
        console.log(`\n  Approaching build timeout (${elapsedMs}ms elapsed). Stopping.`);
        break;
      }

      console.log(`\n  Processing job ${processed + 1}/${Math.min(pendingJobs.length, MAX_JOBS_PER_BUILD)}: ${spec.job_id}`);
      const result = await processJob(spec);
      processed++;
      if (result.ok) succeeded++; else failed++;
    }

    const totalMs = Date.now() - buildStart;
    console.log(`\n========== PROCESS_QUEUE_PLUGIN_END ==========`);
    console.log(`  Processed: ${processed} | Succeeded: ${succeeded} | Failed: ${failed}`);
    console.log(`  Total time: ${totalMs}ms`);
    console.log();
  },
};
