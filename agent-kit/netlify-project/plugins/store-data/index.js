// Netlify Build Plugin: stores /tmp scrape output to Netlify Blobs
//
// This runs AFTER the build command (which writes data to /tmp/scrape-*.jsonl).
// The build command phase does NOT have NETLIFY_BLOBS_CONTEXT, but the
// plugin's onPostBuild phase DOES — so we read /tmp files here and write to Blobs.
//
// Cost: 0 credits (preview deploys are free, blob storage is unmetered)

import { readFileSync, existsSync, statSync } from 'node:fs';

export default {
  onPostBuild: async () => {
    const RUN_TS = new Date().toISOString();
    const KEY_TS = Date.now();

    console.log(`\n========== STORE_PLUGIN started ${RUN_TS} ==========`);

    // Paths the build command writes to (see src/build-scraper.js)
    const RAW_FILE = '/tmp/scrape-raw.jsonl';
    const PROCESSED_FILE = '/tmp/scrape-processed.json';

    if (!existsSync(RAW_FILE)) {
      console.log('  No raw scrape data found — build command may not have produced output');
      return;
    }

    const rawContent = readFileSync(RAW_FILE, 'utf8');
    const rawSize = statSync(RAW_FILE).size;
    let processedContent = '{}';
    let processedSize = 0;
    if (existsSync(PROCESSED_FILE)) {
      processedContent = readFileSync(PROCESSED_FILE, 'utf8');
      processedSize = statSync(PROCESSED_FILE).size;
    }

    console.log(`  Raw: ${(rawSize / 1024).toFixed(1)} KB (${rawSize} bytes)`);
    console.log(`  Processed: ${(processedSize / 1024).toFixed(1)} KB (${processedSize} bytes)`);
    console.log(`  Combined: ${((rawSize + processedSize) / 1024).toFixed(1)} KB`);

    const blobWriteStart = Date.now();
    try {
      const { getStore } = await import('@netlify/blobs');
      const store = getStore('build-scrapes');

      // Write raw blob (large — the full scraped data)
      console.log(`\n  Writing raw blob...`);
      const rawStart = Date.now();
      await store.set(`raw-${KEY_TS}.jsonl`, rawContent);
      console.log(`    Raw blob written in ${Date.now() - rawStart}ms — key: raw-${KEY_TS}.jsonl`);

      // Write processed blob (small JSON — aggregates, top-N, etc.)
      console.log(`  Writing processed blob...`);
      const procStart = Date.now();
      await store.setJSON(`processed-${KEY_TS}.json`, JSON.parse(processedContent));
      console.log(`    Processed blob written in ${Date.now() - procStart}ms — key: processed-${KEY_TS}.json`);

      // Write 'latest' pointer — clients can read this to find the most recent run
      console.log(`  Writing 'latest' pointer...`);
      await store.setJSON('latest', {
        run_ts: RUN_TS,
        run_unix_ts: KEY_TS,
        raw_blob_key: `raw-${KEY_TS}.jsonl`,
        processed_blob_key: `processed-${KEY_TS}.json`,
        raw_size_bytes: rawSize,
        processed_size_bytes: processedSize,
      });
      console.log(`    Latest pointer written`);

      // List all blobs in the store (auditable)
      console.log(`\n  Listing all blobs in 'build-scrapes' store...`);
      const list = await store.list();
      const blobCount = list.blobs?.length || 0;
      const totalStoreBytes = (list.blobs || []).reduce((s, b) => s + (b.size || 0), 0);
      console.log(`  Store contains ${blobCount} blobs totaling ${(totalStoreBytes / 1024).toFixed(1)} KB:`);
      for (const b of (list.blobs || [])) {
        console.log(`    - ${b.key} | ${(b.size / 1024).toFixed(1)} KB | modified=${b.last_modified}`);
      }

      console.log(`\n  BLOB_WRITE_OK — total time: ${Date.now() - blobWriteStart}ms`);
    } catch (e) {
      console.log(`  BLOB_WRITE_ERR: ${e.message}`);
      console.log(`  Stack: ${e.stack?.split('\n').slice(0, 6).join('\n')}`);
    }

    console.log(`\n========== STORE_PLUGIN_END ==========\n`);
  },
};
