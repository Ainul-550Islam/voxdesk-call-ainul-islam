// Parser seam for strip_generated_tails.py. No regex-based brace deletion.
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../dashboard/package.json', import.meta.url));
const { parse } = require('@babel/parser');
const [file, namesJson] = process.argv.slice(2);
const names = new Set(JSON.parse(namesJson));
const text = fs.readFileSync(file, 'utf8');
const plugins = file.endsWith('.tsx') || file.endsWith('.jsx') ? ['typescript', 'jsx'] : ['typescript'];
const source = parse(text, { sourceType: 'module', plugins, tokens: true });
const offsets = new Int32Array(text.length + 1);
let utf16 = 0;
let codepoint = 0;
for (const char of text) {
  utf16 += char.length;
  offsets[utf16] = ++codepoint;
}
const declarations = [];
const matched = new Set();
for (const statement of source.program.body) {
  const node = statement.type === 'ExportNamedDeclaration' ? statement.declaration : statement;
  if (!node) continue;
  const identifiers = node.type === 'VariableDeclaration'
    ? node.declarations.map(d => d.id.type === 'Identifier' ? d.id.name : '')
    : node.id?.type === 'Identifier' ? [node.id.name] : [];
  if (!identifiers.some(name => names.has(name))) continue;
  if (identifiers.some(name => !names.has(name))) throw new Error('Mixed real/generated declaration: ' + file);
  for (const name of identifiers) matched.add(name);
  declarations.push(statement);
}
if (matched.size !== names.size) throw new Error('Not every candidate is a top-level declaration: ' + file);
const ranges = declarations.map(node => [node.start, node.end]);

function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) {
    for (const child of node) walk(child, visit);
    return;
  }
  if (typeof node.type === 'string') visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (key === 'loc' || key === 'tokens' || key === 'comments' || key === 'leadingComments' || key === 'trailingComments' || key === 'innerComments' || key === 'extra') continue;
    if (value && typeof value === 'object') walk(value, visit);
  }
}

function keyName(property) {
  if (property.computed || !property.key) return null;
  if (property.key.type === 'Identifier') return property.key.name;
  if (property.key.type === 'StringLiteral') return property.key.value;
  return null;
}

function isTrueProperty(property, name) {
  return property.type === 'ObjectProperty' && keyName(property) === name &&
    property.value.type === 'BooleanLiteral' && property.value.value === true;
}

function commaBetween(left, right) {
  const token = source.tokens.find(item => item.start >= left.end && item.end <= right.start && item.type.label === ',');
  if (!token) throw new Error('Could not locate object-property separator in ' + file);
  return token;
}

function objectPropertyRemovalRanges(object) {
  const properties = object.properties;
  const indexes = properties.flatMap((property, index) =>
    isTrueProperty(property, 'verified') || isTrueProperty(property, 'real') ? [index] : []);
  if (!indexes.some(index => isTrueProperty(properties[index], 'verified')) ||
      !indexes.some(index => isTrueProperty(properties[index], 'real'))) return [];
  const rangesForObject = [];
  let position = 0;
  while (position < indexes.length) {
    const first = indexes[position];
    let last = first;
    position += 1;
    while (position < indexes.length && indexes[position] === last + 1) {
      last = indexes[position];
      position += 1;
    }
    if (first === 0 && last === properties.length - 1) {
      rangesForObject.push([properties[first].start, properties[last].end]);
    } else if (last === properties.length - 1) {
      rangesForObject.push([commaBetween(properties[first - 1], properties[first]).start, properties[last].end]);
    } else {
      const comma = commaBetween(properties[last], properties[last + 1]);
      rangesForObject.push([properties[first].start, comma.end]);
    }
  }
  return rangesForObject;
}

walk(source.program, node => {
  if (node.type !== 'ObjectExpression') return;
  if (declarations.some(declaration => declaration.start <= node.start && node.end <= declaration.end)) return;
  ranges.push(...objectPropertyRemovalRanges(node));
});
const ordered = ranges.map(([start, end]) => [offsets[start], offsets[end]]).sort((a, b) => a[0] - b[0]);
for (let index = 1; index < ordered.length; index += 1) {
  if (ordered[index][0] < ordered[index - 1][1]) throw new Error('Overlapping source edits in ' + file);
}
process.stdout.write(JSON.stringify(ordered));
