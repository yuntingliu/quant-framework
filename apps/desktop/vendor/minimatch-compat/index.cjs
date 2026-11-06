// minimatch <6 exported a callable function with attached helpers. Preserve
// that shape for legacy consumers; all matching runs in patched upstream code.
const upstream = require('minimatch-upstream');

class Minimatch extends upstream.Minimatch {
  constructor(pattern, options) {
    super(pattern, options);
    // glob 7 reads _glob and calls String.match on parsed path segments.
    // v11 uses nonrecursive Matcher objects instead of RegExp objects. Adapt
    // only that protocol, retaining the patched matcher and its complexity.
    for (const row of this.set) {
      for (const segment of row) {
        if (typeof segment === 'object' && segment !== null) {
          segment._glob = segment.pattern;
          segment[Symbol.match] = function (value) {
            return this.test(value) ? [value] : null;
          };
        }
      }
    }
  }
}

const minimatch = (...args) => upstream.minimatch(...args);
module.exports = Object.assign(minimatch, upstream, { Minimatch });
