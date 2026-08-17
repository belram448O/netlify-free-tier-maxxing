// Function v3 — tries to override TLS fingerprint using custom https.Agent
// Also tests node:tls directly for raw TLS control

import https from 'node:https';
import tls from 'node:tls';
import http from 'node:http';

// Chrome 120 cipher list (ordered as Chrome sends them)
const CHROME_CIPHERS = [
  // TLS 1.3 ciphers
  'TLS_AES_128_GCM_SHA256',
  'TLS_AES_256_GCM_SHA384',
  'TLS_CHACHA20_POLY1305_SHA256',
  // ECDHE (forward-secret)
  'ECDHE-ECDSA-AES128-GCM-SHA256',
  'ECDHE-RSA-AES128-GCM-SHA256',
  'ECDHE-ECDSA-AES256-GCM-SHA384',
  'ECDHE-RSA-AES256-GCM-SHA384',
  'ECDHE-ECDSA-CHACHA20-POLY1305',
  'ECDHE-RSA-CHACHA20-POLY1305',
  'ECDHE-RSA-AES128-SHA',
  'ECDHE-RSA-AES256-SHA',
  'ECDHE-ECDSA-AES128-SHA',
  'ECDHE-ECDSA-AES256-SHA',
  // DHE
  'DHE-RSA-AES128-GCM-SHA256',
  'DHE-RSA-AES256-GCM-SHA384',
  // RSA fallback
  'AES128-GCM-SHA256',
  'AES256-GCM-SHA384',
  'AES128-SHA',
  'AES256-SHA',
].join(':');

const CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

export async function handler(event, context) {
  const targetUrl = event.queryStringParameters?.url;
  const mode = event.queryStringParameters?.mode || 'default';  // default | chrome_ciphers | tls_direct

  if (!targetUrl) {
    return {
      statusCode: 400,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        error: 'missing url parameter',
        usage: '?url=...&mode=default|chrome_ciphers|tls_direct',
      }, null, 2),
    };
  }

  const start = Date.now();
  let result = { mode, target_url: targetUrl };

  try {
    if (mode === 'chrome_ciphers') {
      // Use https.Agent with custom ciphers to mimic Chrome
      const agent = new https.Agent({
        ciphers: CHROME_CIPHERS,
        honorCipherOrder: false,
        minVersion: 'TLSv1.2',
        maxVersion: 'TLSv1.3',
        // ALPN to prefer h2
        ALPNProtocols: ['h2', 'http/1.1'],
        // Don't reject unauthorized
        rejectUnauthorized: true,
      });

      const r = await fetch(targetUrl, {
        method: 'GET',
        headers: {
          'User-Agent': CHROME_UA,
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9',
          'Accept-Encoding': 'gzip, deflate, br',
          'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
          'Sec-Ch-Ua-Mobile': '?0',
          'Sec-Ch-Ua-Platform': '"Windows"',
          'Sec-Fetch-Dest': 'document',
          'Sec-Fetch-Mode': 'navigate',
          'Sec-Fetch-Site': 'none',
          'Sec-Fetch-User': '?1',
          'Upgrade-Insecure-Requests': '1',
        },
        dispatcher: agent,  // undici's fetch supports dispatcher option
      });
      const text = await r.text();
      result.status = r.status;
      result.elapsed_ms = Date.now() - start;
      result.response_size = text.length;
      result.response_first_500 = text.substring(0, 500);
      result.response_headers = Object.fromEntries(r.headers.entries());
      // Note: undici's fetch doesn't actually use https.Agent — that's the old node-fetch
      result.note = 'undici fetch may ignore https.Agent — see tls_direct mode for raw TLS control';
    } else if (mode === 'tls_direct') {
      // Use node:tls directly for raw socket-level TLS
      const url = new URL(targetUrl);
      const port = url.port || (url.protocol === 'https:' ? 443 : 80);

      result.socket_info = {
        host: url.hostname,
        port: parseInt(port),
        protocol: url.protocol,
      };

      const tlsStart = Date.now();
      const tlsSocket = tls.connect({
        host: url.hostname,
        port: parseInt(port),
        servername: url.hostname,
        ciphers: CHROME_CIPHERS,
        honorCipherOrder: false,
        minVersion: 'TLSv1.2',
        maxVersion: 'TLSv1.3',
        ALPNProtocols: ['h2', 'http/1.1'],
      });

      // Wait for secureConnect
      await new Promise((resolve, reject) => {
        tlsSocket.once('secureConnect', resolve);
        tlsSocket.once('error', reject);
        setTimeout(() => reject(new Error('TLS connect timeout')), 10000);
      });
      result.tls_handshake_ms = Date.now() - tlsStart;
      result.tls_protocol = tlsSocket.getProtocol();
      result.tls_cipher = tlsSocket.getCipher()?.name;
      result.alpn_protocol = tlsSocket.alpnProtocol;
      result.peer_cert_subject = tlsSocket.getPeerCertificate()?.subject?.CN;

      // Send HTTP/1.1 request over the TLS socket
      const path = url.pathname + url.search || '/';
      const reqPath = (url.pathname || '/') + (url.search || '');
      const httpReq = `GET ${reqPath} HTTP/1.1\r\nHost: ${url.hostname}\r\nUser-Agent: ${CHROME_UA}\r\nAccept: */*\r\nConnection: close\r\n\r\n`;
      tlsSocket.write(httpReq);

      // Collect response
      const chunks = [];
      await new Promise((resolve) => {
        tlsSocket.on('data', (chunk) => chunks.push(chunk));
        tlsSocket.on('end', resolve);
        tlsSocket.on('close', resolve);
        setTimeout(resolve, 8000);
      });
      tlsSocket.destroy();

      const rawResponse = Buffer.concat(chunks).toString('utf8');
      result.raw_response_size = rawResponse.length;
      result.raw_response_full = rawResponse;
      result.elapsed_ms = Date.now() - start;
    } else {
      // Default: standard fetch
      const r = await fetch(targetUrl, {
        method: 'GET',
        headers: { 'User-Agent': CHROME_UA },
      });
      const text = await r.text();
      result.status = r.status;
      result.elapsed_ms = Date.now() - start;
      result.response_size = text.length;
      result.response_first_500 = text.substring(0, 500);
      result.response_headers = Object.fromEntries(r.headers.entries());
    }
  } catch (e) {
    result.error = e.message;
    result.stack = e.stack?.split('\n').slice(0, 5).join('\n');
    result.elapsed_ms = Date.now() - start;
  }

  return {
    statusCode: 200,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(result, null, 2),
  };
}
