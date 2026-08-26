import { describe, expect, it } from 'vitest';

import { esc, safeUrl } from './html';

describe('esc', () => {
  it('escapes every character that can break out of tooltip HTML', () => {
    expect(esc('&<>"\'')).toBe('&amp;&lt;&gt;&quot;&#39;');
  });

  it('neutralises an injected tag', () => {
    expect(esc('<img src=x onerror=alert(1)>')).toBe('&lt;img src=x onerror=alert(1)&gt;');
  });

  it('escapes a value used inside a quoted attribute', () => {
    expect(esc('" onmouseover="alert(1)')).toBe('&quot; onmouseover=&quot;alert(1)');
  });

  it('leaves plain text untouched', () => {
    expect(esc('login smoke · timeout')).toBe('login smoke · timeout');
  });

  it('stringifies numbers and turns missing values into an empty string', () => {
    expect(esc(42)).toBe('42');
    expect(esc(0)).toBe('0');
    expect(esc(null)).toBe('');
    expect(esc(undefined)).toBe('');
  });
});

describe('safeUrl', () => {
  it('passes absolute http and https links through', () => {
    expect(safeUrl('https://rp.example/ui/#demo/launches/all/9/42')).toBe(
      'https://rp.example/ui/#demo/launches/all/9/42',
    );
    expect(safeUrl('http://rp.example/ui/1')).toBe('http://rp.example/ui/1');
    expect(safeUrl('HTTPS://RP.EXAMPLE/ui')).toBe('HTTPS://RP.EXAMPLE/ui');
  });

  it('passes app-relative paths through', () => {
    expect(safeUrl('/ui/#demo/launches')).toBe('/ui/#demo/launches');
    expect(safeUrl('./item/42')).toBe('./item/42');
    expect(safeUrl('../item/42')).toBe('../item/42');
  });

  it('rejects script-bearing schemes', () => {
    expect(safeUrl('javascript:alert(1)')).toBeNull();
    expect(safeUrl('JAVASCRIPT:alert(1)')).toBeNull();
    expect(safeUrl('JavaScript:alert(1)')).toBeNull();
    expect(safeUrl('data:text/html,<script>alert(1)</script>')).toBeNull();
    expect(safeUrl('DATA:text/html,x')).toBeNull();
    expect(safeUrl('vbscript:msgbox(1)')).toBeNull();
  });

  it('rejects a scheme hidden behind leading whitespace or control characters', () => {
    expect(safeUrl('  javascript:alert(1)')).toBeNull();
    expect(safeUrl('\njavascript:alert(1)')).toBeNull();
    expect(safeUrl('\t javascript:alert(1)')).toBeNull();
    // Browsers ignore a tab inside the scheme; an allow-list never matches it.
    expect(safeUrl('java\tscript:alert(1)')).toBeNull();
  });

  it('trims a link it does accept', () => {
    expect(safeUrl('  https://rp.example/ui  ')).toBe('https://rp.example/ui');
  });

  it('rejects a protocol-relative link to another host', () => {
    expect(safeUrl('//evil.example/steal')).toBeNull();
  });

  it('rejects missing, empty and non-link values', () => {
    expect(safeUrl(null)).toBeNull();
    expect(safeUrl(undefined)).toBeNull();
    expect(safeUrl('')).toBeNull();
    expect(safeUrl('   ')).toBeNull();
    expect(safeUrl('rp.example/ui/42')).toBeNull();
    expect(safeUrl('mailto:qa@example.com')).toBeNull();
  });
});
