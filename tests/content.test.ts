import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { loadSite, parentUrl } from '../src/lib/content.ts';

async function fixture(files: Record<string, string>, run: (root: string) => Promise<void>) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'athenaeum-content-'));
  try {
    for (const [name, text] of Object.entries(files)) {
      await mkdir(path.dirname(path.join(root, name)), { recursive: true });
      await writeFile(path.join(root, name), text);
    }
    await run(root);
  } finally { await rm(root, { recursive: true, force: true }); }
}

test('an empty directory has a useful home page', () => fixture({}, async (root) => {
  const { pages, assets } = await loadSite(root);
  assert.deepEqual(pages.map((p) => [p.url, p.title, p.generated]), [['/', 'Athenaeum', true]]);
  assert.deepEqual(assets, []);
}));

test('empty frontmatter is valid and app deep links preserve their trailing-slash behavior', () => fixture({
  'index.md': '---\n---\n# Home\n[Root](/quacktuaries)\n[Deep](/quacktuaries/session/1?view=test)',
}, async (root) => {
  const { pages } = await loadSite(root, ['quacktuaries']);
  assert.match(pages[0].html, /href="\/quacktuaries\/"/);
  assert.match(pages[0].html, /href="\/quacktuaries\/session\/1\?view=test"/);
}));

test('nested pages generate indexes, headings supply titles, and order controls listings', () => fixture({
  'Lessons/Unit_One/example.md': '## A first lesson',
  'projects/index.md': '---\ntitle: Projects\norder: -1\n---\n',
}, async (root) => {
  const { pages } = await loadSite(root);
  assert.equal(pages[0].title, 'Projects');
  assert(pages.some((p) => p.url === '/lessons/unit-one/'));
  assert.equal(pages.find((p) => p.url.endsWith('/example/'))?.title, 'A first lesson');
  assert.equal(parentUrl('/lessons/unit-one/example/'), '/lessons/unit-one/');
}));

test('heading-free Markdown uses a filename title and frontmatter takes precedence', () => fixture({
  'index.md': 'A collection.',
  'first_lesson.md': 'A lesson without a heading.',
  'second.md': '---\ntitle: Explicit title\n---\n# Different heading',
}, async (root) => {
  const { pages } = await loadSite(root);
  assert.equal(pages.find((p) => p.url === '/')?.title, 'Athenaeum');
  assert.equal(pages.find((p) => p.url === '/first-lesson/')?.title, 'First lesson');
  assert.equal(pages.find((p) => p.url === '/second/')?.title, 'Explicit title');
}));

test('only assets referenced by published pages are collected; drafts and draft sections vanish', () => fixture({
  'index.md': '# Home\n[Data](assets/public.csv)',
  'draft.md': '---\ndraft: true\n---\n[Secret](assets/secret.pdf)',
  'private/index.md': '---\ndraft: true\n---\n# Private',
  'private/child.md': '# Hidden child\n[Other](../assets/other.csv)',
  'assets/public.csv': 'x\n1', 'assets/secret.pdf': 'private', 'assets/other.csv': 'private',
}, async (root) => {
  const site = await loadSite(root);
  assert.deepEqual(site.pages.map((p) => p.url), ['/']);
  assert.deepEqual(site.assets, [{ source: 'assets/public.csv', url: '/_content/assets/public.csv' }]);
  assert.match(site.pages[0].html, /href="\/_content\/assets\/public.csv"/);
}));

test('links, fragments, footnotes, local images, math, tables and code render together', () => fixture({
  'index.md': '# Home\n[Lesson](notes/example.md#worked-example)\n![Plot](assets/plot.png)\n[App](/quacktuaries/)',
  'notes/example.md': '# Lesson\n## Worked example\n$x^2$\n\n$$\n\\frac{1}{n}\n$$\n\n| x | y |\n| - | - |\n| 1 | 2 |\n\n```python\nprint(1)\n```\n\nA note.[^a]\n\n[^a]: Explained.\n\n[Home](../index.md?from=lesson#home)',
  'assets/plot.png': 'image fixture',
}, async (root) => {
  const { pages, assets } = await loadSite(root, ['quacktuaries']);
  assert.match(pages.find((p) => p.url === '/')!.html, /href="\/notes\/example\/#worked-example"/);
  const lesson = pages.find((p) => p.url === '/notes/example/')!.html;
  for (const pattern of [/<math/, /katex-display/, /<table>/, /language-python/, /data-footnote-ref/, /href="\/\?from=lesson#home"/]) assert.match(lesson, pattern);
  assert.equal(assets.length, 1);
}));

const invalidCases: [string, Record<string, string>, RegExp][] = [
  ['duplicate routes', { 'foo.md': '# One', 'foo/index.md': '# Two' }, /Duplicate route/],
  ['normalization collisions', { 'Unit One.md': '# One', 'unit_one.md': '# Two' }, /Duplicate route/],
  ['reserved app', { 'quacktuaries/index.md': '# No' }, /reserved/],
  ['invalid filename', { 'bad.name.md': '# No' }, /Invalid route segment/],
  ['typed metadata', { 'index.md': '---\ndraft: "false"\n---\n# No' }, /draft/],
  ['unknown metadata', { 'index.md': '---\nsecret: yes\n---\n# No' }, /Unrecognized/],
  ['bad date', { 'index.md': '---\ndate: 2026-02-30\n---\n# No' }, /real YYYY-MM-DD/],
  ['missing project href', { 'index.md': '---\nkind: project\n---\n# No' }, /require href/],
  ['broken link', { 'index.md': '# Home\n[Missing](missing.md)' }, /Broken link/],
  ['missing anchor', { 'index.md': '# Home\n[Missing](#unknown)' }, /Missing heading/],
  ['draft page link', { 'index.md': '[Hidden](draft.md)', 'draft.md': '---\ndraft: true\n---\n# Draft' }, /draft/],
  ['traversal', { 'index.md': '[Outside](../secrets.txt)' }, /escapes content/],
  ['encoded traversal', { 'index.md': '[Outside](%2e%2e/secrets.txt)' }, /escapes content/],
  ['hidden asset', { 'index.md': '[Hidden](.private.txt)', '.private.txt': 'no' }, /hidden file/],
  ['script URL', { 'index.md': '[Bad](javascript:alert)' }, /Unsupported URL/],
  ['raw HTML asset bypass', { 'index.md': '<img src="assets/private.png">' }, /Raw HTML/],
  ['unsafe download type', { 'index.md': '[HTML](page.html)', 'page.html': '<script>bad</script>' }, /unsupported asset/],
  ['invalid math', { 'index.md': '$\\notacommand{x}$' }, /Invalid math/],
];
for (const [name, files, error] of invalidCases) test(`rejects ${name} with source context`, () => fixture(files, async (root) => {
  await assert.rejects(loadSite(root, ['quacktuaries']), (e: Error) => {
    assert.match(e.message, /content\//);
    assert.match(e.message, error);
    return true;
  });
}));

test('symlinks cannot import files from outside content', () => fixture({ 'index.md': '# Home' }, async (root) => {
  await symlink('/tmp', path.join(root, 'outside'));
  await assert.rejects(loadSite(root), /Symlinks/);
}));

test('removing the last published reference stops exporting its asset', () => fixture({
  'index.md': '[Download](data.csv)', 'data.csv': 'x\n1',
}, async (root) => {
  assert.equal((await loadSite(root)).assets.length, 1);
  await writeFile(path.join(root, 'index.md'), '# Home');
  assert.equal((await loadSite(root)).assets.length, 0);
}));
