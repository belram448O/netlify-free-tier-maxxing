// Demo 2: API/Request Proxy
//
// A configurable HTTP proxy that fetches a target URL and returns the response.
// Useful for: scraping, CORS bypass, TLS impersonation, request inspection.
//
// Endpoints:
//   GET  /api/proxy?url=<url>[&mode=direct|blob][&method=fetch|chrome_impersonate]
//                  [&ua=...][&headers_only=1][&timeout=N]
//   POST /api/proxy?url=<url>  (forwards POST body to target)
//
// Modes:
//   mode=direct (default)
//     → Fetches URL, returns response inline (passthrough of body + content-type)
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
//     → Best for: HEAD-like inspection, link checking
//     → Body is discarded; useful when you don't need the content
//
// Methods (TLS impersonation):
//   method=fetch (default)
//     → Node's built-in fetch (undici). Fast but JA3 = 1808993db60a053eb8ce0eb1c51750d6 (Node fingerprint)
//   method=chrome_impersonate
//     → Uses tls-impersonate with Chrome 120 ClientHello. JA3 = 947eccbc4e2adea862cd37bf77342106 (Chrome-like)
//     → Best for: scraping bot-protected sites that check JA3
//
// Blob retrieval:
//   GET /api/proxy?blob_key=<key>
//     → Returns the stored blob content (passthrough)
//     → Use this to fetch the result when mode=blob was used
//
// Examples:
//   curl '/api/proxy?url=https://example.com'
//     → Returns example.com HTML directly
//
//   curl '/api/proxy?url=https://example.com&mode=blob'
//     → Returns { blob_key: "proxy-...", size: 1234, content_type: "text/html" }
//   curl '/api/proxy?blob_key=proxy-...'
//     → Returns the stored HTML
//
//   curl '/api/proxy?url=https://tls.peet.ws/api/all&method=chrome_impersonate&mode=metadata'
//     → Returns { status: 200, tls: { ja3_hash: "947eccbc...", ... }, headers: {...}, size: 7554 }

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

// Fetch with method selection
async function fetchTarget(targetUrl, method, userAgent, reqMethod = 'GET', reqBody = null) {
  if (method === 'chrome_impersonate') {
    return fetchWithTlsImpersonate(targetUrl, userAgent, reqMethod, reqBody);
  }
  // Default: undici fetch
  const fetchOpts = {
    method: reqMethod,
    headers: { 'User-Agent': userAgent, 'Accept': '*/*' },
    redirect: 'follow',
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
  };
}

async function fetchWithTlsImpersonate(targetUrl, userAgent, reqMethod, reqBody) {
  if (!isSupported()) throw new Error('tls-impersonate not supported on this runtime');
  const { tlsOptions, unsupported } = impersonate(CHROME_120_SPEC);

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
  const reqLine = `${reqMethod} ${path} HTTP/1.1`;
  const headers = [
    reqLine,
    `Host: ${url.hostname}`,
    `User-Agent: ${userAgent}`,
    `Accept: */*`,
    `Connection: close`,
  ];
  if (reqBody && reqMethod !== 'GET' && reqMethod !== 'HEAD') {
    headers.push(`Content-Length: ${Buffer.byteLength(reqBody)}`);
  }
  tlsSocket.write(headers.join('\r\n') + '\r\n\r\n');
  if (reqBody && reqMethod !== 'GET' && reqMethod !== 'HEAD') {
    tlsSocket.write(reqBody);
  }

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
  const rawHeaders = headerEnd >= 0 ? raw.substring(0, headerEnd) : '';
  const body = headerEnd >= 0 ? raw.substring(headerEnd + 4) : raw;
  const status = parseInt(rawHeaders.match(/^HTTP\/[\d.]+ (\d+)/)?.[1] || '0');

  // Parse headers
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
}

// Store result in blob
async function storeResult(body, contentType, targetUrl, method, status) {
  const { getStore } = await import('@netlify/blobs');
  const store = getStore(STORE_NAME);
  const blobKey = `proxy-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
  await store.set(blobKey, body, {
    metadata: {
      content_type: contentType,
      size: String(body.length),
      target_url: targetUrl,
      method,
      upstream_status: String(status),
      stored_at: new Date().toISOString(),
    },
  });
  // Update latest pointer
  await store.setJSON('latest', {
    blob_key: blobKey,
    target_url: targetUrl,
    method,
    upstream_status: status,
    size: body.length,
    stored_at: new Date().toISOString(),
  });
  return blobKey;
}

async function retrieveResult(blobKey) {
  const { getStore } = await import('@netlify/blobs');
  const store = getStore(STORE_NAME);
  const blob = await store.get(blobKey, { type: 'arrayBuffer' });
  const metadata = await store.getMetadata(blobKey);
  return { blob, metadata };
}

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
        },
      });
    } catch (e) {
      return Response.json({ error: e.message, blob_key: blobKey }, { status: 500 });
    }
  }

  // === Proxy mode ===
  const targetUrl = params.get('url');
  if (!targetUrl) {
    return Response.json({
      error: 'missing url parameter',
      usage: {
        proxy: '?url=<target>[&mode=direct|blob|metadata][&method=fetch|chrome_impersonate][&ua=...]',
        retrieve_blob: '?blob_key=<key>',
      },
      modes: {
        direct: 'Returns response body inline (default). Cost: 20 cr/GB egress.',
        blob: 'Stores response in blob, returns metadata. Cost: 0 credits (free egress via Blobs API).',
        metadata: 'Returns only headers + TLS fingerprint, discards body. Cost: negligible.',
      },
      methods: {
        fetch: 'Default Node undici fetch (fast, JA3 = 1808993...)',
        chrome_impersonate: 'tls-impersonate with Chrome 120 spec (JA3 = 947eccc...)',
      },
    }, { status: 400 });
  }

  const mode = params.get('mode') || 'direct';
  const method = params.get('method') || 'fetch';
  const userAgent = params.get('ua') || DEFAULT_UA;
  const headersOnly = params.get('headers_only') === '1' || mode === 'metadata';
  const reqMethod = req.method;
  const reqBody = reqMethod !== 'GET' && reqMethod !== 'HEAD' ? await req.text() : null;

  if (!['direct', 'blob', 'metadata'].includes(mode)) {
    return Response.json({ error: `invalid mode: ${mode}`, valid_modes: ['direct', 'blob', 'metadata'] }, { status: 400 });
  }

  const start = Date.now();
  console.log(`[proxy] ${reqMethod} ${targetUrl} mode=${mode} method=${method}`);

  try {
    const result = await fetchTarget(targetUrl, method, userAgent, reqMethod, reqBody);
    const elapsedMs = Date.now() - start;

    // Extract TLS fingerprint from response body if it's a TLS echo service
    let tlsSeenByServer = null;
    try {
      const bodyText = result.body.toString('utf8');
      if (bodyText.includes('ja3_hash') || bodyText.includes('ja3')) {
        const j = JSON.parse(bodyText);
        if (j.tls?.ja3_hash) {
          tlsSeenByServer = {
            ja3_hash: j.tls.ja3_hash,
            ja4: j.tls.ja4,
            http_version: j.http_version,
            ip: j.ip,
            ciphers_count: j.tls?.ciphers?.length,
            extensions_count: j.tls?.extensions?.length,
          };
        }
      }
    } catch {}

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
      });
    }

    // Blob mode: store body, return metadata
    if (mode === 'blob') {
      const blobKey = await storeResult(result.body, result.content_type, targetUrl, method, result.status);
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
