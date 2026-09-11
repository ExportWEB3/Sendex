import DOMPurify, { type Config } from 'dompurify';

const EMAIL_HTML_CONFIG: Config = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: [
    'base',
    'button',
    'embed',
    'form',
    'input',
    'meta',
    'object',
    'script',
    'select',
    'textarea',
  ],
  FORBID_ATTR: ['srcdoc'],
};

const INLINE_HTML_CONFIG: Config = {
  ALLOWED_TAGS: ['b', 'br', 'em', 'i', 'mark', 'span', 'strong'],
  ALLOWED_ATTR: ['class', 'style'],
};

/** Sanitize user-authored email markup before rendering it in the dashboard. */
export function sanitizeEmailHtml(html: string): string {
  return DOMPurify.sanitize(html, EMAIL_HTML_CONFIG);
}

/** Sanitize generated inline preview markup while preserving variable highlights. */
export function sanitizeInlineHtml(html: string): string {
  return DOMPurify.sanitize(html, INLINE_HTML_CONFIG);
}
