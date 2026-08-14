// Build-time probe — focused on the 4 questions
// NO plugin, NO function — just the build command itself
import { execSync } from 'node:child_process';
import { writeFileSync, statSync } from 'node:fs';

const out = { phase: 'build-command', ts: new Date().toISOString() };

// --- 1. Log volume test — escalating sizes ---
out.log_volume_test = {};
const sizes = [
  { name: '10KB',   bytes: 10 * 1024 },
  { name: '100KB',  bytes: 100 * 1024 },
  { name: '500KB',  bytes: 500 * 1024 },
  { name: '1MB',    bytes: 1 * 1024 * 1024 },
  { name: '5MB',    bytes: 5 * 1024 * 1024 },
  { name: '10MB',   bytes: 10 * 1024 * 1024 },
];

for (const s of sizes) {
  const chunk = 'A'.repeat(s.bytes);
  const start = Date.now();
  process.stdout.write(`\n--- LOG_CHUNK_START_${s.name} ---\n`);
  process.stdout.write(chunk);
  process.stdout.write(`\n--- LOG_CHUNK_END_${s.name} (written ${chunk.length} bytes in ${Date.now() - start}ms) ---\n`);
  out.log_volume_test[s.name] = { bytes: s.bytes, ms: Date.now() - start };
}

// --- 2. Try to write to a file and see if it ends up in build artifacts / accessible ---
// Files written to the publish dir get served as static assets — that's a known exfil path
// But that data becomes part of the deploy (only works on prod deploys usually)
out.file_write_test = {};
try {
  writeFileSync('src/data-test.json', JSON.stringify({ ts: new Date().toISOString(), msg: 'exfil via static file' }, null, 2));
  out.file_write_test.size_bytes = statSync('src/data-test.json').size;
  out.file_write_test.ok = true;
} catch (e) { out.file_write_test.err = e.message; }

// --- 3. Outbound HTTP POST to webhook (the main exfil channel) ---
out.outbound_post = {};
try {
  const payload = JSON.stringify({
    source: 'netlify-build',
    ts: new Date().toISOString(),
    data: 'exfil-test-' + 'X'.repeat(1000),  // ~1KB payload
  });
  const start = Date.now();
  const r = await fetch('https://httpbin.org/post', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Netlify-Build': 'probe' },
    body: payload,
  });
  const respJson = await r.json();
  out.outbound_post = {
    ok: r.ok,
    status: r.status,
    ms: Date.now() - start,
    sent_bytes: payload.length,
    echoed_back_data_first_50: respJson?.data?.substring(0, 50),
    echoed_headers: respJson?.headers,
  };
} catch (e) { out.outbound_post.err = e.message; }

// --- 4. Try multiple webhook endpoints (simulating GitHub API, Supabase, etc.) ---
out.endpoints_test = {};
const endpoints = [
  'https://httpbin.org/get',
  'https://api.github.com/zen',
  'https://postman-echo.com/get',
  'https://api.ipify.org?format=json',  // shows build egress IP
];
for (const url of endpoints) {
  try {
    const start = Date.now();
    const r = await fetch(url);
    const text = await r.text();
    out.endpoints_test[url] = {
      ok: r.ok,
      status: r.status,
      ms: Date.now() - start,
      response_first_100: text.substring(0, 100),
    };
  } catch (e) { out.endpoints_test[url] = { err: e.message }; }
}

// --- 5. Environment probe — what does the build env give us? ---
out.env = {
  node_version: process.version,
  arch: process.arch,
  platform: process.platform,
  has_blobs_context: !!process.env.NETLIFY_BLOBS_CONTEXT,
  context: process.env.CONTEXT,
  deploy_id: process.env.DEPLOY_ID,
  site_id: process.env.SITE_ID,
  branch: process.env.BRANCH,
  pull_request: process.env.PULL_REQUEST,
  commit_ref: process.env.COMMIT_REF,
  site_name: process.env.SITE_NAME,
  // Check the runtime container
  kata_container: process.env.KATA_CONTAINER,
  fc_region: process.env.FC_REGION,
  fc_function_memory_size: process.env.FC_FUNCTION_MEMORY_SIZE,
  cpu_count: (await import('node:os')).cpus().length,
  cpu_model: (await import('node:os')).cpus()[0]?.model,
};

// Print final structured result — this is what shows up in build logs
console.log('=== BUILD_PROBE_RESULT_START ===');
console.log(JSON.stringify(out, null, 2));
console.log('=== BUILD_PROBE_RESULT_END ===');
