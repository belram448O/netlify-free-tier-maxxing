exports.handler = async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const result = { cwd: process.cwd(), env_node_path: process.env.NODE_PATH };
  try {
    result.var_task = fs.readdirSync('/var/task');
    result.var_task_node_modules = fs.existsSync('/var/task/node_modules') ? fs.readdirSync('/var/task/node_modules') : 'NO node_modules';
    result.var_runtime = fs.existsSync('/var/runtime') ? fs.readdirSync('/var/runtime') : 'NO /var/runtime';
  } catch (e) { result.err = e.message; }
  return { statusCode: 200, body: JSON.stringify(result, null, 2) };
};
