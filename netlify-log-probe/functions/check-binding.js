exports.handler = async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const result = {};
  try {
    // Read binding.js
    const bindingPath = '/var/task/node_modules/curl-cffi-node/dist/binding.js';
    if (fs.existsSync(bindingPath)) {
      result.binding_js = fs.readFileSync(bindingPath, 'utf8');
    }
    // Try loading the .node file directly
    const nativePath = '/var/task/node_modules/@curl-cffi-node/linux-x64-gnu/curl-cffi-node.linux-x64-gnu.node';
    result.native_path_exists = fs.existsSync(nativePath);
    if (result.native_path_exists) {
      result.native_file_size = fs.statSync(nativePath).size;
      try {
        const native = require(nativePath);
        result.native_keys = Object.keys(native).slice(0, 20);
        result.native_typeof = typeof native;
      } catch (e) {
        result.native_load_err = e.message;
      }
    }
    // Check what's in @curl-cffi-node/linux-x64-gnu
    const scopedPath = '/var/task/node_modules/@curl-cffi-node/linux-x64-gnu';
    if (fs.existsSync(scopedPath)) {
      result.scoped_files = fs.readdirSync(scopedPath);
      // Check the package.json main field
      const pkgPath = path.join(scopedPath, 'package.json');
      if (fs.existsSync(pkgPath)) {
        const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
        result.scoped_pkg_main = pkg.main;
        result.scoped_pkg_files = pkg.files;
      }
    }
    // Also try the npm/linux-x64-gnu path
    const npmPath = '/var/task/node_modules/curl-cffi-node/npm/linux-x64-gnu';
    if (fs.existsSync(npmPath)) {
      result.npm_linux_x64_gnu_files = fs.readdirSync(npmPath);
    }
  } catch (e) { result.err = e.message; }
  return { statusCode: 200, body: JSON.stringify(result, null, 2) };
};
