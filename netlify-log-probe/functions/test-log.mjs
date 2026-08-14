// Test whether v1-style handler with ESM (.mjs) captures console.log
export async function handler(event, context) {
  console.log('HELLO_FROM_V1_ESM_FUNCTION');
  console.log(`request_id=${context?.awsRequestId}`);
  console.log(`timestamp=${new Date().toISOString()}`);
  console.log('==========DATA_START==========');
  console.log(JSON.stringify({test: 'data', value: 42, ts: new Date().toISOString()}));
  console.log('==========DATA_END==========');
  console.error('THIS_IS_AN_ERROR_LOG');
  return {
    statusCode: 200,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ok: true, format: 'v1-esm'}),
  };
}
