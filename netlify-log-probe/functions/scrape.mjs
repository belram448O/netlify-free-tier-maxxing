// ESM (.mjs) — uses tls-impersonate for real Chrome JA3/JA4 impersonation
import tls from 'node:tls';
import { impersonate, isSupported } from 'tls-impersonate';

export async function handler(event, context) {
  const start = Date.now();
  const result = {
    handler_started: new Date().toISOString(),
    aws_request_id: context?.awsRequestId,
    function_memory_mb: process.env.AWS_LAMBDA_FUNCTION_MEMORY_SIZE,
    function_region: process.env.AWS_REGION,
    has_blobs_context: !!process.env.NETLIFY_BLOBS_CONTEXT,
    tls_impersonate_supported: isSupported(),
  };

  const test = event.queryStringParameters?.test || 'fingerprint';
  const method = event.queryStringParameters?.method || 'fetch';
  result.test = test;
  result.method = method;

  if (test === 'fingerprint') {
    const targetUrl = event.queryStringParameters?.url || 'https://tls.peet.ws/api/all';
    result.target_url = targetUrl;

    if (method === 'fetch') {
      // Default undici fetch — bot-fingerprintable
      try {
        const r = await fetch(targetUrl, {
          headers: { 'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36' },
        });
        const text = await r.text();
        result.status = r.status;
        result.ms = Date.now() - start;
        try {
          const j = JSON.parse(text);
          result.tls_seen_by_server = {
            ja3_hash: j.tls?.ja3_hash,
            ja4: j.tls?.ja4,
            http_version: j.http_version,
            user_agent: j.user_agent,
            ip: j.ip,
            ciphers_count: j.tls?.ciphers?.length,
            extensions_count: j.tls?.extensions?.length,
          };
        } catch { result.response_first_500 = text.substring(0, 500); }
      } catch (e) { result.fetch_err = e.message; }

    } else if (method === 'chrome_impersonate') {
      // Use tls-impersonate with a real Chrome 120 ClientHello spec
      try {
        // Real Chrome 120 ClientHello (cipher suites + extensions in wire order)
        // Source: https://github.com/bogdanfinn/tls-client (Chrome 120 profile)
        const CHROME_SPEC = {
          cipherSuites: [
            // GREASE would go first in real Chrome but we'll skip
            0x1301, 0x1302, 0x1303, // TLS 1.3: AES_128_GCM, AES_256_GCM, CHACHA20_POLY1305
            0xc02b, 0xc02f, 0xc02c, 0xc030, // ECDHE-ECDSA/RSA AES128/256-GCM-SHA256/384
            0xcca9, 0xcca8, // ECDHE-ECDSA/RSA CHACHA20-POLY1305
            0xc013, 0xc014, // ECDHE-RSA AES128/256-SHA (legacy)
            0x009c, 0x009d, // RSA AES128/256-GCM-SHA256/384
            0x002f, 0x0035, // RSA AES128/256-CBC-SHA (legacy)
          ],
          extensions: [
            { type: 0x0016 }, // encrypt_then_mac
            { type: 0x000b }, // ec_point_formats
            { type: 0xff01 }, // renegotiation_info
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
          supportedGroups: [0x001d, 0x0017, 0x0018], // X25519, secp256r1, secp384r1
          signatureAlgorithms: [
            0x0403, 0x0804, 0x0401, 0x0503, 0x0501, 0x0803, 0x0601, 0x0201,
            0x0603, 0x0203, 0x0401, 0x0501, 0x0804, 0x0805, 0x0806, 0x0201,
          ],
          alpnProtocols: ['h2', 'http/1.1'],
        };

        const { tlsOptions, unsupported } = impersonate(CHROME_SPEC);
        result.unsupported_count = unsupported?.length || 0;
        result.unsupported_features = (unsupported || []).slice(0, 5);

        const url = new URL(targetUrl);
        const port = url.port || 443;

        const tlsSocket = tls.connect({
          host: url.hostname,
          port: parseInt(port),
          servername: url.hostname,
          ...tlsOptions,
        });

        await new Promise((resolve, reject) => {
          tlsSocket.once('secureConnect', resolve);
          tlsSocket.once('error', reject);
          setTimeout(() => reject(new Error('TLS timeout')), 10000);
        });

        result.tls_info = {
          protocol: tlsSocket.getProtocol(),
          cipher: tlsSocket.getCipher()?.name,
          alpn: tlsSocket.alpnProtocol,
        };

        const path = (url.pathname || '/') + (url.search || '');
        // Use HTTP/2 if ALPN negotiated h2, else HTTP/1.1
        if (tlsSocket.alpnProtocol === 'h2') {
          // For simplicity, fall back to HTTP/1.1 even if h2 was negotiated
          // (HTTP/2 frame handling is complex)
          result.note = 'h2 negotiated but used HTTP/1.1 (HTTP/2 not implemented)';
        }
        tlsSocket.write(`GET ${path} HTTP/1.1\r\nHost: ${url.hostname}\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\nAccept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r\nAccept-Language: en-US,en;q=0.9\r\nAccept-Encoding: gzip, deflate, br\r\nConnection: close\r\n\r\n`);

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

        result.status = parseInt(raw.match(/^HTTP\/[\d.]+ (\d+)/)?.[1] || '0');
        result.ms = Date.now() - start;
        result.raw_size = raw.length;
        try {
          const j = JSON.parse(body);
          result.tls_seen_by_server = {
            ja3_hash: j.tls?.ja3_hash,
            ja4: j.tls?.ja4,
            http_version: j.http_version,
            user_agent: j.user_agent,
            ip: j.ip,
            ciphers_count: j.tls?.ciphers?.length,
            extensions_count: j.tls?.extensions?.length,
            ja3_first_100: j.tls?.ja3?.substring(0, 100),
            // List actual extensions the server saw
            extensions_seen: j.tls?.extensions?.slice(0, 20).map(e => e.name || e.type),
          };
        } catch { result.response_first_500 = body.substring(0, 500); }
      } catch (e) {
        result.chrome_impersonate_err = e.message;
        result.chrome_impersonate_stack = e.stack?.split('\n').slice(0, 8).join('\n');
      }
    }
  } else if (test === 'ingress_test') {
    // Download N MB from internet, return tiny "ok" response
    // Tests whether function-initiated downloads cost bandwidth credits
    const size = parseInt(event.queryStringParameters?.size || '5'); // MB
    const bytes = size * 1024 * 1024;
    const downloadStart = Date.now();
    try {
      const r = await fetch(`https://httpbin.org/bytes/${bytes}`, {
        headers: { 'User-Agent': 'Mozilla/5.0' },
      });
      const buf = Buffer.from(await r.arrayBuffer());
      result.download = {
        url: `https://httpbin.org/bytes/${bytes}`,
        size_mb_requested: size,
        bytes_received: buf.length,
        mb_received: +(buf.length / 1024 / 1024).toFixed(2),
        status: r.status,
        ms: Date.now() - downloadStart,
        first_32_bytes_hex: buf.subarray(0, 32).toString('hex'),
      };
      // Return only tiny confirmation — to isolate ingress from response bandwidth
      result.tiny_response = 'ok';
    } catch (e) { result.download_err = e.message; }

  } else if (test === 'egress_test') {
    // Return N MB as the HTTP response — should definitely count as bandwidth
    const size = parseInt(event.queryStringParameters?.size || '5'); // MB
    const bytes = size * 1024 * 1024;
    const payload = 'Y'.repeat(bytes);
    result.payload_size = bytes;
    result.payload_mb = size;
    return {
      statusCode: 200,
      headers: { 'content-type': 'text/plain', 'x-test': 'egress_test', 'x-size-mb': String(size) },
      body: payload,
    };
  }

  result.total_ms = Date.now() - start;
  return {
    statusCode: 200,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(result, null, 2),
  };
}
