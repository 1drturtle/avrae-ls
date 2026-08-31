const path = require("path");

function gvarIdFromUriPath(uriPath) {
  const filename = path.posix.basename(uriPath);
  return decodeURIComponent(filename.endsWith(".gvar") ? filename.slice(0, -5) : filename);
}

module.exports = { gvarIdFromUriPath };
