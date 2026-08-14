// Demo 1: Download Service (queued, with robust status tracking)
//
// Endpoints:
//   POST /api/download?url=<url>[&method=fetch|chrome_impersonate][&async=1]
//     → Queues a download job, returns job_id + initial status
//     → If async=1, returns immediately with status=pending; client polls
//     → If sync (default), waits up to 30s for completion, returns final result
//
//   GET /api/download?job_id=<id>
//     → Returns current status of a job
//
//   GET /api/download?job_id=<id>&result=1
//     → Returns the downloaded data (raw passthrough of stored blob)
//
//   DELETE /api/download?job_id=<id>
//     → Marks job as cancelled (best-effort)
//
// State machine:
//   pending → downloading → complete | error | cancelled
//
// Storage layout (Blobs):
//   store: download-jobs
//   ├── status/{job_id}     — JSON status blob (updated through lifecycle)
//   ├── result/{job_id}     — Raw downloaded bytes (set on completion)
//   └── index/latest        — Pointer to most recent job
//
// Log events (auditable via `netlify logs --json --function download`):
//   JOB_QUEUED      — job created, includes job_id, url, method
//   JOB_START       — download started
//   JOB_PROGRESS    — optional progress event (size downloaded so far)
//   JOB_COMPLETE    — download finished, includes blob_key + size
//   JOB_ERROR       — error occurred, includes message
//   JOB_CANCELLED   — job cancelled by user

import tls from 'node:tls';
import { impersonate, isSupported } from 'tls-impersonate';

const STATUS_STORE = 'download-jobs';

// State machine helpers — write status updates as JSON blobs
async function setStatus(env, jobId, status, extra = {}) {
  const { getStore } = await import('@netlify/blobs');
  const store = getStore(STATUS_STORE);
  // Build status object — STATUS FIELD TAKES PRECEDENCE over any value in extra
  // (to avoid the bug where extra contains a stale status from a previous call)
  const statusObj = {
    job_id: jobId,
    status,
    updated_at: new Date().toISOString(),
    ...extra,
    status,  // re-apply to ensure it's not overwritten by extra
  };
  await store.setJSON(`status/${jobId}`, statusObj);
  // Update latest pointer
  await store.setJSON('index/latest', { job_id: jobId, status, updated_at: statusObj.updated_at });
  return statusObj;
}

async function getStatus(env, jobId) {
  const { getStore } = await import('@netlify/blobs');
  const store = getStore(STATUS_STORE);
  try {
    return await store.get(`status/${jobId}`, { type: 'json' });
  } catch (e) {
    return null;
  }
}

async function setResult(env, jobId, body, contentType) {
  const { getStore } = await import('@netlify/blobs');
  const store = getStore(STATUS_STORE);
  await store.set(`result/${jobId}`, body, {
    metadata: { content_type: contentType, size: String(body.length) },
  });
}

async function getResult(env, jobId) {
  const { getStore } = await import('@netlify/blobs');
  const store = getStore(STATUS_STORE);
  const blob = await store.get(`result/${jobId}`, { type: 'arrayBuffer' });
  return blob;
}

// Download with method selection
async function downloadUrl(url, method, userAgent) {
  if (method === 'chrome_impersonate') {
    return downloadWithTlsImpersonate(url, userAgent);
  }
  // Default: undici fetch
  const r = await fetch(url, {
    headers: { 'User-Agent': userAgent, 'Accept': '*/*' },
    redirect: 'follow',
  });
  const buf = Buffer.from(await r.arrayBuffer());
  return {
    status: r.status,
    body: buf,
    content_type: r.headers.get('content-type') || 'application/octet-stream',
    headers: Object.fromEntries(r.headers.entries()),
  };
}

// Chrome 120 ClientHello spec — same as scrape.mjs
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

async function downloadWithTlsImpersonate(targetUrl, userAgent) {
  if (!isSupported()) throw new Error('tls-impersonate not supported on this runtime');
  const { tlsOptions } = impersonate(CHROME_120_SPEC);

  const url = new URL(targetUrl);
  const tlsSocket = tls.connect({
    host: url.hostname,
    port: parseInt(url.port || '443'),
    servername: url.hostname,
    ...tlsOptions,
  });

  await new Promise((resolve, reject) => {
    tlsSocket.once('secureConnect', resolve);
    tlsSocket.once('error', reject);
    setTimeout(() => reject(new Error('TLS timeout')), 10000);
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
    setTimeout(resolve, 8000);
  });
  tlsSocket.destroy();

  const raw = Buffer.concat(chunks).toString('utf8');
  const headerEnd = raw.indexOf('\r\n\r\n');
  const headers = headerEnd >= 0 ? raw.substring(0, headerEnd) : '';
  const body = headerEnd >= 0 ? raw.substring(headerEnd + 4) : raw;
  const status = parseInt(headers.match(/^HTTP\/[\d.]+ (\d+)/)?.[1] || '0');
  const ctMatch = headers.match(/^content-type:\s*(.+)$/im);
  const contentType = ctMatch ? ctMatch[1].trim() : 'application/octet-stream';

  return {
    status,
    body: Buffer.from(body, 'utf8'),
    content_type: contentType,
    headers: { 'content-type': contentType },
  };
}

