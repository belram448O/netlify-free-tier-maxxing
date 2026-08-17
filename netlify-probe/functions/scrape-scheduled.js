// Simple scheduled function — no Chrome, just confirms execution + probes runtime
export async function handler(event, context) {
  const start = Date.now();
  const runId = `run-${Date.now()}`;
  const result = {
    run_id: runId,
    handler_started: new Date().toISOString(),
    invoked_via: event.invocationSource || 'unknown',
  };

  // Runtime probe
  const mem = process.memoryUsage();
  result.runtime = {
    node_version: process.version,
    arch: process.arch,
    platform: process.platform,
    memory_rss_mb: +(mem.rss / 1024 / 1024).toFixed(1),
    memory_heap_total_mb: +(mem.heapTotal / 1024 / 1024).toFixed(1),
    memory_heap_used_mb: +(mem.heapUsed / 1024 / 1024).toFixed(1),
    memory_external_mb: +(mem.external / 1024 / 1024).toFixed(1),
    has_blobs_context: !!process.env.NETLIFY_BLOBS_CONTEXT,
    function_region: process.env.AWS_REGION || process.env.AWS_LAMBDA_REGION || 'unknown',
    function_name: process.env.AWS_LAMBDA_FUNCTION_NAME || 'unknown',
    function_memory_mb: process.env.AWS_LAMBDA_FUNCTION_MEMORY_SIZE || 'unknown',
    function_timeout_s: process.env.AWS_LAMBDA_FUNCTION_TIMEOUT || 'unknown',
    execution_env: process.env.AWS_EXECUTION_ENV || 'unknown',
    remaining_time_ms: context?.getRemainingTimeInMillis?.() || 'unknown',
    aws_request_id: context?.awsRequestId || 'unknown',
  };

  // CPU probe
  const cpuStart = Date.now();
  let primeCount = 0;
  for (let n = 2; n < 1_000_000; n++) {
    let isPrime = true;
    const sqrtN = Math.sqrt(n);
    for (let i = 2; i <= sqrtN; i++) {
      if (n % i === 0) { isPrime = false; break; }
    }
    if (isPrime) primeCount++;
  }
  result.cpu_probe = {
    primes_under_1M: primeCount,
    cpu_ms: Date.now() - cpuStart,
  };

  // Heavy CPU — 5M primes (more CPU time, confirm wall vs CPU)
  const cpu5mStart = Date.now();
  let primeCount5M = 0;
  for (let n = 2; n < 5_000_000; n++) {
    let isPrime = true;
    const sqrtN = Math.sqrt(n);
    for (let i = 2; i <= sqrtN; i++) {
      if (n % i === 0) { isPrime = false; break; }
    }
    if (isPrime) primeCount5M++;
  }
  result.cpu_probe_5M = {
    primes_under_5M: primeCount5M,
    cpu_ms: Date.now() - cpu5mStart,
  };

  // Write heartbeat blob
  try {
    const { getStore } = await import('@netlify/blobs');
    const store = getStore('function-runs');
    await store.setJSON(runId, result);
    await store.set('latest', JSON.stringify({ run_id: runId, ts: new Date().toISOString(), runtime: result.runtime, cpu_probe: result.cpu_probe, cpu_probe_5M: result.cpu_probe_5M }, null, 2));
    result.blob_write_ok = true;
  } catch (e) {
    result.blob_write_err = e.message;
  }

  result.total_handler_ms = Date.now() - start;
  return { statusCode: 200, body: JSON.stringify(result, null, 2) };
}
