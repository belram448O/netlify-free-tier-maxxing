// Build-time probe: tests 4 things
// 1. How much can we write to build logs? (write 100KB, 500KB, 1MB chunks)
// 2. Can we POST data out to an external webhook? (data exfil via network)
// 3. Can we write to Blobs from build? (yes, we know — re-confirm)
// 4. Can we run headless Chrome in the build container? (probe only — no puppeteer install yet)

import { execSync } from 'node:child_process';

const out = { phase: 'build', ts: new Date().toISOString() };

// --- 1. Build log volume test ---
// Write increasing chunks to stdout (which becomes the build log)
out.log_volume_test = {};
const sizes = [
  { name: '10KB', bytes: 10 * 1024 },
  { name: '100KB', bytes: 100 * 1024 },
  { name: '500KB', bytes: 500 * 1024 },
  { name: '1MB', bytes: 1 * 1024 * 1024 },
];
for (const s of sizes) {
  const chunk = 'A'.repeat(s.bytes);
  const start = Date.now();
  process.stdout.write(`--- LOG_CHUNK_START_${s.name} ---\n`);
  process.stdout.write(chunk);
  process.stdout.write(`\n--- LOG_CHUNK_END_${s.name} (written ${chunk.length} bytes in ${Date.now() - start}ms) ---\n`);
  out.log_volume_test[s.name] = { bytes: s.bytes, ms: Date.now() - start };
}

// --- 2. Outbound HTTP POST to webhook (data exfil) ---
// Using httpbin.org which echoes back the request — proves we can POST data out
out.outbound_post = {};
try {
  const payload = JSON.stringify({ source: 'netlify-build', ts: new Date().toISOString(), data: 'exfil-test-'.repeat(100) });
  const start = Date.now();
  const r = await fetch('https://httpbin.org/post', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload,
  });
  const respJson = await r.json();
  out.outbound_post = {
    ok: r.ok,
    status: r.status,
    ms: Date.now() - start,
    echoed_back_bytes: JSON.stringify(respJson).length,
    echoed_back_data_starts_with: respJson?.data?.substring(0, 50),
  };
} catch (e) { out.outbound_post.err = e.message; }

// --- 3. Blobs write from build ---
out.blobs_write = {};
try {
  const { getStore } = await import('@netlify/blobs');
  const store = getStore('build-dumps');
  // Write a small blob
  await store.setJSON(`build-${Date.now()}`, { ts: new Date().toISOString(), msg: 'from-build', payload: 'test'.repeat(1000) });
  // Try a larger blob (1MB)
  const bigPayload = 'X'.repeat(1024 * 1024); // 1MB
  await store.set('big-test', bigPayload);
  out.blobs_write = { ok: true, wrote_1mb: true };
} catch (e) { out.blobs_write.err = e.message; }

// --- 4. Headless Chrome probe in build ---
out.chrome_probe = {};
try {
  // Check if Chrome/Chromium is pre-installed
  out.chrome_probe.which = execSync('which chromium chromium-browser google-chrome chrome chrome-headless-shell 2>&1 || echo NONE').toString().trim();
  // Try installing @sparticuz/chromium (the Lambda-compatible binary) into the build
  const installStart = Date.now();
  try {
    execSync('npm install @sparticuz/chromium --no-save 2>&1 | tail -5', { cwd: process.cwd(), stdio: 'pipe', timeout: 60000 });
    out.chrome_probe.install_ms = Date.now() - installStart;
    out.chrome_probe.installed = true;
    // Try to launch it
    try {
      const { default: chromium } = await import('@sparticuz/chromium');
      const executablePath = await chromium.executablePath();
      out.chrome_probe.executable_path = executablePath;
      // Don't actually launch puppeteer (we didn't install it) — just confirm binary exists
      out.chrome_probe.binary_size_bytes = (await import('node:fs')).statSync(executablePath).size;
      out.chrome_probe.binary_size_mb = +(out.chrome_probe.binary_size_bytes / 1024 / 1024).toFixed(1);
    } catch (e) { out.chrome_probe.launch_err = e.message; }
  } catch (e) { out.chrome_probe.install_err = e.message; out.chrome_probe.install_ms = Date.now() - installStart; }
} catch (e) { out.chrome_probe.err = e.message; }

// --- 5. Try to fetch a JS-rendered page using curl + node http ---
out.fetch_test = {};
try {
  const start = Date.now();
  const r = await fetch('https://example.com');
  const html = await r.text();
  out.fetch_test = { ok: r.ok, status: r.status, ms: Date.now() - start, html_size: html.length };
} catch (e) { out.fetch_test.err = e.message; }

// Print final structured result
console.log('=== BUILD_PROBE_RESULT_START ===');
console.log(JSON.stringify(out, null, 2));
console.log('=== BUILD_PROBE_RESULT_END ===');