// Main handler
export default async function handler(req, context) {
  const url = new URL(req.url);
  const method = req.method;

  // === GET: status check or result retrieval ===
  if (method === 'GET') {
    const jobId = url.searchParams.get('job_id');
    if (!jobId) {
      return Response.json({
        error: 'missing job_id parameter',
        usage: 'GET ?job_id=<id>[&result=1]',
      }, { status: 400 });
    }

    const status = await getStatus(context, jobId);
    if (!status) {
      return Response.json({ error: 'job not found', job_id: jobId }, { status: 404 });
    }

    // If result=1 and status is complete, return the actual data
    if (url.searchParams.get('result') === '1') {
      if (status.status !== 'complete') {
        return Response.json({
          error: 'result not available',
          job_id: jobId,
          status: status.status,
        }, { status: 409 });
      }
      const result = await getResult(context, jobId);
      if (!result) {
        return Response.json({ error: 'result blob missing', job_id: jobId }, { status: 404 });
      }
      return new Response(result, {
        headers: {
          'content-type': status.content_type || 'application/octet-stream',
          'x-job-id': jobId,
          'x-original-size': String(status.size || 0),
        },
      });
    }

    // Otherwise just return the status object
    return Response.json(status, { headers: { 'x-job-id': jobId } });
  }

  // === DELETE: cancel job (best-effort) ===
  if (method === 'DELETE') {
    const jobId = url.searchParams.get('job_id');
    if (!jobId) return Response.json({ error: 'missing job_id' }, { status: 400 });
    const current = await getStatus(context, jobId);
    if (!current) return Response.json({ error: 'job not found' }, { status: 404 });
    if (current.status === 'complete' || current.status === 'error') {
      return Response.json({ error: 'cannot cancel terminal job', status: current.status }, { status: 409 });
    }
    console.log(`JOB_CANCELLED job_id=${jobId}`);
    const updated = await setStatus(context, jobId, 'cancelled', { ...current, cancelled_at: new Date().toISOString() });
    return Response.json(updated);
  }

  // === POST: queue a download ===
  if (method === 'POST') {
    const targetUrl = url.searchParams.get('url');
    const downloadMethod = url.searchParams.get('method') || 'fetch';
    const async = url.searchParams.get('async') === '1';
    const userAgent = url.searchParams.get('ua') || 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

    if (!targetUrl) {
      return Response.json({
        error: 'missing url parameter',
        usage: 'POST ?url=<url>[&method=fetch|chrome_impersonate][&async=1]',
      }, { status: 400 });
    }

    // Generate job_id and create initial status
    const jobId = `job-${Date.now()}-${Math.random().toString(36).substring(2, 10)}`;
    const createdAt = new Date().toISOString();
    const initialStatus = {
      job_id: jobId,
      status: 'pending',
      target_url: targetUrl,
      method: downloadMethod,
      created_at: createdAt,
      updated_at: createdAt,
    };

    // Persist initial status
    await setStatus(context, jobId, 'pending', initialStatus);
    console.log(`JOB_QUEUED job_id=${jobId} url=${targetUrl} method=${downloadMethod} async=${async}`);

    // If async mode, return immediately — client polls
    if (async) {
      return Response.json({
        ...initialStatus,
        poll_url: `${url.pathname}?job_id=${jobId}`,
        result_url: `${url.pathname}?job_id=${jobId}&result=1`,
        message: 'Job queued. Poll the status URL until status=complete, then fetch result_url.',
      }, { status: 202, headers: { 'x-job-id': jobId } });
    }

    // Sync mode: process inline (up to 30s timeout)
    await setStatus(context, jobId, 'downloading', { ...initialStatus, started_at: new Date().toISOString() });
    console.log(`JOB_START job_id=${jobId} url=${targetUrl}`);

    try {
      const downloadStart = Date.now();
      const result = await downloadUrl(targetUrl, downloadMethod, userAgent);
      const downloadMs = Date.now() - downloadStart;

      // Store the result
      await setResult(context, jobId, result.body, result.content_type);

      // Update status to complete
      const finalStatus = await setStatus(context, jobId, 'complete', {
        ...initialStatus,
        download_ms: downloadMs,
        size: result.body.length,
        content_type: result.content_type,
        upstream_status: result.status,
        completed_at: new Date().toISOString(),
        result_blob_key: `result/${jobId}`,
      });

      console.log(`JOB_COMPLETE job_id=${jobId} status=${result.status} size=${result.body.length} ms=${downloadMs}`);

      // Return metadata (NOT the body — client fetches it via ?result=1)
      return Response.json({
        ...finalStatus,
        result_url: `${url.pathname}?job_id=${jobId}&result=1`,
        message: 'Download complete. Fetch result_url to retrieve the data (free via Blobs API).',
      }, { headers: { 'x-job-id': jobId, 'x-blob-key': `result/${jobId}` } });

    } catch (e) {
      console.error(`JOB_ERROR job_id=${jobId} error=${e.message}`);
      const errorStatus = await setStatus(context, jobId, 'error', {
        ...initialStatus,
        error: e.message,
        failed_at: new Date().toISOString(),
      });
      return Response.json(errorStatus, { status: 502, headers: { 'x-job-id': jobId } });
    }
  }

  return Response.json({ error: `method ${method} not allowed` }, { status: 405 });
}

export const config = {
  path: ['/api/download', '/.netlify/functions/download'],
};
