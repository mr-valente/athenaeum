import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdtemp, readFile, writeFile, symlink, rm, mkdir, readdir, realpath } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';

test('real builds publish selected assets and remove drafts, stale assets, and deleted pages', async () => {
  const project = fileURLToPath(new URL('../', import.meta.url));
  const root = await mkdtemp(path.join(os.tmpdir(), 'athenaeum-publishing-'));
  try {
    for (const name of ['src', 'design', 'deploy', 'astro.config.mjs', 'tsconfig.json', 'package.json'])
      await cp(path.join(project, name), path.join(root, name), { recursive: true });
    await symlink(path.join(project, 'node_modules'), path.join(root, 'node_modules'));
    await mkdir(path.join(root, 'content', 'notes'), { recursive: true });
    const build = async () => execFileSync(process.execPath, [await realpath(path.join(project, 'node_modules/.bin/astro')), 'build'], {
      cwd: root, env: { ...process.env, ASTRO_TELEMETRY_DISABLED: '1' }, stdio: 'pipe', timeout: 60_000,
    });
    await writeFile(path.join(root, 'content/notes/lesson.md'), '# Lesson\n$x^2$\n\n[Data](values.csv)');
    await writeFile(path.join(root, 'content/notes/values.csv'), 'x\n3\n');
    await writeFile(path.join(root, 'content/draft.md'), '---\ndraft: true\n---\n# DO_NOT_PUBLISH\n[Download](private.pdf)');
    await writeFile(path.join(root, 'content/private.pdf'), '%PDF-private-fixture');
    await writeFile(path.join(root, 'content/app.md'), '---\nkind: project\nhref: https://example.com/app\nrepository: https://github.com/mr-valente/bernoulli\n---\n# App');
    await build();
    const html = await readFile(path.join(root, 'dist/notes/lesson/index.html'), 'utf8');
    assert.match(html, /<math/);
    assert.match(html, /href="\/_content\/notes\/values.csv"/);
    assert.match(await readFile(path.join(root, 'dist/notes/index.html'), 'utf8'), /href="\/notes\/lesson\/"/);
    assert.equal(await readFile(path.join(root, 'dist/_content/notes/values.csv'), 'utf8'), 'x\n3\n');
    assert(!html.includes('<script'));
    const appHtml = await readFile(path.join(root, 'dist/app/index.html'), 'utf8');
    assert.match(appHtml, /href="https:\/\/github.com\/mr-valente\/bernoulli"/);
    assert(appHtml.indexOf('Open App') < appHtml.indexOf('View App on GitHub'));
    assert(!html.includes('ath-repository-link'));
    await assert.rejects(readFile(path.join(root, 'dist/_content/private.pdf')), { code: 'ENOENT' });
    await assert.rejects(readFile(path.join(root, 'dist/draft/index.html')), { code: 'ENOENT' });
    assert(!(await readFile(path.join(root, 'dist/sitemap.xml'), 'utf8')).includes('/draft/'));

    await writeFile(path.join(root, 'content/notes/lesson.md'), '---\ndraft: true\n---\n# Lesson\n[Data](values.csv)');
    await build();
    await assert.rejects(readFile(path.join(root, 'dist/notes/lesson/index.html')), { code: 'ENOENT' });
    await assert.rejects(readFile(path.join(root, 'dist/_content/notes/values.csv')), { code: 'ENOENT' });
    assert(!(await readFile(path.join(root, 'dist/index.html'), 'utf8')).includes('/notes/'));
    assert(!(await readFile(path.join(root, 'dist/sitemap.xml'), 'utf8')).includes('/notes/'));

    await rm(path.join(root, 'content'), { recursive: true });
    await mkdir(path.join(root, 'content'));
    await build();
    assert.match(await readFile(path.join(root, 'dist/index.html'), 'utf8'), /just getting started/);
    assert((await readdir(path.join(root, 'dist'))).includes('404.html'));
  } finally { await rm(root, { recursive: true, force: true }); }
});
