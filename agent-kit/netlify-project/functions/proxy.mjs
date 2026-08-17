// API/Request Proxy — proper implementation
//
// A configurable HTTP proxy that fetches a target URL and returns the response.
// Supports all HTTP methods, custom headers, timeout, TLS impersonation.
//
// Endpoints:
//   ANY /api/proxy?url=<url>[&mode=direct|blob|metadata][&method=fetch|chrome_impersonate]
//                [&ua=...][&timeout=N][&follow_redirects=0]
//
//   GET /api/proxy?blob_key=<key>
//     → Retrieves a previously stored blob (from a mode=blob request)
//
//   GET /api/proxy?list=1[&limit=N]
//     → Lists recent proxy results (from blob mode)
//
// Modes:
//   mode=direct (default)
//     → Fetches URL, returns response inline (passthrough of body + content-type + headers)
//     → Best for: small responses, low-latency use, browser-side requests
//     → Cost: bandwidth = 20 credits/GB egress
//
//   mode=blob
//     → Fetches URL, writes response to Netlify Blob, returns blob metadata
//     → Best for: large responses, async retrieval, persistent caching
//     → Cost: 0 credits (blob storage is free, API read is free)
//     → Client then fetches: GET /api/proxy?blob_key=<key>
//
//   mode=metadata
//     → Fetches URL, returns ONLY metadata (status, headers, size, tls fingerprint)
//     → Best for: HEAD-like inspection, link checking, JA3 verification
//     → Body is discarded; useful when you don't need the content
//
// Methods (TLS impersonation):
//   method=fetch (default)
//     → Node's built-in fetch (undici). Fast but JA3 = 1808993db60a053eb8ce0eb1c51750d6 (Node fingerprint)
//   method=chrome_impersonate
//     → Uses tls-impersonate with Chrome 120 ClientHello. JA3 = 947eccbc4e2adea862cd37bf77342106 (Chrome-like)
//     → Best for: scraping bot-protected sites that check JA3
//     → Note: For methods other than GET/HEAD, falls back to fetch() (TLS socket impl only supports GET)
//
// Custom request headers:
//   Pass as query params prefixed with "h_". Examples:
//     &h_authorization=Bearer+xxx     →  sets Authorization: Bearer xxx
//     &h_x_api_key=abc123             →  sets X-Api-Key: abc123
//     &h_accept=application/json      →  sets Accept: application/json
//   User-Agent can be set via &ua=... (shortcut for &h_user_agent=...)
//
// Examples:
//   # Simple direct proxy (returns upstream body inline)
//   curl '/api/proxy?url=https://example.com'
//
//   # Proxy a POST request with body (forwards body to upstream)
//   curl -X POST '/api/proxy?url=https://httpbin.org/post' -d '{"hello":"world"}'
//
//   # Use blob mode for large responses (returns metadata, not body)
//   curl '/api/proxy?url=https://example.com/large-file&mode=blob'
//   # Then retrieve:
//   curl '/api/proxy?blob_key=proxy-...'
//
//   # Inspect TLS fingerprint without downloading body
//   curl '/api/proxy?url=https://tls.peet.ws/api/all&method=chrome_impersonate&mode=metadata'
//
//   # Custom headers (e.g., auth)
//   curl '/api/proxy?url=https://api.github.com/user&h_authorization=token+ghp_xxx'

import tls from 'node:tls';
import { impersonate, isSupported } from 'tls-impersonate';

const STORE_NAME = 'proxy-cache';

// Chrome 120 ClientHello spec
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

const DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const DEFAULT_TIMEOUT_MS = 30_000;

// === Blob helpers ===

async function getStore() {
  const { getStore: getStoreFn } = await import('@netlify/blobs');
  return getStoreFn(STORE_NAME);
}

