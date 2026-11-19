import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

function findButton(element) {
  if (Array.isArray(element)) {
    for (const child of element) {
      const button = findButton(child);
      if (button) return button;
    }
    return null;
  }
  if (!element || typeof element !== 'object') return null;
  if (element.type === 'button') return element;
  return findButton(element.props?.children);
}

test('IssueCard shows Apply for a fixable legacy weight-loss claim', async () => {
  const projectRoot = fileURLToPath(new URL('..', import.meta.url));
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    root: projectRoot,
    logLevel: 'silent',
    server: { middlewareMode: true }
  });
  try {
    const { default: IssueCard } = await vite.ssrLoadModule('/src/components/IssueCard.jsx');
    const fixableIssue = {
      id: 'claim_1_10_32',
      category: 'Compliance & Claims',
      severity: 'CRITICAL',
      rule: 'Unsupported Medical Claims',
      matched_text: 'guarantees weight loss',
      replacement_text: 'Enjoy food made with natural ingredients.',
      suggestion: 'Enjoy food made with natural ingredients.',
      required_disclaimer: 'Avoid medical claims. Use: "Enjoy food made with natural ingredients."',
      auto_fixable: true,
      location: { start: 10, end: 32 }
    };
    let appliedIssue;
    const card = IssueCard({
      issue: fixableIssue,
      onApplyFix: (issue) => { appliedIssue = issue; }
    });

    assert.match(renderToStaticMarkup(card), /<button\b[^>]*>.*Apply/s);
    findButton(card).props.onClick();
    assert.equal(appliedIssue, fixableIssue);

    const manualIssue = {
      ...fixableIssue,
      id: 'claim_1_10_23',
      replacement_text: null,
      suggestion: '',
      required_disclaimer: 'Use only substantiated claims.',
      auto_fixable: false
    };
    const manualCard = IssueCard({ issue: manualIssue, onApplyFix: () => {} });
    assert.doesNotMatch(renderToStaticMarkup(manualCard), /<button\b/);
    assert.match(renderToStaticMarkup(manualCard), /Required Disclaimer/);
  } finally {
    await vite.close();
  }
});
