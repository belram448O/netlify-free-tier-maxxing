// Plugin: writes a "build completed" marker blob + prints final env state
export default {
  onPostBuild: async ({ utils }) => {
    const out = { phase: 'onPostBuild', ts: new Date().toISOString() };
    out.has_blobs_context = !!process.env.NETLIFY_BLOBS_CONTEXT;
    out.site_id = process.env.SITE_ID;
    out.deploy_id = process.env.DEPLOY_ID;
    out.build_id = process.env.BUILD_ID;
    out.branch = process.env.BRANCH;
    out.context = process.env.CONTEXT;

    // Try writing a "build complete" marker to Blobs
    try {
      const { getStore } = await import('@netlify/blobs');
      const store = getStore('build-events');
      await store.setJSON(`build-${process.env.DEPLOY_ID || Date.now()}`, out);
      out.blobs_write_ok = true;
    } catch (e) { out.blobs_write_err = e.message; }

    console.log('=== POSTBUILD_RESULT ===');
    console.log(JSON.stringify(out, null, 2));
    console.log('=== POSTBUILD_RESULT_END ===');
  }
};
