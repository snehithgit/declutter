import test from 'node:test';
import assert from 'node:assert/strict';
import {parseRoute, routeHash, createLatestGate, fetchJson, makeScope, sameScope} from './state.mjs';
const review = 'a'.repeat(64), run = 'b'.repeat(32);
test('hash navigation preserves direct view and detail links', () => {
  assert.deepEqual(parseRoute('#/review/'+review), {page:'review', detail:review});
  assert.deepEqual(parseRoute('#/export/'+run), {page:'export', detail:run});
  assert.deepEqual(parseRoute(routeHash('classification')), {page:'classification', detail:null});
  assert.deepEqual(parseRoute('#/export/'+review), {page:'export', detail:null});
  assert.deepEqual(parseRoute('#/untrusted'), {page:'overview', detail:null});
  assert.equal(routeHash('review', review), '#/review/'+review);
});
test('latest request gate rejects late stale results including on cleanup', async () => {
  const gate = createLatestGate();
  const first = gate.next();
  const second = gate.next();
  assert.equal(first.signal.aborted, true);
  assert.equal(first.isCurrent(), false);
  assert.equal(second.isCurrent(), true);
  await Promise.resolve();
  assert.equal(first.isCurrent(), false);
  gate.cancel();
  assert.equal(second.isCurrent(), false);
});
test('all-match selection is immutable and filter scoped', () => {
  const filters = {search:'wedding', root_id:2};
  const scope = makeScope(filters, 30, 'b'.repeat(64));
  filters.search = 'other';
  assert.equal(scope.filters.search, 'wedding');
  assert.equal(sameScope(scope, filters), false);
  assert.equal(sameScope(scope, {search:'wedding',root_id:2}),true);
  assert.throws(()=>makeScope(filters, 0,'bad'));
});
test('non JSON and failed JSON responses report useful source/status', async () => {
  const previous = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response('<html>502</html>',{status:502,headers:{'content-type':'text/html'}});
    await assert.rejects(fetchJson('/api/reviews'), /HTTP 502; text\/html/);
    globalThis.fetch = async () => new Response('{broken',{status:200,headers:{'content-type':'application/json'}});
    await assert.rejects(fetchJson('/api/reviews'), /Invalid JSON/);
    globalThis.fetch = async () => new Response('{"detail":"Selection changed"}',{status:409,headers:{'content-type':'application/json'}});
    await assert.rejects(fetchJson('/api/reviews/bulk'), /Selection changed/);
  } finally {globalThis.fetch=previous;}
});

test('out-of-order responses do not overwrite the latest view', async () => {
  const gate = createLatestGate();
  const displayed = [];
  let oldResolve;
  const oldPromise = new Promise(resolve => {oldResolve = resolve;});
  const first = gate.next();
  const oldTask = oldPromise.then(value => {if (first.isCurrent()) displayed.push(value);});
  const second = gate.next();
  await Promise.resolve('new').then(value => {if (second.isCurrent()) displayed.push(value);});
  oldResolve('old');
  await oldTask;
  assert.deepEqual(displayed, ['new']);
});
