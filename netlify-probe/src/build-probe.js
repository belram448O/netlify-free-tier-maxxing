// Probe the Netlify BUILD environment.
// Outputs findings to stdout (visible in deploy logs) AND writes a blob.

import os from 'node:os';
import { execSync } from 'node:child_process';

const out = {};

// 1. Who am I
out.user = execSync('whoami').toString().trim();
out.cwd = process.cwd();
out.env_keys_count = Object.keys(process.env).length;
out.env_keys = Object.keys(process.env).filter(k => !k.toLowerCase().includes('token') && !k.toLowerCase().includes('secret') && !k.toLowerCase().includes('key')).sort();

// 2. Memory
const mem = process.memoryUsage();
out.memory_rss_mb = (mem.rss / 1024 / 1024).toFixed(1);
out.memory_heap_total_mb = (mem.heapTotal / 1024 / 1024).toFixed(1);
out.memory_heap_used_mb = (mem.heapUsed / 1024 / 1024).toFixed(1);
out.memory_external_mb = (mem.external / 1024 / 1024).toFixed(1);

// 3. CPU
const cpus = os.cpus();
out.cpu_count = cpus.length;
out.cpu_model = cpus[0]?.model;
out.cpu_speed_mhz = cpus[0]?.speed;
out.arch = process.arch;
out.platform = process.platform;

// 4. Free disk
try {
  out.df = execSync('df -h /').toString().trim().split('\n');
  out.free_disk_gb = execSync("df -BG / | awk 'NR==2 {print $4}'").toString().trim();
} catch (e) { out.df_err = e.message; }

// 5. Build env vars
out.is_netlify_build = !!process.env.NETLIFY;
out.has_blobs_context = !!process.env.NETLIFY_BLOBS_CONTEXT;
out.deploy_context = process.env.CONTEXT;
out.deploy_id = process.env.DEPLOY_ID;
out.site_id = process.env.SITE_ID;
out.build_id = process.env.BUILD_ID;

// 6. Outbound HTTP test (try to fetch something external)
const start = Date.now();
try {
  const r = await fetch('https://httpbin.org/get');
  out.outbound_http_ok = r.ok;
  out.outbound_http_ms = Date.now() - start;
} catch (e) { out.outbound_http_err = e.message; out.outbound_http_ms = Date.now() - start; }

// 7. Try to install a heavy package — Chrome binary
const chrome_start = Date.now();
try {
  // Try to detect if we can install & run chromium-headless-shell
  out.chrome_test = execSync('which chromium chromium-browser google-chrome chrome 2>&1 || echo NONE').toString().trim();
  out.chrome_install_attempt_ms = Date.now() - chrome_start;
} catch (e) { out.chrome_install_err = e.message; out.chrome_install_attempt_ms = Date.now() - chrome_start; }

// 8. Try to apt-get (probably not allowed)
try {
  out.apt_test = execSync('which apt-get apt dpkg 2>&1 || echo NONE').toString().trim();
} catch (e) { out.apt_test_err = e.message; }

// 9. Block print everything
console.log('=== BUILD PROBE RESULTS ===');
console.log(JSON.stringify(out, null, 2));
console.log('=== END BUILD PROBE ===');
