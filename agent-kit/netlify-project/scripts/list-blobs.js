// Helper script: list all blobs in a Netlify site's stores
// Usage: NETLIFY_AUTH_TOKEN=nfp_xxx NETLIFY_SITE_ID=xxx node scripts/list-blobs.js [store-name]
//
// Cost: 0 credits (blob API is free)

const TOKEN = process.env.NETLIFY_AUTH_TOKEN;
const SITE_ID = process.env.NETLIFY_SITE_ID;
const STORE_NAME = process.argv[2];

if (!TOKEN || !SITE_ID) {
  console.error('Usage: NETLIFY_AUTH_TOKEN=nfp_xxx NETLIFY_SITE_ID=xxx node scripts/list-blobs.js [store-name]');
  console.error('  If store-name is omitted, lists all stores.');
  process.exit(1);
}

const apiBase = 'https://api.netlify.com/api/v1';

async function main() {
  if (STORE_NAME) {
    const store = STORE_NAME.startsWith('site:') ? STORE_NAME : `site:${STORE_NAME}`;
    const r = await fetch(`${apiBase}/blobs/${SITE_ID}/${store}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    if (!r.ok) { console.error(`Failed: ${r.status} ${await r.text()}`); process.exit(1); }
    const data = await r.json();
    console.log(`Store: ${store}`);
    console.log(`Blobs: ${data.blobs?.length || 0}`);
    let total = 0;
    for (const b of (data.blobs || [])) {
      const size = b.size || 0;
      total += size;
      console.log(`  ${b.key}  ${size} bytes  modified=${b.last_modified}`);
    }
    console.log(`---`);
    console.log(`Total: ${total} bytes (${(total/1024/1024).toFixed(2)} MB)`);
  } else {
    const r = await fetch(`${apiBase}/blobs/${SITE_ID}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    if (!r.ok) { console.error(`Failed: ${r.status} ${await r.text()}`); process.exit(1); }
    const data = await r.json();
    console.log(`Stores in site ${SITE_ID}:`);
    for (const store of (data.stores || [])) {
      console.log(`  ${store}`);
    }
  }
}

main().catch(e => { console.error(e); process.exit(1); });
