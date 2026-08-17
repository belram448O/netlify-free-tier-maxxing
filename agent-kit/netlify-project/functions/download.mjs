// Download Service — proper implementation
//
// Architecture:
//   - Function handles: queue submission, status queries, result retrieval, cancellation
//   - Build plugin handles: actual downloading (for async/long-running jobs, up to 15 min)
//   - Sync mode (small files, <30s): function downloads inline
//   - Async mode (large files, >30s): function queues job, build plugin processes
//
// Storage layout (Blobs, store="download-jobs"):
//   ├── queue/pending/{job_id}    — JSON: job spec (url, method, ua, created_at, attempts)
//   ├── status/{job_id}           — JSON: current status (pending|downloading|complete|error|cancelled)
//   ├── result/{job_id}           — Raw bytes: downloaded content (with metadata)
//   └── index/latest              — JSON: pointer to most recent job
//
// Endpoints:
//   POST /api/download?url=<url>[&method=fetch|chrome_impersonate][&async=1][&ua=...][&timeout=N]
//     → Queues a download job
//     → If async=1: returns immediately with status=pending, client polls
//     → If async=0 (default): function downloads inline (up to 30s), returns final status
//
//   GET /api/download?job_id=<id>
//     → Returns current status
//
//   GET /api/download?job_id=<id>&result=1
//     → Returns the downloaded data (raw passthrough)
//
//   GET /api/download?list=1[&status=pending|complete|error]
//     → Lists jobs (most recent first)
//
//   DELETE /api/download?job_id=<id>
//     → Marks job as cancelled (best-effort; if already downloading, the build plugin will check)
//
// Log events (auditable via `netlify logs --json --function download`):
//   JOB_QUEUED, JOB_START, JOB_PROGRESS, JOB_COMPLETE, JOB_ERROR, JOB_CANCELLED

import tls from 'node:tls';
import { impersonate, isSupported } from 'tls-impersonate';

const STORE_NAME = 'download-jobs';
const SYNC_TIMEOUT_MS = 30_000; // 30s — Lambda sync function limit
const DEFAULT_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

// === Blob helpers ===

async function getStore() {
  const { getStore: getStoreFn } = await import('@netlify/blobs');
  return getStoreFn(STORE_NAME);
}

