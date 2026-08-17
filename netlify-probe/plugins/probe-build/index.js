// Build plugin that runs AFTER the build — captures end-of-build env state + writes a blob
export default {
  onPostBuild: async ({ utils, netlifyConfig }) => {
    const { execSync } = await import('node:child_process');
    const out = { phase: 'onPostBuild', timestamp: new Date().toISOString() };

    out.env_keys_count = Object.keys(process.env).length;
    out.has_blobs_context = !!process.env.NETLIFY_BLOBS_CONTEXT;
    if (process.env.NETLIFY_BLOBS_CONTEXT) {
      try {
        const ctx = JSON.parse(Buffer.from(process.env.NETLIFY_BLOBS_CONTEXT, 'base64').toString());
        out.blobs_context = {
          apiURL: ctx.apiURL,
          edgeURL: ctx.edgeURL,
          uncachedEdgeURL: ctx.uncachedEdgeURL,
          siteID: ctx.siteID,
          primaryRegion: ctx.primaryRegion,
          deployID: ctx.deployID,
          hasToken: !!ctx.token
        };
      } catch (e) { out.blobs_context_err = e.message; }
    }

    out.build_duration_total_seconds = process.env.BUILD_TOTAL_DURATION_S || 'unknown';
    out.cache_hit = execSync('echo $CACHE_HIT 2>&1 || echo none').toString().trim();

    // Try writing to Blobs (since the build context should have access)
    let blobsWriteOk = false, blobsWriteErr = null;
    try {
      const { getStore } = await import('@netlify/blobs');
      const store = getStore('probe-store');
      await store.setJSON(`build-probe-${Date.now()}`, {
        ts: new Date().toISOString(),
        msg: 'written from build plugin',
        context: out.blobs_context
      });
      blobsWriteOk = true;
    } catch (e) { blobsWriteErr = e.message; }
    out.blobs_write_ok = blobsWriteOk;
    out.blobs_write_err = blobsWriteErr;

    console.log('=== POSTBUILD PROBE ===');
    console.log(JSON.stringify(out, null, 2));
    console.log('=== END POSTBUILD PROBE ===');
  }
};
