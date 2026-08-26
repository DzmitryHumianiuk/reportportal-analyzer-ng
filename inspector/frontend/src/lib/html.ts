// Shared HTML helpers for the ECharts tooltip formatters.
//
// An ECharts tooltip formatter returns a string that the library injects as
// HTML, so this file is the app's XSS boundary for chart hovers. Every value
// that comes from the API goes through esc() before it is interpolated, and
// every link goes through safeUrl() before it becomes an anchor. The text a
// user sees is unchanged; markup inside a test-item name stays inert.
//
// React views never need these: JSX escapes its children on its own.

const ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

/**
 * Escape a value for interpolation into tooltip HTML — either as text or
 * inside a quoted attribute. Missing values become an empty string.
 */
export function esc(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/**
 * Allow-list for links that may become anchors: an absolute http(s) URL, or a
 * path relative to this app ("/…", "./…", "../…"). Everything else returns
 * null so the caller can degrade to plain text.
 *
 * The allow-list is deliberate: `javascript:`, `data:`, `vbscript:` and their
 * obfuscated spellings never match, and neither does a protocol-relative
 * "//other.host" link.
 */
export function safeUrl(url: string | null | undefined): string | null {
  if (url == null) return null;
  const trimmed = String(url).trim();
  if (!trimmed) return null;
  return /^(?:https?:\/\/|\/(?!\/)|\.{1,2}\/)/i.test(trimmed) ? trimmed : null;
}
