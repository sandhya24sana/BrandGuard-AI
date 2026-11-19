import assert from 'node:assert/strict';
import test from 'node:test';
import { api } from '../src/api.js';

test('API helper reports network failures as explicit failed responses', async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => {
    throw new Error('offline');
  };

  const response = await api.getHealth();
  assert.equal(response.success, false);
  assert.equal(response.error, 'offline');
});

test('API helper reports non-JSON upstream failures without parsing HTML as JSON', async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => new Response('<html>proxy unavailable</html>', {
    status: 502,
    statusText: 'Bad Gateway',
    headers: { 'content-type': 'text/html' },
  });

  const response = await api.getHealth();
  assert.equal(response.success, false);
  assert.match(response.error, /HTTP 502/);
  assert.match(response.error, /proxy unavailable/);
});
