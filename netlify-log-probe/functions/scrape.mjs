// Modern v2 function — uses Request/Response API
// This format properly logs console.log output via Netlify's observability stack

import tls from 'node:tls';
import { impersonate, isSupported } from 'tls-impersonate';

export default async function handler(req, context) {
  const url = new URL(req.url);
  const params = url.searchParams;
  const test = params.get('test') || 'fingerprint';
  const method = params.get('method') || 'fetch';
  const targetUrl = params.get('url') || 'https://tls.peet.ws/api/all';
  const returnBlob = params.get('return_blob') === '1';

  console.log(`=== FUNCTION INVOCATION ${new Date().toISOString()} ===`);
  console.log(`test=${test} method=${method} url=${targetUrl} return_blob=${returnBlob}`);
  console.log(`request_id=${context?.awsRequestId || 'unknown'}`);

  if (test === 'fingerprint') {
    if (method === 'fetch') {
      console.log('Using default fetch()');
      const r = await fetch(targetUrl, {
        headers: { 'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36' },
      });
      const text = await r.text();
      console.log(`Response: status=${r.status} size=${text.length} bytes`);
      let parsed;
      try {
        parsed = JSON.parse(text);
        console.log(`TLS seen by server:`);
        console.log(`  JA3 hash: ${parsed.tls?.ja3_hash}`);
        console.log(`  JA4: ${parsed.tls?.ja4}`);
        console.log(`  HTTP: ${parsed.http_version} | ciphers: ${parsed.tls?.ciphers?.length} | exts: ${parsed.tls?.extensions?.length}`);
        console.log(`  IP: ${parsed.ip}`);
      } catch (e) {
        console.log(`Could not parse response as JSON: ${e.message}`);
        console.log(`First 200 chars: ${text.substring(0, 200)}`);
      }

      // If return_blob=1, also write the scraped data to a blob and return its key
      let blobInfo = null;
      if (returnBlob) {
        try {
          const { getStore } = await import('@netlify/blobs');
          const store = getStore('function-scrapes');
          const blobKey = `scrape-${Date.now()}`;
          await store.setJSON(blobKey, {
            target_url: targetUrl,
            timestamp: new Date().toISOString(),
            status: r.status,
            response_size: text.length,
            response_body: text,
            tls_fingerprint: parsed?.tls,
          });
          // Update latest pointer
          await store.setJSON('latest', {
            blob_key: blobKey,
            timestamp: new Date().toISOString(),
            target_url: targetUrl,
          });
          blobInfo = { blob_key: blobKey, store: 'function-scrapes', size_bytes: text.length };
          console.log(`Wrote blob: ${blobKey} (${text.length} bytes)`);
        } catch (e) {
          console.log(`Blob write error: ${e.message}`);
          blobInfo = { error: e.message };
        }
      }

      const result = {
        ok: r.ok,
        status: r.status,
        target_url: targetUrl,
        tls: parsed?.tls ? {
          ja3_hash: parsed.tls.ja3_hash,
          ja4: parsed.tls.ja4,
          http_version: parsed.http_version,
          ip: parsed.ip,
          ciphers_count: parsed.tls.ciphers?.length,
          extensions_count: parsed.tls.extensions?.length,
        } : null,
        blob: blobInfo,
      };
      console.log(`=== END INVOCATION ===`);
      return Response.json(result, { headers: { 'x-blob-key': blobInfo?.blob_key || '' } });

    } else if (method === 'chrome_impersonate') {
      console.log('Using tls-impersonate with Chrome 120 spec');
      if (!isSupported()) {
        console.error('tls-impersonate not supported on this runtime');
        return Response.json({ error: 'tls-impersonate not supported' }, { status: 500 });
      }

      const CHROME_SPEC = {
        cipherSuites: [
          0x1301, 0x1302, 0x1303,
          0xc02b, 0xc02f, 0xc02c, 0xc030,
          0xcca9, 0xcca8,
          0xc013, 0xc014,
          0x009c, 0x009d, 0x002f, 0x0035,
        ],
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

      const { tlsOptions, unsupported } = impersonate(CHROME_SPEC);
      console.log(`tls-impersonate unsupported features: ${unsupported?.length || 0}`);

      const target = new URL(targetUrl);
      const port = target.port || 443;

      const tlsSocket = tls.connect({
        host: target.hostname,
        port: parseInt(port),
        servername: target.hostname,
        ...tlsOptions,
      });

      console.log(`Connecting to ${target.hostname}:${port}...`);
      await new Promise((resolve, reject) => {
        tlsSocket.once('secureConnect', resolve);
        tlsSocket.once('error', reject);
        setTimeout(() => reject(new Error('TLS timeout')), 10000);
      });

      console.log(`TLS: ${tlsSocket.getProtocol()} | ${tlsSocket.getCipher()?.name} | ALPN: ${tlsSocket.alpnProtocol}`);

      const path = (target.pathname || '/') + (target.search || '');
      tlsSocket.write(
        `GET ${path} HTTP/1.1\r\n` +
        `Host: ${target.hostname}\r\n` +
        `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n` +
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
      const bodyStart = raw.indexOf('\r\n\r\n');
      const body = bodyStart >= 0 ? raw.substring(bodyStart + 4) : raw;
      const status = parseInt(raw.match(/^HTTP\/[\d.]+ (\d+)/)?.[1] || '0');
      console.log(`Response: status=${status} size=${body.length} bytes`);

      let parsed;
      try {
        parsed = JSON.parse(body);
        console.log(`TLS seen by server:`);
        console.log(`  JA3 hash: ${parsed.tls?.ja3_hash}`);
        console.log(`  JA4: ${parsed.tls?.ja4}`);
        console.log(`  HTTP: ${parsed.http_version} | ciphers: ${parsed.tls?.ciphers?.length} | exts: ${parsed.tls?.extensions?.length}`);
        console.log(`  IP: ${parsed.ip}`);
      } catch (e) {
        console.log(`Could not parse as JSON: ${e.message}`);
      }

      // Write to blob if requested
      let blobInfo = null;
      if (returnBlob) {
        try {
          const { getStore } = await import('@netlify/blobs');
          const store = getStore('function-scrapes');
          const blobKey = `scrape-${Date.now()}`;
          await store.setJSON(blobKey, {
            target_url: targetUrl,
            method: 'chrome_impersonate',
            timestamp: new Date().toISOString(),
            status,
            response_size: body.length,
            response_body: body,
            tls_fingerprint: parsed?.tls,
            tls_options_used: {
              protocol: tlsSocket.getProtocol(),
              cipher: tlsSocket.getCipher()?.name,
              alpn: tlsSocket.alpnProtocol,
              unsupported_features: unsupported,
            },
          });
          await store.setJSON('latest', {
            blob_key: blobKey,
            timestamp: new Date().toISOString(),
            target_url: targetUrl,
            method: 'chrome_impersonate',
          });
          blobInfo = { blob_key: blobKey, store: 'function-scrapes', size_bytes: body.length };
          console.log(`Wrote blob: ${blobKey} (${body.length} bytes)`);
        } catch (e) {
          console.log(`Blob write error: ${e.message}`);
          blobInfo = { error: e.message };
        }
      }

      const result = {
        ok: status >= 200 && status < 300,
        status,
        target_url: targetUrl,
        method: 'chrome_impersonate',
        tls: {
          negotiated: {
            protocol: tlsSocket.getProtocol(),
            cipher: tlsSocket.getCipher()?.name,
            alpn: tlsSocket.alpnProtocol,
          },
          seen_by_server: parsed?.tls ? {
            ja3_hash: parsed.tls.ja3_hash,
            ja4: parsed.tls.ja4,
            http_version: parsed.http_version,
            ip: parsed.ip,
            ciphers_count: parsed.tls.ciphers?.length,
            extensions_count: parsed.tls.extensions?.length,
          } : null,
        },
        blob: blobInfo,
      };
      console.log(`=== END INVOCATION ===`);
      return Response.json(result, { headers: { 'x-blob-key': blobInfo?.blob_key || '' } });
    }
  }

  console.log(`Unknown test/method, returning help`);
  return Response.json({
    error: 'unknown test/method',
    usage: '?test=fingerprint&method=fetch|chrome_impersonate&url=...&return_blob=1',
  }, { status: 400 });
}

export const config = {
  path: ['/scrape', '/.netlify/functions/scrape'],
};
