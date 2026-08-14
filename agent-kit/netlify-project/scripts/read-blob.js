// Helper script: read a blob from Netlify by store + key
// Usage: NETLIFY_AUTH_TOKEN=nfp_xxx NETLIFY_SITE_ID=xxx node scripts/read-blob.js <store-name> <blob-key>
//
// Cost: 0 credits (blob API is free, even for large blobs)

const TOKEN = process.env.NETLIFY_AUTH_TOKEN;
const SITE_ID = process.env.NETLIFY_SITE_ID;
const STORE_NAME = process.argv[2];
const BLOB_KEY = process.argv[3];

if (!TOKEN || !SITE_ID || !STORE_NAME || !BLOB_KEY) {
  console.error('Usage: NETLIFY_AUTH_TOKEN=nfp_xxx NETLIFY_SITE_ID=xxx node scripts/read-blob.js <store-name> <blob-key>');
  console.error('Example: node scripts/read-blob.js function-scrapes scrape-1786743913760');
  process.exit(1);
}

const store = STORE_NAME.startsWith('site:') ? STORE_NAME : `site:${STORE_NAME}`;
const url = `https://api.netlify.com/api/v1/blobs/${SITE_ID}/${store}/${BLOB_KEY}`;

const r = await fetch(url, {
  headers: { Authorization: `Bearer ${TOKEN}` },
});

if (!r.ok) {
  console.error(`Failed: ${r.status} ${await r.text()}`);
  process.exit(1);
}

const text = await r.text();
console.log(text);