async function setStatus(jobId, status, extra = {}) {
  const store = await getStore();
  // CRITICAL: status field must take precedence — don't let `extra.status` override it
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

async function enqueueJob(jobSpec) {
  const store = await getStore();
  await store.setJSON(`queue/pending/${jobSpec.job_id}`, jobSpec);
}

async function dequeueJob(jobId) {
  const store = await getStore();
  await store.delete(`queue/pending/${jobId}`);
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

async function getResult(jobId) {
  const store = await getStore();
  const blob = await store.get(`result/${jobId}`, { type: 'arrayBuffer' });
  const metadata = await store.getMetadata(`result/${jobId}`);
  return { blob, metadata };
}

async function listJobs(filterStatus = null, limit = 50) {
  const store = await getStore();
  const list = await store.list();
  const statusKeys = (list.blobs || []).filter(b => b.key.startsWith('status/'));
  const jobs = [];
  for (const k of statusKeys.slice(0, limit)) {
    try {
      const s = await store.get(k.key, { type: 'json' });
      if (s && (!filterStatus || s.status === filterStatus)) {
        jobs.push(s);
      }
    } catch {}
  }
  // Sort by updated_at descending
  jobs.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  return jobs;
}

// === Download implementation ===

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

async function downloadUrl(url, method, userAgent, timeoutMs = SYNC_TIMEOUT_MS) {
  if (method === 'chrome_impersonate') {
    return downloadWithTlsImpersonate(url, userAgent, timeoutMs);
  }
  // Default: undici fetch with timeout via AbortController
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error('Download timeout')), timeoutMs);
  try {
    const r = await fetch(url, {
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
      tls: null,
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function downloadWithTlsImpersonate(targetUrl, userAgent, timeoutMs) {
  if (!isSupported()) throw new Error('tls-impersonate not supported on this runtime');
  const { tlsOptions, unsupported } = impersonate(CHROME_120_SPEC);

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
      `GET ${path} HTTP/1.1\r\n` +
      `Host: ${url.hostname}\r\n` +
      `User-Agent: ${userAgent}\r\n` +
      `Accept: */*\r\n` +
      `Connection: close\r\n\r\n`
    );

    const chunks = [];
    await new Promise((resolve) => {
      tlsSocket.on('data', (c) => chunks.push(c));
      tlsSocket.on('end', resolve);
      tlsSocket.on('close', resolve);
      tlsSocket.on('error', resolve); // don't throw on socket errors after connect
    });

    const raw = Buffer.concat(chunks).toString('utf8');
    const headerEnd = raw.indexOf('\r\n\r\n');
    const rawHeaders = headerEnd >= 0 ? raw.substring(0, headerEnd) : '';
    const body = headerEnd >= 0 ? raw.substring(headerEnd + 4) : raw;
    const status = parseInt(rawHeaders.match(/^HTTP\/[\d.]+ (\d+)/)?.[1] || '0');
    const ctMatch = rawHeaders.match(/^content-type:\s*(.+)$/im);
    const contentType = ctMatch ? ctMatch[1].trim() : 'application/octet-stream';

    return {
      status,
      body: Buffer.from(body, 'utf8'),
      content_type: contentType,
      headers: { 'content-type': contentType },
      tls: {
        negotiated: {
          protocol: tlsSocket.getProtocol(),
          cipher: tlsSocket.getCipher()?.name,
          alpn: tlsSocket.alpnProtocol,
        },
        unsupported_features: unsupported,
      },
    };
  } finally {
    clearTimeout(timeout);
    tlsSocket.destroy();
  }
}

// === Handler ===

export default async function handler(req, context) {
  const url = new URL(req.url);
  const params = url.searchParams;
  const httpMethod = req.method;

  // === GET: status / result / list ===
  if (httpMethod === 'GET') {
    // List mode
    if (params.get('list') === '1') {
      const filterStatus = params.get('status');
      const limit = parseInt(params.get('limit') || '50');
      const jobs = await listJobs(filterStatus, limit);
      return Response.json({ count: jobs.length, jobs }, { headers: { 'x-job-count': String(jobs.length) } });
    }

    const jobId = params.get('job_id');
    if (!jobId) {
      return Response.json({
        error: 'missing job_id parameter (or use ?list=1 to list jobs)',
        usage: {
          status: 'GET ?job_id=<id>',
          result: 'GET ?job_id=<id>&result=1',
          list:   'GET ?list=1[&status=pending|complete|error]',
        },
      }, { status: 400 });
    }

    const status = await getStatus(jobId);
    if (!status) {
      return Response.json({ error: 'job not found', job_id: jobId }, { status: 404 });
    }

    // Result retrieval
    if (params.get('result') === '1') {
      if (status.status !== 'complete') {
        return Response.json({
          error: 'result not available',
          job_id: jobId,
          status: status.status,
        }, { status: 409 });
      }
      const { blob, metadata } = await getResult(jobId);
      if (!blob) {
        return Response.json({ error: 'result blob missing', job_id: jobId }, { status: 404 });
      }
      return new Response(blob, {
        headers: {
          'content-type': metadata?.content_type || 'application/octet-stream',
          'x-job-id': jobId,
          'x-original-size': String(metadata?.size || status.size || 0),
          'x-stored-at': metadata?.stored_at || '',
        },
      });
    }

    return Response.json(status, { headers: { 'x-job-id': jobId } });
  }

  // === DELETE: cancel ===
  if (httpMethod === 'DELETE') {
    const jobId = params.get('job_id');
    if (!jobId) return Response.json({ error: 'missing job_id' }, { status: 400 });
    const current = await getStatus(jobId);
    if (!current) return Response.json({ error: 'job not found' }, { status: 404 });
    if (['complete', 'error', 'cancelled'].includes(current.status)) {
      return Response.json({ error: 'cannot cancel terminal job', status: current.status }, { status: 409 });
    }
    // Remove from pending queue (if not yet picked up by build)
    await dequeueJob(jobId);
    console.log(`JOB_CANCELLED job_id=${jobId}`);
    const updated = await setStatus(jobId, 'cancelled', { ...current, cancelled_at: new Date().toISOString() });
    return Response.json(updated);
  }

  // === POST: queue a download ===
  if (httpMethod === 'POST') {
    const targetUrl = params.get('url');
    const downloadMethod = params.get('method') || 'fetch';
    const asyncMode = params.get('async') === '1';
    const userAgent = params.get('ua') || DEFAULT_UA;
    const timeoutMs = parseInt(params.get('timeout') || String(SYNC_TIMEOUT_MS));

    if (!targetUrl) {
      return Response.json({
        error: 'missing url parameter',
        usage: 'POST ?url=<url>[&method=fetch|chrome_impersonate][&async=1][&ua=...][&timeout=N]',
        modes: {
          sync:  'async=0 (default) — function downloads inline, up to 30s. Returns final status.',
          async: 'async=1 — queues job, returns immediately. Build plugin processes (up to 15 min). Client polls for status.',
        },
      }, { status: 400 });
    }

    // Generate job_id
    const jobId = `job-${Date.now()}-${Math.random().toString(36).substring(2, 10)}`;
    const createdAt = new Date().toISOString();

    const jobSpec = {
      job_id: jobId,
      target_url: targetUrl,
      method: downloadMethod,
      user_agent: userAgent,
      timeout_ms: timeoutMs,
      created_at: createdAt,
      async: asyncMode,
      attempts: 0,
    };

    // Persist initial status
    await setStatus(jobId, 'pending', jobSpec);
    console.log(`JOB_QUEUED job_id=${jobId} url=${targetUrl} method=${downloadMethod} async=${asyncMode}`);

    // === ASYNC mode: queue and return immediately ===
    if (asyncMode) {
      await enqueueJob(jobSpec);
      return Response.json({
        ...jobSpec,
        status: 'pending',
        poll_url: `${url.pathname}?job_id=${jobId}`,
        result_url: `${url.pathname}?job_id=${jobId}&result=1`,
        message: 'Job queued. A build process will pick it up. Poll poll_url until status=complete, then fetch result_url.',
      }, { status: 202, headers: { 'x-job-id': jobId } });
    }

    // === SYNC mode: download inline (up to 30s) ===
    await setStatus(jobId, 'downloading', { ...jobSpec, started_at: new Date().toISOString() });
    console.log(`JOB_START job_id=${jobId} url=${targetUrl}`);

    try {
      const downloadStart = Date.now();
      const result = await downloadUrl(targetUrl, downloadMethod, userAgent, timeoutMs);
      const downloadMs = Date.now() - downloadStart;

      // Store the result
      await setResult(jobId, result.body, result.content_type, {
        target_url: targetUrl,
        method: downloadMethod,
        upstream_status: String(result.status),
      });

      // Update status to complete
      const finalStatus = await setStatus(jobId, 'complete', {
        ...jobSpec,
        download_ms: downloadMs,
        size: result.body.length,
        content_type: result.content_type,
        upstream_status: result.status,
        completed_at: new Date().toISOString(),
        result_blob_key: `result/${jobId}`,
      });

      console.log(`JOB_COMPLETE job_id=${jobId} status=${result.status} size=${result.body.length} ms=${downloadMs}`);

      return Response.json({
        ...finalStatus,
        result_url: `${url.pathname}?job_id=${jobId}&result=1`,
        message: 'Download complete. Fetch result_url to retrieve the data (free via Blobs API).',
      }, { headers: { 'x-job-id': jobId, 'x-blob-key': `result/${jobId}` } });

    } catch (e) {
      console.error(`JOB_ERROR job_id=${jobId} error=${e.message}`);
      const errorStatus = await setStatus(jobId, 'error', {
        ...jobSpec,
        error: e.message,
        failed_at: new Date().toISOString(),
      });
      return Response.json(errorStatus, { status: 502, headers: { 'x-job-id': jobId } });
    }
  }

  return Response.json({ error: `method ${httpMethod} not allowed` }, { status: 405 });
}

export const config = {
  path: ['/api/download', '/.netlify/functions/download'],
};
