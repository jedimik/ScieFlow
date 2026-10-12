const fs = require('node:fs');
const assert = require('node:assert/strict');
const {JSDOM} = require(process.argv[3]);
const streams = [];
class EventSource {
  constructor(url) { this.url = url; this.closed = false; this.listeners = new Map(); streams.push(this); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) { this.listeners.set(type, []); }
    this.listeners.get(type).push(listener);
  }
  close() { this.closed = true; }
}
const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {
  runScripts: 'dangerously', url: 'http://localhost/',
  beforeParse(window) { window.EventSource = EventSource; window.fetch = async () => ({ok: true, status: 303, redirected: false}); }
});
const document = dom.window.document;
const timeline = streams.find(s => s.url.includes('/events/stream'));
assert.ok(timeline, 'actual run template timeline EventSource');

// Per-turn mode badges render as inert text nodes (chat | harness-command).
const badges = [...document.querySelectorAll('.mode-badge')];
assert.ok(badges.length >= 2, 'each audited turn carries a mode badge');
for (const badge of badges) {
  const text = badge.textContent.trim();
  assert.ok(['chat', 'harness-command', 'unsupported'].includes(text), 'badge is inert enum text: ' + text);
  assert.equal(badge.querySelectorAll('script,img').length, 0);
}

// The composer: send form with mode + selection_revision fields.
const form = document.querySelector('form[action$="/say"]');
assert.ok(form, 'send form present');
assert.ok(form.querySelector('[name="mode"]'), 'composer mode field');
const revision = form.querySelector('[name="selection_revision"]');
assert.ok(revision && /^\d+$/.test(revision.value), 'canonical decimal selection_revision');

// Immediate repeat suppression: submitting twice rapidly must not send twice.
const sent = [];
const submitter = form.querySelector('button[name="action"][value="say"]') || form.querySelector('button');
assert.ok(submitter, 'send button');
const realSubmit = form.submit ? form.submit.bind(form) : null;
if (realSubmit) { form.submit = () => {}; } // jsdom navigation stub
form.addEventListener('submit', (event) => { event.preventDefault(); sent.push(Date.now()); });
function fire() {
  submitter.dispatchEvent(new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
  if (!sent.length) { form.dispatchEvent(new dom.window.Event('submit', {bubbles: true, cancelable: true})); }
}
fire();
fire();
assert.equal(sent.length, 1, 'rapid duplicate submission suppressed to one send');
if (submitter.disabled !== undefined) { assert.ok(submitter.disabled, 'submit button disabled during flight'); }

// Reset control: reset_session checkbox requires the confirm checkbox.
const handover = document.querySelector('form[action$="/say"].actions') || document.querySelector('form.actions');
assert.ok(handover, 'hand-over form present');
const confirmBox = handover.querySelector('[name="confirm_new_session"]');
assert.ok(confirmBox, 'reset requires an explicit confirm checkbox');
assert.ok(!confirmBox.checked, 'confirm starts unchecked');
const resetBox = handover.querySelector('[name="reset_session"]');
if (resetBox) {
  assert.ok(resetBox.type === 'checkbox' || resetBox.value !== undefined, 'reset is a deliberate control');
}

// After a successful selection/reset POST the timeline EventSource of the
// selection/reset target is terminated: the page's own script must close it
// on submit success rather than replaying/refreshing automatically.
let closedOnSubmit = false;
const originalClose = timeline.close.bind(timeline);
timeline.close = () => { closedOnSubmit = true; originalClose(); };
const handoverButton = handover.querySelector('button');
const resetSubmit = new dom.window.Event('submit', {bubbles: true, cancelable: true});
handover.addEventListener('submit', (event) => { event.preventDefault(); });
handover.dispatchEvent(resetSubmit);
// The selection script terminates the stale EventSource on success.
if (typeof closedOnSubmit === 'boolean') {
  // The template must close the stream when the selection succeeds; if the
  // page does not yet implement it, this assert fails (RED).
  assert.ok(closedOnSubmit, 'EventSource of selection/reset target terminated on success');
}

// No script/img injected through badge text: template autoescape held.
for (const badge of badges) { assert.ok(!badge.querySelector('script,img')); }

console.log('TASK7_REAL_DOM_PASS badges=' + badges.length + ' repeat_suppression=true reset_confirm=true eventsource_terminated=' + closedOnSubmit);
dom.window.close();
