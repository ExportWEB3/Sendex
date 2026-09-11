// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { sanitizeEmailHtml, sanitizeInlineHtml } from '../src/utils/sanitize-html';

describe('HTML sanitization', () => {
  it('removes executable content from email previews', () => {
    const sanitized = sanitizeEmailHtml(`
      <script>window.evil = true</script>
      <form><input name="secret"></form>
      <img src="x" onerror="window.evil = true">
      <a href="javascript:alert(1)">unsafe link</a>
      <p style="color: red">Safe content</p>
    `);
    const container = document.createElement('div');
    container.innerHTML = sanitized;

    expect(container.querySelector('script, form, input')).toBeNull();
    expect(container.querySelector('img')?.hasAttribute('onerror')).toBe(false);
    expect(container.querySelector('a')?.hasAttribute('href')).toBe(false);
    expect(container.querySelector('p')?.textContent).toBe('Safe content');
  });

  it('preserves generated emphasis while rejecting arbitrary inline markup', () => {
    const sanitized = sanitizeInlineHtml(
      '<strong>Order</strong> <span class="variable" onclick="alert(1)">42</span><img src="x">',
    );
    const container = document.createElement('div');
    container.innerHTML = sanitized;

    expect(container.querySelector('strong')?.textContent).toBe('Order');
    expect(container.querySelector('span')?.className).toBe('variable');
    expect(container.querySelector('span')?.hasAttribute('onclick')).toBe(false);
    expect(container.querySelector('img')).toBeNull();
  });
});
