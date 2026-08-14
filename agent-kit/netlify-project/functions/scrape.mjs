// Netlify Function: on-demand HTTP scraper with Chrome TLS impersonation + Blobs storage
//
// Usage:
//   GET /.netlify/functions/scrape?url=<target>&method=<fetch|chrome_impersonate>&return_blob=<0|1>&ua=<user-agent>
//
// Returns JSON with:
//   - status, target_url, method
//   - tls.seen_by_server (JA3/JA4 from a fingerprinting service like tls.peet.ws)
//   - blob: { blob_key, store, size_bytes } if return_blob=1
//
// Costs (Free plan, preview deploy):
//   - 0 credits per invocation (compute ~0.0006 cr per call, negligible)
//   - 0 credits for blob write
//   - Ingress (download from target URL) is free
//   - Only the JSON response body counts as bandwidth (~500 bytes = 0.00001 cr)

import tls from 'node:tls';
import { impersonate, isSupported } from 'tls-impersonate';

// Chrome 120 ClientHello spec — cipher suites + extensions in wire order
// Source: tls-client Chrome 120 profile + Chrome devtools capture
const CHROME_120_SPEC = {
  cipherSuites: [
    // TLS 1.3
    0x1301, // TLS_AES_128_GCM_SHA256
    0x1302, // TLS_AES_256_GCM_SHA384
    0x1303, // TLS_CHACHA20_POLY1305_SHA256
    // ECDHE (forward-secret)
    0xc02b, // ECDHE-ECDSA-AES128-GCM-SHA256
    0xc02f, // ECDHE-RSA-AES128-GCM-SHA256
    0xc02c, // ECDHE-ECDSA-AES256-GCM-SHA384
    0xc030, // ECDHE-RSA-AES256-GCM-SHA384
    0xcca9, // ECDHE-ECDSA-CHACHA20-POLY1305
    0xcca8, // ECDHE-RSA-CHACHA20-POLY1305
    // Legacy ECDHE
    0xc013, // ECDHE-RSA-AES128-SHA
    0xc014, // ECDHE-RSA-AES256-SHA
    // Legacy RSA
    0x009c, // AES128-GCM-SHA256
    0x009d, // AES256-GCM-SHA384
    0x002f, // AES128-SHA
    0x0035, // AES256-SHA
  ],
  extensions: [
    { type: 0x0016 }, // encrypt_then_mac
    { type: 0x000b }, // ec_point_formats
    { type: 0xff01 }, // renegotiation_info (boringssl)
    { type: 0x0000 }, // server_name
    { type: 0x0017 }, // extended_master_secret
    { type: 0x000d }, // signature_algorithms
    { type: 0x000a }, // supported_groups (elliptic_curves)
    { type: 0x0023 }, // session_ticket
    { type: 0x0010, alpnProtocols: ['h2', 'http/1.1'] }, // ALPN
    { type: 0x002b }, // supported_versions
    { type: 0x002d }, // psk_key_exchange_modes
    { type: 0x0033 }, // key_share
    { type: 0x001c }, // record_size_limit
    { type: 0x0015 }, // compress_certificate
  ],
  supportedGroups: [
    0x001d, // X25519
    0x0017, // secp256r1 (P-256)
    0x0018, // secp384r1 (P-384)
  ],
  signatureAlgorithms: [
    0x0403, // ecdsa_secp256r1_sha256
    0x0804, // rsa_pss_rsae_sha256
    0x0401, // rsa_pkcs1_sha256
    0x0503, // ecdsa_secp384r1_sha384
    0x0501, // rsa_pkcs1_sha384
    0x0803, // rsa_pss_rsae_sha384
    0x0601, // rsa_pkcs1_sha512
    0x0201, // rsa_pkcs1_sha1
  ],
  alpnProtocols: ['h2', 'http/1.1'],
};

const DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

