exports.handler = async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const result = {};
  try {
    const ccDir = '/var/task/node_modules/curl-cffi-node';
    if (fs.existsSync(ccDir)) {
      result.curl_cffi_node_files = fs.readdirSync(ccDir);
      // Check if @curl-cffi-node exists
      const scoped = '/var/task/node_modules/@curl-cffi-node';
      result.scoped_exists = fs.existsSync(scoped);
      if (fs.existsSync(scoped)) {
        result.scoped_files = fs.readdirSync(scoped);
      }
      // Check the dist/binding.js
      const bindingPath = path.join(ccDir, 'dist/binding.js');
      if (fs.existsSync(bindingPath)) {
        result.binding_js_first_500 = fs.readFileSync(bindingPath, 'utf8').substring(0, 500);
      }
      // Try to find the .node file
      const npmDir = path.join(ccDir, 'npm/linux-x64-gnu');
      if (fs.existsSync(npmDir)) {
        result.npm_linux_x64_gnu_files = fs.readdirSync(npmDir);
      }
    }
  } catch (e) { result.err = e.message; }
  return { statusCode: 200, body: JSON.stringify(result, null, 2) };
};