async function storeResult(body, contentType, targetUrl, method, status, requestMethod) {
  const store = await getStore();
  const blobKey = `proxy-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
  await store.set(blobKey, body, {
    metadata: {
      content_type: contentType,
      size: String(body.length),
      target_url: targetUrl,
      method,
      request_method: requestMethod,
      upstream_status: String(status),
      stored_at: new Date().toISOString(),
    },
  });
  await store.setJSON('latest', {
    blob_key: blobKey,
    target_url: targetUrl,
    method,
    request_method: requestMethod,
    upstream_status: status,
    size: body.length,
    stored_at: new Date().toISOString(),
  });
  return blobKey;
}

async function retrieveResult(blobKey) {
  const store = await getStore();
  const blob = await store.get(blobKey, { type: 'arrayBuffer' });
  const metadata = await store.getMetadata(blobKey);
  return { blob, metadata };
}

async function listResults(limit = 50) {
  const store = await getStore();
  const list = await store.list();
  const keys = (list.blobs || []).filter(b => b.key.startsWith('proxy-'));
  return keys.slice(0, limit).map(k => ({ key: k.key, size: k.size, last_modified: k.last_modified }));
}

// === Header parsing ===

function parseCustomHeaders(params) {
  const headers = {};
  for (const [key, value] of params.entries()) {
    if (key.startsWith('h_')) {
      const headerName = key.substring(2).replace(/_/g, '-');
      headers[headerName] = value;
    }
  }
  return headers;
}

// === Fetch implementations ===

async function fetchTarget(targetUrl, method, userAgent, reqMethod, reqBody, customHeaders, followRedirects, timeoutMs) {
  // For non-GET/HEAD methods, always use undici fetch (TLS socket impl only supports GET)
  if (method === 'chrome_impersonate' && (reqMethod === 'GET' || reqMethod === 'HEAD')) {
    return fetchWithTlsImpersonate(targetUrl, userAgent, timeoutMs);
  }
  return fetchWithUndici(targetUrl, userAgent, reqMethod, reqBody, customHeaders, followRedirects, timeoutMs);
}

async function fetchWithUndici(targetUrl, userAgent, reqMethod, reqBody, customHeaders, followRedirects, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error('Request timeout')), timeoutMs);
  try {
    const headers = {
      'User-Agent': userAgent,
      'Accept': '*/*',
      ...customHeaders,
    };
    const fetchOpts = {
      method: reqMethod,
      headers,
      redirect: followRedirects ? 'follow' : 'manual',
      signal: controller.signal,
    };
    if (reqMethod !== 'GET' && reqMethod !== 'HEAD' && reqBody) {
      fetchOpts.body = reqBody;
    }
    const r = await fetch(targetUrl, fetchOpts);
    const buf = Buffer.from(await r.arrayBuffer());
    return {
      status: r.status,
      body: buf,
      content_type: r.headers.get('content-type') || 'application/octet-stream',
      headers: Object.fromEntries(r.headers.entries()),
      tls: null,
      redirected: r.redirected,
      final_url: r.url,
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchWithTlsImpersonate(targetUrl, userAgent, timeoutMs) {
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
      tlsSocket.on('error', resolve);
    });

    const raw = Buffer.concat(chunks).toString('utf8');
    const headerEnd = raw.indexOf('\r\n\r\n');
    const rawHeaders = headerEnd >= 0 ? raw.substring(0, headerEnd) : '';
    const body = headerEnd >= 0 ? raw.substring(headerEnd + 4) : raw;
    const status = parseInt(rawHeaders.match(/^HTTP\/[\d.]+ (\d+)/)?.[1] || '0');

    const headerLines = rawHeaders.split('\r\n').slice(1).filter(Boolean);
    const headerObj = {};
    for (const line of headerLines) {
      const idx = line.indexOf(':');
      if (idx > 0) {
        const key = line.substring(0, idx).trim().toLowerCase();
        const val = line.substring(idx + 1).trim();
        headerObj[key] = val;
      }
    }

    return {
      status,
      body: Buffer.from(body, 'utf8'),
      content_type: headerObj['content-type'] || 'application/octet-stream',
      headers: headerObj,
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

// === Extract TLS fingerprint from response body (if it's a TLS echo service) ===

function extractTlsFingerprint(body) {
  try {
    const text = body.toString('utf8');
    if (!text.includes('ja3')) return null;
    const j = JSON.parse(text);
    if (j.tls?.ja3_hash) {
      return {
        ja3_hash: j.tls.ja3_hash,
        ja4: j.tls.ja4,
        http_version: j.http_version,
        ip: j.ip,
        ciphers_count: j.tls?.ciphers?.length,
        extensions_count: j.tls?.extensions?.length,
      };
    }
  } catch {}
  return null;
}

// === Main handler ===

export default async function handler(req, context) {
  const url = new URL(req.url);
  const params = url.searchParams;

  // === Blob retrieval mode ===
  const blobKey = params.get('blob_key');
  if (blobKey) {
    try {
      const { blob, metadata } = await retrieveResult(blobKey);
      if (!blob) {
        return Response.json({ error: 'blob not found', blob_key: blobKey }, { status: 404 });
      }
      return new Response(blob, {
        headers: {
          'content-type': metadata?.content_type || 'application/octet-stream',
          'x-blob-key': blobKey,
          'x-stored-at': metadata?.stored_at || '',
          'x-target-url': metadata?.target_url || '',
          'x-upstream-status': metadata?.upstream_status || '',
        },
      });
    } catch (e) {
      return Response.json({ error: e.message, blob_key: blobKey }, { status: 500 });
    }
  }

  // === List mode ===
  if (params.get('list') === '1') {
    const limit = parseInt(params.get('limit') || '50');
    const results = await listResults(limit);
    return Response.json({ count: results.length, results });
  }

  // === Proxy mode ===
  const targetUrl = params.get('url');
  if (!targetUrl) {
    return Response.json({
      error: 'missing url parameter',
      usage: {
        proxy: 'ANY ?url=<target>[&mode=direct|blob|metadata][&method=fetch|chrome_impersonate][&ua=...][&timeout=N][&follow_redirects=0][&h_<header>=<value>]',
        retrieve_blob: 'GET ?blob_key=<key>',
        list: 'GET ?list=1[&limit=N]',
      },
      modes: {
        direct: 'Returns response body inline (default). Cost: 20 cr/GB egress.',
        blob: 'Stores response in blob, returns metadata. Cost: 0 credits (free egress via Blobs API).',
        metadata: 'Returns only headers + TLS fingerprint, discards body. Cost: negligible.',
      },
      methods: {
        fetch: 'Default Node undici fetch (fast, JA3 = 1808993...)',
        chrome_impersonate: 'tls-impersonate with Chrome 120 spec (JA3 = 947eccc...). Only for GET/HEAD.',
      },
      headers: 'Pass custom headers as &h_<header_name>=<value>. Example: &h_authorization=Bearer+xxx',
    }, { status: 400 });
  }

  const mode = params.get('mode') || 'direct';
  const method = params.get('method') || 'fetch';
  const userAgent = params.get('ua') || DEFAULT_UA;
  const headersOnly = params.get('headers_only') === '1' || mode === 'metadata';
  const followRedirects = params.get('follow_redirects') !== '0';
  const timeoutMs = Math.min(parseInt(params.get('timeout') || String(DEFAULT_TIMEOUT_MS)), DEFAULT_TIMEOUT_MS);
  const reqMethod = req.method;
  const customHeaders = parseCustomHeaders(params);
  const reqBody = reqMethod !== 'GET' && reqMethod !== 'HEAD' ? await req.text() : null;

  if (!['direct', 'blob', 'metadata'].includes(mode)) {
    return Response.json({ error: `invalid mode: ${mode}`, valid_modes: ['direct', 'blob', 'metadata'] }, { status: 400 });
  }

  // chrome_impersonate only supports GET/HEAD — fall back to fetch for other methods
  const effectiveMethod = (method === 'chrome_impersonate' && reqMethod !== 'GET' && reqMethod !== 'HEAD')
    ? 'fetch' : method;

  const start = Date.now();
  console.log(`[proxy] ${reqMethod} ${targetUrl} mode=${mode} method=${effectiveMethod}${effectiveMethod !== method ? ` (fallback from ${method})` : ''}`);

  try {
    const result = await fetchTarget(targetUrl, effectiveMethod, userAgent, reqMethod, reqBody, customHeaders, followRedirects, timeoutMs);
    const elapsedMs = Date.now() - start;

    const tlsSeenByServer = extractTlsFingerprint(result.body);

    // Metadata mode: discard body, return only headers + TLS info
    if (headersOnly) {
      console.log(`[proxy] metadata: status=${result.status} size=${result.body.length} ms=${elapsedMs}`);
      return Response.json({
        target_url: targetUrl,
        method: reqMethod,
        upstream_status: result.status,
        upstream_headers: result.headers,
        body_size: result.body.length,
        content_type: result.content_type,
        tls_local: result.tls,
        tls_seen_by_server: tlsSeenByServer,
        elapsed_ms: elapsedMs,
        redirected: result.redirected,
        final_url: result.final_url,
      });
    }

    // Blob mode: store body, return metadata
    if (mode === 'blob') {
      const blobKey = await storeResult(result.body, result.content_type, targetUrl, effectiveMethod, result.status, reqMethod);
      console.log(`[proxy] blob: blob_key=${blobKey} size=${result.body.length} ms=${elapsedMs}`);
      return Response.json({
        target_url: targetUrl,
        method: reqMethod,
        upstream_status: result.status,
        content_type: result.content_type,
        size: result.body.length,
        blob_key: blobKey,
        retrieve_url: `${url.pathname}?blob_key=${blobKey}`,
        tls_local: result.tls,
        tls_seen_by_server: tlsSeenByServer,
        elapsed_ms: elapsedMs,
      }, { headers: { 'x-blob-key': blobKey } });
    }

    // Direct mode: passthrough
    console.log(`[proxy] direct: status=${result.status} size=${result.body.length} ms=${elapsedMs}`);
    return new Response(result.body, {
      status: result.status,
      headers: {
        'content-type': result.content_type,
        'x-upstream-status': String(result.status),
        'x-elapsed-ms': String(elapsedMs),
        'x-target-url': targetUrl,
        ...(result.tls?.negotiated ? {
          'x-tls-protocol': result.tls.negotiated.protocol || '',
          'x-tls-cipher': result.tls.negotiated.cipher || '',
          'x-tls-alpn': result.tls.negotiated.alpn || '',
        } : {}),
        ...(tlsSeenByServer ? {
          'x-ja3-hash': tlsSeenByServer.ja3_hash || '',
          'x-ja4': tlsSeenByServer.ja4 || '',
        } : {}),
      },
    });

  } catch (e) {
    console.error(`[proxy] error: ${e.message}`);
    return Response.json({
      error: e.message,
      target_url: targetUrl,
      method,
      elapsed_ms: Date.now() - start,
    }, { status: 502 });
  }
}

export const config = {
  path: ['/api/proxy', '/.netlify/functions/proxy'],
};
