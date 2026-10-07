import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { ROUTES, matchRoute } from '../app/router';
import routeSnapshot from './fixtures/route-registry.json';

const repo = path.resolve(process.cwd(), '..');
const numbered = /\b[A-Za-z_$][\w$]*(?:_real_|_CONST_)\d+\b/;
const verified = /\bverified\s*:\s*true\s*,\s*real\s*:\s*true\b/;
const declarations = /\b(?:function|const|let|var|class|interface|type)\s+([A-Za-z_$][\w$]*)_\d+\b/g;

function sources(root: string): string[] {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(root, entry.name);
    if (['node_modules', '.next', 'dist', 'build', '.cache', '.git'].includes(entry.name)) return [];
    if (entry.isDirectory()) return sources(file);
    return /\.(?:tsx?|jsx?|mjs)$/.test(entry.name) ? [file] : [];
  });
}

function violations(text: string): boolean {
  const counts = new Map<string, number>();
  for (const match of text.matchAll(declarations)) counts.set(match[1], (counts.get(match[1]) || 0) + 1);
  return numbered.test(text) || verified.test(text) || [...counts.values()].some(count => count >= 15);
}

function stripParsed(source: string, symbols: string[]): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'voxdesk-tail-'));
  try {
    const file = path.join(dir, 'fixture.tsx');
    fs.writeFileSync(file, source);
    const result = execFileSync(process.execPath, [path.join(repo, 'scripts/strip_generated_tails.mjs'), file, JSON.stringify(symbols)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    const ranges: [number, number][] = JSON.parse(result);
    const chars = Array.from(source);
    for (const [start, end] of ranges.reverse()) chars.splice(start, end - start);
    return chars.join('');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

describe('Generated-tail regression guard', () => {
  it('scans both shipped and shadow frontend trees without excluding tests', () => {
    const findings = ['dashboard/src', 'dashboard-next'].flatMap(root => sources(path.join(repo, root)))
      .filter(file => violations(fs.readFileSync(file, 'utf8')))
      .map(file => path.relative(repo, file));
    expect(findings).toEqual([]);
  });

  it('detects explicit definitions, shared stems, and multiline verification flags', () => {
    const name = 'sample_' + 'real_' + 2;
    const constant = 'SAMPLE_' + 'CONST_' + 2;
    expect(violations(`export function ${name}() { return {}; }`)).toBe(true);
    expect(violations(`export const ${constant} = 2;`)).toBe(true);
    expect(violations(Array.from({ length: 15 }, (_, i) => `export const helper_${i} = ${i};`).join('\n'))).toBe(true);
    expect(violations(['verified', 'real'].map(key => `${key}: true`).join(',\n'))).toBe(true);
    expect(violations('export function RealComponent() { return null; }')).toBe(false);
  });

  it('preserves complete Unicode component heads and real declarations following a tail', () => {
    const name = 'fixture_' + 'real_' + 1;
    const head = 'export function Page() { return <div>বাংলা 🎧</div>; }\n';
    const realAfter = '\nexport const meaningful = "retained";\n';
    expect(stripParsed(head + `export function ${name}() { return { nested: { value: "}" } }; }` + realAfter, [name])).toBe(head + realAfter);
  });

  it('removes only true verification properties and keeps neighboring data', () => {
    const properties = ['verified: true', 'real: true'].join(', ');
    const source = `export const status = { id: 7, ${properties}, label: "sample" };`;
    expect(stripParsed(source, [])).toBe('export const status = { id: 7,  label: "sample" };');
  });

  it('rejects mixed declarations rather than deleting real values', () => {
    const name = 'fixture_' + 'CONST_' + 3;
    expect(() => stripParsed(`export const ${name} = 3, realValue = 4;`, [name])).toThrow();
  });

  it('refuses nested definitions and syntax errors', () => {
    const name = 'fixture_' + 'real_' + 4;
    expect(() => stripParsed(`function Page() { function ${name}() {} }`, [name])).toThrow();
    expect(() => stripParsed(`export function ${name}() {`, [name])).toThrow();
  });
});

describe('Route preservation during cleanup', () => {
  it('preserves every route, metadata field, alias, and declaration order', () => {
    expect(ROUTES).toEqual(routeSnapshot);
  });

  it('does not crash on malformed percent-encoded route parameters', () => {
    expect(matchRoute('/calls/%E0%A4%A')).toBeNull();
    expect(matchRoute('/calls/call%20one')?.params.id).toBe('call one');
  });
});
