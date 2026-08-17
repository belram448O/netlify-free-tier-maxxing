// Netlify Function: log-as-output demo (v1 ESM handler)
//
// This function demonstrates using function logs as a free egress channel.
// All console.log output is captured by Netlify's observability stack and
// retrievable via `netlify logs --json --since 24h` (24h retention on Free plan).
//
// Usage:
//   1. Invoke the function:
//      curl 'https://<deploy-id>--<site>.netlify.app/.netlify/functions/log-exfil?data=hello'
//   2. Retrieve the log output:
//      netlify logs --json --since 1h --function log-exfil
//
// Log limitations (Free plan):
//   - 24-hour retention
//   - 4 KB per invocation (Lambda compatibility mode)
//   - 700 KB per single log entry via Log Drains (Enterprise only)

export async function handler(event, context) {
  const params = event.queryStringParameters || {};
  const data = params.data || 'default-payload';
  const ts = new Date().toISOString();
  const requestId = context?.awsRequestId || 'unknown';

  console.log('==========LOG_EXFIL_START==========');
  console.log(`request_id=${requestId}`);
  console.log(`timestamp=${ts}`);
  console.log(`data=${data}`);

  // You can log structured JSON — readable via `netlify logs --json`
  console.log(JSON.stringify({
    request_id: requestId,
    timestamp: ts,
    payload: data,
    source: 'log-exfil-function',
  }));

  // Multiple lines for multi-record data
  for (let i = 0; i < 5; i++) {
    console.log(`record_${i}=${JSON.stringify({ idx: i, value: Math.random(), ts })}`);
  }

  console.log('==========LOG_EXFIL_END==========');

  // Tiny response — just confirms the function ran
  return {
    statusCode: 200,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      ok: true,
      message: 'Data written to function logs. Retrieve with: netlify logs --json --since 1h --function log-exfil',
      request_id: requestId,
      timestamp: ts,
    }),
  };
}
