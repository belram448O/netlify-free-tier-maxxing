// Helper script: trigger a preview deploy via the Netlify API
// Usage: NETLIFY_AUTH_TOKEN=nfp_xxx NETLIFY_SITE_ID=xxx node scripts/trigger-deploy.js ["deploy message"]
//
// This triggers a preview deploy (0 credits). The deploy will run the build command
// defined in netlify.toml, then run any plugins. Function URLs from previous preview
// deploys remain accessible until they expire (which they don't, per our tests).

const TOKEN = process.env.NETLIFY_AUTH_TOKEN;
const SITE_ID = process.env.NETLIFY_SITE_ID;
const MESSAGE = process.argv[2] || `api-triggered preview deploy at ${new Date().toISOString()}`;

if (!TOKEN || !SITE_ID) {
  console.error('Usage: NETLIFY_AUTH_TOKEN=nfp_xxx NETLIFY_SITE_ID=xxx node scripts/trigger-deploy.js ["deploy message"]');
  process.exit(1);
}

const apiBase = 'https://api.netlify.com/api/v1';

async function main() {
  // Get the latest deploy's file manifest so we can re-deploy with draft:true
  // (without Git, we need to provide files or accept an empty deploy)
  console.log(`Triggering preview deploy on site ${SITE_ID}...`);
  console.log(`Message: ${MESSAGE}`);

  // Step 1: Create a draft deploy (preview, 0 credits)
  const r = await fetch(`${apiBase}/sites/${SITE_ID}/deploys`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      title: MESSAGE,
      draft: true,  // CRITICAL: draft:true = preview deploy = 0 credits
      files: {},     // Empty files = no static assets to upload, build still runs
    }),
  });

  if (!r.ok) {
    console.error(`Failed to create deploy: ${r.status} ${await r.text()}`);
    process.exit(1);
  }

  const deploy = await r.json();
  console.log(`\nDeploy created:`);
  console.log(`  ID: ${deploy.id}`);
  console.log(`  State: ${deploy.state}`);
  console.log(`  Context: ${deploy.context} (should be 'deploy-preview')`);
  console.log(`  URL: ${deploy.deploy_ssl_url}`);

  // Step 2: Poll for ready state
  console.log(`\nWaiting for deploy to go live...`);
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 5000));
    const r2 = await fetch(`${apiBase}/sites/${SITE_ID}/deploys/${deploy.id}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    const d = await r2.json();
    if (d.state === 'ready') {
      console.log(`\n✓ Deploy ready!`);
      console.log(`  Function URL: https://${deploy.id}--<your-site>.netlify.app/.netlify/functions/scrape`);
      console.log(`  Deploy time: ${d.deploy_time}s`);
      return;
    }
    if (d.state === 'error') {
      console.error(`\n✗ Deploy failed: ${d.error_message}`);
      process.exit(1);
    }
    process.stdout.write('.');
  }
  console.error('\nTimeout waiting for deploy');
  process.exit(1);
}

main().catch(e => { console.error(e); process.exit(1); });
