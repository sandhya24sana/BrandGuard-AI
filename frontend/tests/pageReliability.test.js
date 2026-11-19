import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { Window } from 'happy-dom';
import { createServer } from 'vite';

const projectRoot = fileURLToPath(new URL('..', import.meta.url));

function response(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function deferredFetch() {
  const requests = [];
  globalThis.fetch = (url) => new Promise((resolve) => {
    requests.push({ url: String(url), resolve });
  });
  return requests;
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function createComponentHarness(context, componentPath) {
  const names = [
    'window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event',
    'MouseEvent', 'MutationObserver', 'IS_REACT_ACT_ENVIRONMENT', 'fetch',
  ];
  const originalDescriptors = new Map(
    names.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)])
  );
  const window = new Window({ url: 'http://localhost/' });
  for (const [name, value] of Object.entries({
    window,
    document: window.document,
    navigator: window.navigator,
    HTMLElement: window.HTMLElement,
    Node: window.Node,
    Event: window.Event,
    MouseEvent: window.MouseEvent,
    MutationObserver: window.MutationObserver,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    });
  }

  const React = await import('react');
  const { act } = React;
  const { createRoot } = await import('react-dom/client');
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    root: projectRoot,
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  const { default: Component } = await vite.ssrLoadModule(componentPath);
  const container = window.document.createElement('div');
  window.document.body.appendChild(container);
  const root = createRoot(container);

  context.after(async () => {
    await act(async () => root.unmount());
    await vite.close();
    window.happyDOM.abort();
    for (const [name, descriptor] of originalDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });

  return { React, act, Component, container, root, window };
}

function brandDetails(id, name) {
  return {
    success: true,
    brand: {
      id,
      name,
      tagline: `${name} tagline`,
      primary_tone: 'Clear',
      voice_traits: [],
    },
    facts: [],
    terminology: [],
    visual_rules: {
      primary_colors: [],
      secondary_colors: [],
      forbidden_colors: [],
    },
    claim_rules: [],
  };
}

function contentItem(id, title) {
  return {
    id,
    title,
    content_type: 'Ad Copy',
    channel: 'Digital',
    risk_score: 80,
    status: 'pending_review',
    current_version: 1,
    updated_at: '2026-01-01T00:00:00Z',
  };
}

test('GuidelinesManager ignores late responses from a previously selected brand', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/GuidelinesManager.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 1 }));
    await flush();
  });
  await act(async () => {
    root.render(React.createElement(Component, { brandId: 2 }));
    await flush();
  });
  assert.equal(requests.length, 2);

  await act(async () => {
    requests[1].resolve(response(brandDetails(2, 'Brand Two')));
    await flush();
  });
  assert.match(container.textContent, /Brand Two Guidelines/);

  await act(async () => {
    requests[0].resolve(response(brandDetails(1, 'Brand One')));
    await flush();
  });
  assert.match(container.textContent, /Brand Two Guidelines/);
  assert.doesNotMatch(container.textContent, /Brand One/);
});

test('GuidelinesManager exposes load failures and allows retry', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/GuidelinesManager.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 1 }));
    await flush();
  });
  await act(async () => {
    requests[0].resolve(response({ success: false, error: 'Guidelines unavailable' }, 503));
    await flush();
  });
  assert.match(container.textContent, /Guidelines unavailable/);

  await act(async () => {
    container.querySelector('button').dispatchEvent(
      new window.MouseEvent('click', { bubbles: true }),
    );
    await flush();
  });
  await act(async () => {
    requests[1].resolve(response(brandDetails(1, 'Recovered Brand')));
    await flush();
  });
  assert.match(container.textContent, /Recovered Brand Guidelines/);
});

test('ReviewHistory clears prior-brand content and ignores late list responses', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/ReviewHistory.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 1 }));
    await flush();
  });
  await act(async () => {
    root.render(React.createElement(Component, { brandId: 2 }));
    await flush();
  });
  assert.equal(requests.length, 2);

  await act(async () => {
    requests[1].resolve(response({ success: true, items: [contentItem(2, 'Brand Two Content')] }));
    await flush();
  });
  assert.match(container.textContent, /Brand Two Content/);

  await act(async () => {
    requests[0].resolve(response({ success: true, items: [contentItem(1, 'Brand One Content')] }));
    await flush();
  });
  assert.match(container.textContent, /Brand Two Content/);
  assert.doesNotMatch(container.textContent, /Brand One Content/);
});

test('ReviewHistory shows API failures instead of stale or empty results', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/ReviewHistory.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 1 }));
    await flush();
  });
  await act(async () => {
    requests[0].resolve(response({ success: false, error: 'History unavailable' }, 503));
    await flush();
  });
  assert.match(container.textContent, /History unavailable/);
  assert.equal(container.querySelector('[role="alert"]')?.textContent, 'History unavailable');
});

