const fs = require('node:fs');
const assert = require('node:assert/strict');
const {JSDOM} = require(process.argv[3]);
const streams = [];
class EventSource {
  constructor(url) { this.url = url; this.closed = false; streams.push(this); }
  close() { this.closed = true; }
}
const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {
  runScripts: 'dangerously', url: 'http://localhost/',
  beforeParse(window) { window.EventSource = EventSource; }
});
const out = dom.window.document.getElementById('out');
const stream = streams.find(s => s.url.includes('/log/stream'));
assert.ok(stream, 'actual job template EventSource');
assert.ok(out.textContent.includes('hello from the job'));
stream.onmessage({data: 'hello from the job'});
assert.equal(out.textContent, 'hello from the job\n', 'replace initial panel once, no duplicated replay');
const evil = '<script>window.executed=true</script><img src=x onerror="window.executed=true">**markdown**';
stream.onmessage({data: evil});
assert.equal(dom.window.executed, undefined);
assert.equal(out.querySelectorAll('script,img').length, 0);
assert.ok(out.textContent.includes(evil));
for (let i = 0; i < 80; i++) {
  stream.onmessage({data: '😀'.repeat(4000)});
  assert.ok(out.textContent.length <= 200000, 'UTF16 aggregate cap on every append');
  assert.ok(!/[\uD800-\uDBFF]$/.test(out.textContent), 'no trailing half surrogate');
  assert.ok(!/(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(out.textContent), 'no orphan low surrogate');
}
assert.ok(stream.closed, 'saturation closes view only');
assert.equal((out.textContent.match(/raw artifact retained/g) || []).length, 1);
const frozen = out.textContent;
stream.onmessage({data: 'after saturation'});
assert.equal(out.textContent, frozen);
assert.equal(streams.length, 1, 'no reconnect/resubmit');
console.log('REAL_DOM_PASS jsdom=' + require(process.argv[3].replace(/lib\/api.js$/, 'package.json')).version + ' units=' + out.textContent.length + ' inert=true notice=1 prefix_duplication=false');
dom.window.close();