export default async function handler(req, context) {
  const url = new URL(req.url);
  const params = url.searchParams;
  const targetUrl = params.get('url');
  const method = params.get('method') || 'fetch';
  const returnBlob = params.get('return_blob') === '1';
  const userAgent = params.get('ua') || DEFAULT_UA;

  if (!targetUrl) {
    return Response.json({
      error: 'missing url parameter',
      usage: '?url=https://example.com[&method=fetch|chrome_impersonate][&return_blob=1][&ua=...]',
      methods: {
        fetch: 'Default Node undici fetch — fast but fingerprintable as Node.js (JA3 1808993...)',
        chrome_impersonate: 'Uses tls-impersonate with Chrome 120 ClientHello spec — bypasses basic JA3 bot detection',
      },
    }, { status: 400 });
  }

  const start = Date.now();
  console.log(`[scrape] start: method=${method} url=${targetUrl} return_blob=${returnBlob}`);

  let result;
  try {
    if (method === 'fetch') {
      result = await fetchWithDefaultUndici(targetUrl, userAgent);
    } else if (method === 'chrome_impersonate') {
      result = await fetchWithTlsImpersonate(targetUrl, userAgent);
    } else {
      return Response.json({
        error: `unknown method: ${method}`,
        valid_methods: ['fetch', 'chrome_impersonate'],
      }, { status: 400 });
    }
  } catch (e) {
    console.error(`[scrape] error: ${e.message}`);
    return Response.json({
      error: e.message,
      target_url: targetUrl,
      method,
      elapsed_ms: Date.now() - start,
    }, { status: 502 });
  }

  // Optionally write the scraped data to Netlify Blobs (free, unmetered)
  let blobInfo = null;
  if (returnBlob && result.body) {
    try {
      const { getStore } = await import('@netlify/blobs');
      const store = getStore('function-scrapes');
      const blobKey = `scrape-${Date.now()}`;
      await store.setJSON(blobKey, {
        target_url: targetUrl,
        method,
        timestamp: new Date().toISOString(),
        status: result.status,
        response_size: result.body.length,
        response_body: result.body,
        tls_fingerprint: result.tls?.seen_by_server,
        tls_negotiated: result.tls?.negotiated,
      });
      // Update latest pointer for easy retrieval
      await store.setJSON('latest', {
        blob_key: blobKey,
        timestamp: new Date().toISOString(),
        target_url: targetUrl,
        method,
      });
      blobInfo = {
        blob_key: blobKey,
        store: 'function-scrapes',
        size_bytes: result.body.length,
      };
      console.log(`[scrape] wrote blob: ${blobKey} (${result.body.length} bytes)`);
    } catch (e) {
      console.error(`[scrape] blob write error: ${e.message}`);
      blobInfo = { error: e.message };
    }
  }

  const elapsed = Date.now() - start;
  console.log(`[scrape] done in ${elapsed}ms (status=${result.status}, body=${result.body?.length || 0} bytes, blob=${blobInfo?.blob_key || 'none'})`);

  // Return SMALL JSON wrapper — only metadata + blob key (NOT the scraped body)
  // Client fetches the body separately via Blobs API (free) if return_blob=1
  return Response.json({
    ok: result.status >= 200 && result.status < 300,
    status: result.status,
    target_url: targetUrl,
    method,
    elapsed_ms: elapsed,
    response_size: result.body?.length || 0,
    tls: result.tls,
    blob: blobInfo,
  }, {
    headers: {
      'x-blob-key': blobInfo?.blob_key || '',
      'x-elapsed-ms': String(elapsed),
    },
  });
}

// Method 1: Default undici fetch — fast but fingerprintable
async function fetchWithDefaultUndici(targetUrl, userAgent) {
  console.log(`[scrape] using default fetch()`);
  const r = await fetch(targetUrl, {
    headers: { 'User-Agent': userAgent, 'Accept': '*/*' },
    redirect: 'follow',
  });
  const text = await r.text();
  let tlsSeenByServer = null;
  try {
    const j = JSON.parse(text);
    if (j.tls?.ja3_hash) {
      tlsSeenByServer = {
        ja3_hash: j.tls.ja3_hash,
        ja4: j.tls.ja4,
        http_version: j.http_version,
        ip: j.ip,
        ciphers_count: j.tls.ciphers?.length,
        extensions_count: j.tls.extensions?.length,
      };
    }
  } catch {}
  return {
    status: r.status,
    body: text,
    tls: { seen_by_server: tlsSeenByServer },
  };
}

// Method 2: tls-impersonate with Chrome 120 spec — bypasses basic JA3 bot detection
async function fetchWithTlsImpersonate(targetUrl, userAgent) {
  console.log(`[scrape] using tls-impersonate (Chrome 120 spec)`);

  if (!isSupported()) {
    throw new Error('tls-impersonate not supported on this Node runtime (needs Node >= 24.15.0)');
  }

  const { tlsOptions, unsupported } = impersonate(CHROME_120_SPEC);
  if (unsupported?.length > 0) {
    console.log(`[scrape] tls-impersonate unsupported features: ${unsupported.length}`);
    for (const u of unsupported) {
      console.log(`  - ${u.kind} ${u.id}: ${u.reason}`);
    }
  }

  const target = new URL(targetUrl);
  const port = parseInt(target.port || '443');

  const tlsSocket = tls.connect({
    host: target.hostname,
    port,
    servername: target.hostname,
    ...tlsOptions,
  });

  console.log(`[scrape] connecting to ${target.hostname}:${port}...`);
  await new Promise((resolve, reject) => {
    tlsSocket.once('secureConnect', resolve);
    tlsSocket.once('error', reject);
    setTimeout(() => reject(new Error('TLS connect timeout (10s)')), 10000);
  });

  const negotiated = {
    protocol: tlsSocket.getProtocol(),
    cipher: tlsSocket.getCipher()?.name,
    alpn: tlsSocket.alpnProtocol,
  };
  console.log(`[scrape] TLS: ${negotiated.protocol} | ${negotiated.cipher} | ALPN: ${negotiated.alpn}`);

  const path = (target.pathname || '/') + (target.search || '');
  const httpReq =
    `GET ${path} HTTP/1.1\r\n` +
    `Host: ${target.hostname}\r\n` +
    `User-Agent: ${userAgent}\r\n` +
    `Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r\n` +
    `Accept-Language: en-US,en;q=0.9\r\n` +
    `Accept-Encoding: gzip, deflate, br\r\n` +
    `Connection: close\r\n\r\n`;
  tlsSocket.write(httpReq);

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

  let tlsSeenByServer = null;
  try {
    const j = JSON.parse(body);
    if (j.tls?.ja3_hash) {
      tlsSeenByServer = {
        ja3_hash: j.tls.ja3_hash,
        ja4: j.tls.ja4,
        http_version: j.http_version,
        ip: j.ip,
        ciphers_count: j.tls.ciphers?.length,
        extensions_count: j.tls.extensions?.length,
      };
    }
  } catch {}

  return {
    status,
    body,
    tls: {
      negotiated,
      seen_by_server: tlsSeenByServer,
      unsupported_features: unsupported,
    },
  };
}

// Export the config so Netlify knows which URL paths this function handles
export const config = {
  path: ['/scrape', '/.netlify/functions/scrape'],
};