test('ContentAnalyzer records a review against the version created during submission', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/ContentAnalyzer.jsx',
  );
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const request = {
      url: String(url),
      body: options.body ? JSON.parse(options.body) : null,
    };
    calls.push(request);
    if (request.url.endsWith('/api/analysis/audit')) {
      return response({
        success: true,
        content_id: 31,
        version: 4,
        status: 'pending_review',
        result: {
          score: 100,
          risk_level: 'LOW',
          sub_scores: {
            terminology: 100,
            facts: 100,
            tone: 100,
            claims: 100,
            accessibility: 100,
          },
          issues: [],
          summary: 'No configured issues.',
          corrected_text: request.body.text,
          engine_used: 'RULE_BASED_FALLBACK',
          visual_validation: { supported: false, message: 'Not validated.' },
          readability: {
            reading_ease: 80,
            grade_level: 5,
            sentence_count: 1,
            reading_time_sec: 1,
          },
        },
      });
    }
    if (request.url.endsWith('/api/reviews')) {
      return response({
        success: true,
        new_status: 'approved',
        message: 'Review recorded.',
      });
    }
    return response({ success: false, error: 'Unexpected request' }, 404);
  };

  await act(async () => {
    root.render(React.createElement(Component, {
      brandId: 7,
      brandName: 'Review Test Brand',
    }));
    await flush();
  });
  const editor = container.querySelector('textarea');
  const valueSetter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value',
  ).set;
  await act(async () => {
    valueSetter.call(editor, ' Draft content ');
    editor.dispatchEvent(new window.Event('input', { bubbles: true }));
    await flush();
  });
  await act(async () => {
    container.querySelector('[title="Sign-off & Approve Content"]').click();
    await flush();
  });

  const reviewerName = container.querySelector('input[placeholder="Reviewer Name"]');
  const nameSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  ).set;
  await act(async () => {
    nameSetter.call(reviewerName, 'Reviewer');
    reviewerName.dispatchEvent(new window.Event('input', { bubbles: true }));
    await flush();
  });
  await act(async () => {
    container.querySelector('button[type="submit"]').click();
    await flush();
  });

  const auditCall = calls.find((call) => call.url.endsWith('/api/analysis/audit'));
  const reviewCall = calls.find((call) => call.url.endsWith('/api/reviews'));
  assert.equal(auditCall.body.text, ' Draft content ');
  assert.equal(reviewCall.body.content_id, 31);
  assert.equal(reviewCall.body.content_version, 4);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3600));
  });
});

test('App reports brand-load failures instead of showing an empty-brand state', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/App.jsx',
  );
  let brandRequestCount = 0;
  let completeRetry;
  globalThis.fetch = async (url) => {
    if (String(url).endsWith('/api/brands')) {
      brandRequestCount += 1;
      if (brandRequestCount > 1) {
        return new Promise((resolve) => {
          completeRetry = () => resolve(response({
            success: false,
            error: 'Brand service unavailable',
          }, 503));
        });
      }
      return response({ success: false, error: 'Brand service unavailable' }, 503);
    }
    return response({
      success: true,
      ai_configured: false,
      active_engine: 'Rule-Based Fallback',
      mode: 'Rule-Based Fallback',
    });
  };

  await act(async () => {
    root.render(React.createElement(Component));
    await flush();
    await flush();
  });
  assert.match(container.textContent, /Brand service unavailable/);
  assert.doesNotMatch(container.textContent, /Create your first brand/);
  assert.ok(
    Array.from(container.querySelectorAll('button')).some((button) => button.textContent.includes('Retry')),
  );
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Retry'))
      .click();
    await Promise.resolve();
  });
  assert.match(container.textContent, /Loading BrandGuard AI/);
  assert.doesNotMatch(container.textContent, /Create your first brand/);
  await act(async () => {
    completeRetry();
    await flush();
  });
  assert.match(container.textContent, /Brand service unavailable/);
});

test('ReviewHistory surfaces report-export API failures', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/ReviewHistory.jsx',
  );
  globalThis.fetch = async (url) => {
    if (String(url).includes('/api/content?')) {
      return response({ success: true, items: [contentItem(2, 'Export failure report')] });
    }
    return response({ success: false, error: 'Report service unavailable' }, 503);
  };

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 2 }));
    await flush();
  });
  await act(async () => {
    container.querySelector('[title="Download JSON Report"]').click();
    await flush();
  });
  assert.match(container.textContent, /Report service unavailable/);
  assert.equal(container.querySelector('[role="alert"]')?.textContent, 'Report service unavailable');
});
