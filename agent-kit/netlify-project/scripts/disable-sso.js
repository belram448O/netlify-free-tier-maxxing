// Helper script: disable SSO on a Netlify site so function URLs are publicly accessible
// Usage: node scripts/disable-sso.js <site-id>
//
// This uses the undocumented bb-api (Backend-For-Frontend) that the dashboard uses.
// It requires session cookies (NOT the PAT) — extract from browser DevTools after
// logging into app.netlify.com.

import { readFileSync } from 'node:fs';

const SITE_ID = process.argv[2];
const COOKIE_FILE = process.env.NETLIFY_COOKIE_FILE || '/tmp/netlify-cookies.txt';

if (!SITE_ID) {
  console.error('Usage: node scripts/disable-sso.js <site-id>');
  console.error('  Requires session cookies in $NETLIFY_COOKIE_FILE (default: /tmp/netlify-cookies.txt)');
  console.error('  Extract cookies from browser DevTools → Application → Cookies → app.netlify.com');
  process.exit(1);
}

let cookie;
try {
  cookie = readFileSync(COOKIE_FILE, 'utf8').trim();
} catch (e) {
  console.error(`Cannot read cookie file: ${COOKIE_FILE}`);
  console.error('Extract cookies from browser DevTools, then save as a single Cookie: header value.');
  console.error('Required cookie: _nf-auth only (WAF tested — connect.sid NOT needed)');
  process.exit(1);
}

const url = `https://app.netlify.com/access-control/bb-api/api/v1/sites/${SITE_ID}`;

console.log(`Disabling SSO on site ${SITE_ID}...`);

const r = await fetch(url, {
  method: 'PUT',
  headers: {
    'Content-Type': 'application/json',
    'Accept': '*/*',
    'Origin': 'https://app.netlify.com',
    'Referer': `https://app.netlify.com/projects/${SITE_ID}/`,
    'Cookie': cookie,
  },
  body: JSON.stringify({
    password: '',
    password_context: 'all',
    sso_login: false,
    sso_login_context: 'all',
  }),
});

if (!r.ok) {
  console.error(`Failed: ${r.status}`);
  console.error(await r.text());
  process.exit(1);
}

const data = await r.json();
console.log(`\n✓ SSO disabled:`);
console.log(`  sso_login: ${data.sso_login}`);
console.log(`  sso_login_context: ${data.sso_login_context}`);
console.log(`  account_sso_login: ${data.account_sso_login}`);
console.log(`  has_password: ${data.has_password}`);
console.log(`\nFunction URLs are now publicly accessible without login.`);
