// Function that writes results OUTBOUND to httpbin (we can see the request was received)
// This proves the function executed even if Blobs fails
export async function handler(event, context) {
  const ts = new Date().toISOString();
  const runId = `af-${Date.now()}`;

  const result = {
    run_id: runId,
    executed_at: ts,
    node: process.version,
    region: process.env.AWS_REGION || 'unknown',
    fn_name: process.env.AWS_LAMBDA_FUNCTION_NAME || 'unknown',
    fn_mem_mb: process.env.AWS_LAMBDA_FUNCTION_MEMORY_SIZE || 'unknown',
    fn_timeout_s: process.env.AWS_LAMBDA_FUNCTION_TIMEOUT || 'unknown',
    exec_env: process.env.AWS_EXECUTION_ENV || 'unknown',
    aws_request_id: context?.awsRequestId || 'unknown',
    event_keys: Object.keys(event || {}),
    event_source: event?.invocationSource || event?.source || 'unknown',
  };

  // Try IP probe — see Lambda egress IP (proves IP pool question)
  try {
    const r = await fetch('https://api.ipify.org?format=json');
    const j = await r.json();
    result.lambda_egress_ip = j.ip;
    const r2 = await fetch('https://ipinfo.io/json');
    const j2 = await r2.json();
    result.lambda_ip_info = { ip: j2.ip, city: j2.city, region: j2.region, country: j2.country, org: j2.org };
  } catch (e) { result.ip_err = e.message; }

  // Try Blobs write
  try {
    const { getStore } = await import('@netlify/blobs');
    const store = getStore('function-runs');
    await store.setJSON(runId, result);
    await store.set('latest', JSON.stringify(result, null, 2));
    result.blobs_write_ok = true;
  } catch (e) {
    result.blobs_write_err = e.message;
    result.blobs_write_stack = e.stack?.split('\n').slice(0, 5).join('\n');
  }

  // OUTBOUND POST to a webhook — visible at httpbin.org/<id>
  try {
    const r = await fetch('https://httpbin.org/post', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Function-Run-Id': runId },
      body: JSON.stringify(result),
    });
    result.webhook_post_status = r.status;
  } catch (e) { result.webhook_err = e.message; }

  // Also write to a /anything endpoint where we can read it back
  try {
    const r = await fetch('https://httpbin.org/anything/netlify-fn-test-' + runId, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result),
    });
    result.webhook_anything_status = r.status;
  } catch (e) { result.webhook_anything_err = e.message; }

  return { statusCode: 200, body: JSON.stringify(result, null, 2) };
}
