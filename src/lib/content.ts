import { readdir, readFile, lstat } from 'node:fs/promises';
import path from 'node:path';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import remarkRehype from 'remark-rehype';
import rehypeKatex from 'rehype-katex';
import rehypeSlug from 'rehype-slug';
import rehypeStringify from 'rehype-stringify';
import { visit } from 'unist-util-visit';
import { toString } from 'mdast-util-to-string';
import { parseDocument } from 'yaml';
import { z } from 'zod';
import type { Root as HtmlRoot, Element } from 'hast';
import { VFile } from 'vfile';

const metadataSchema = z.object({
  title: z.string().trim().min(1).optional(),
  description: z.string().trim().optional(),
  order: z.number().default(0),
  draft: z.boolean().default(false),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine((s) => {
    const date = new Date(s);
    return !Number.isNaN(+date) && date.toISOString().slice(0, 10) === s;
  }, 'Use a real YYYY-MM-DD date').optional(),
  tags: z.array(z.string().trim().min(1)).default([]),
  kind: z.enum(['page', 'project']).default('page'),
  href: z.string().trim().min(1).optional(),
  status: z.string().trim().optional(),
}).strict().superRefine((data, ctx) => {
  if (data.kind === 'project' && !data.href)
    ctx.addIssue({ code: 'custom', message: 'Projects require href' });
  if (data.kind !== 'project' && (data.href || data.status))
    ctx.addIssue({ code: 'custom', message: 'href/status are project-only fields' });
});

export const pageSchema = z.object({
  url: z.string(), title: z.string(), description: z.string(), html: z.string(),
  order: z.number(), tags: z.array(z.string()), kind: z.enum(['page', 'project']),
  href: z.string().optional(), status: z.string().optional(), date: z.string().optional(),
  source: z.string().optional(), generated: z.boolean(), hasHeading: z.boolean(),
});
export type Page = z.infer<typeof pageSchema>;
export type Asset = { source: string; url: string };
export type Site = { pages: Page[]; assets: Asset[] };
const allowedAssets = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.pdf', '.csv', '.txt']);
const reservedSystem = new Set(['_astro', '_content', '_health', '404', 'sitemap.xml']);
const processor = unified().use(remarkParse).use(remarkGfm).use(remarkMath)
  .use(remarkRehype).use(rehypeKatex, { output: 'htmlAndMathml', trust: false, strict: 'error' })
  .use(rehypeSlug).use(rehypeStringify);

function fail(source: string, message: string): never {
  throw new Error(`content/${source}: ${message}`);
}

function slug(segment: string): string {
  const value = segment.toLowerCase().replace(/[ _]+/g, '-');
  if (!/^[a-z0-9][a-z0-9-]*$/.test(value))
    throw new Error(`Invalid route segment "${segment}"; use letters, numbers, spaces, underscores, or hyphens`);
  return value;
}

export function routeFor(source: string): string {
  const parts = source.replace(/\.md$/i, '').split('/');
  if (parts.at(-1)?.toLowerCase() === 'index') parts.pop();
  return parts.length ? `/${parts.map(slug).join('/')}/` : '/';
}

function humanize(value: string): string {
  return value.replace(/[-_]/g, ' ').replace(/^./, (letter) => letter.toUpperCase());
}

export function parentUrl(url: string): string | undefined {
  if (url === '/') return undefined;
  return url.slice(0, url.lastIndexOf('/', url.length - 2) + 1);
}

export function sortedPages(pages: Page[]): Page[] {
  return [...pages].sort((a, b) => a.order - b.order || a.title.localeCompare(b.title, 'en') || a.url.localeCompare(b.url, 'en'));
}

async function inventory(root: string, relative = ''): Promise<string[]> {
  const entries = await readdir(path.join(root, relative), { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name, 'en'))) {
    const name = path.posix.join(relative, entry.name);
    if (entry.isSymbolicLink()) fail(name, 'Symlinks are not allowed in content');
    if (entry.name.startsWith('.')) continue;
    if (entry.isDirectory()) files.push(...await inventory(root, name));
    else if (entry.isFile()) files.push(name);
  }
  return files;
}

export async function loadSite(root: string, appPaths: string[] = []): Promise<Site> {
  const rootInfo = await lstat(root);
  if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) throw new Error('content must be a real directory');
  const reserved = new Set([...reservedSystem, ...appPaths.map(slug)]);
  const files = new Set(await inventory(root));
  const routes = new Map<string, { page: Page; tree: HtmlRoot; ids: Set<string>; draft: boolean }>();
  const bySource = new Map<string, string>();
  for (const source of [...files].filter((name) => /\.md$/i.test(name))) {
    const raw = (await readFile(path.join(root, source), 'utf8')).replace(/^\uFEFF/, '').replace(/\r\n/g, '\n');
    let body = raw;
    let metadata: unknown = {};
    if (raw.startsWith('---\n')) {
      const match = raw.match(/^---\n([\s\S]*?)^---(?:\n|$)/m);
      if (!match) fail(source, 'Unclosed YAML frontmatter');
      const document = parseDocument(match[1]);
      if (document.errors.length) fail(source, document.errors.map((e) => e.message).join('; '));
      try { metadata = document.toJS({ maxAliasCount: 20 }) ?? {}; }
      catch (error) { fail(source, String(error)); }
      body = raw.slice(match[0].length);
    }
    const result = metadataSchema.safeParse(metadata);
    if (!result.success) fail(source, result.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; '));
    const data = result.data;
    let url: string;
    try { url = routeFor(source); } catch (error) { fail(source, String(error)); }
    if (reserved.has(url.split('/')[1])) fail(source, `Route ${url} is reserved`);
    if (routes.has(url)) fail(source, `Duplicate route ${url} (also ${routes.get(url)!.page.source})`);
    const markdown = processor.parse(body);
    let firstHeading = '';
    let hasHeading = false;
    visit(markdown, 'heading', (node) => {
      firstHeading ||= toString(node);
      if (node.depth === 1) hasHeading = true;
    });
    // Raw HTML would bypass the link/asset contract. Use ordinary Markdown instead.
    visit(markdown, 'html', () => { if (!data.draft) fail(source, 'Raw HTML is not supported; use Markdown'); });
    let tree: HtmlRoot;
    const renderFile = new VFile({ path: source, value: body });
    try { tree = await processor.run(markdown, renderFile) as HtmlRoot; }
    catch (error) { fail(source, String(error)); }
    if (!data.draft && renderFile.messages.length)
      fail(source, `Invalid math or Markdown: ${renderFile.messages.map((message) => message.reason).join('; ')}`);
    const ids = new Set<string>(['main']);
    visit(tree, 'element', (node) => { if (node.properties.id) ids.add(String(node.properties.id)); });
    // rehype-katex reports invalid expressions as error markup rather than throwing.
    visit(tree, 'element', (node) => {
      if (!data.draft && Array.isArray(node.properties.className) && node.properties.className.includes('katex-error'))
        fail(source, `Invalid math: ${node.properties.title ?? 'KaTeX could not render this expression'}`);
    });
    const { draft, ...fields } = data;
    const page: Page = { ...fields, title: data.title ?? (firstHeading || humanize(url === '/' ? 'Athenaeum' : url.split('/').at(-2)!)),
      description: data.description ?? '', url, source, generated: false, hasHeading, html: '' };
    routes.set(url, { page, tree, ids, draft });
    bySource.set(source, url);
  }

  // Generate indexes only for published descendants. An explicit draft index hides its whole section.
  const isHidden = (url: string): boolean => {
    for (let current: string | undefined = url; current; current = parentUrl(current))
      if (routes.get(current)?.draft) return true;
    return false;
  };
  for (const url of [...routes.keys()]) {
    if (isHidden(url)) continue;
    for (let parent = parentUrl(url); parent; parent = parentUrl(parent)) {
      if (routes.has(parent)) continue;
      routes.set(parent, { page: { url: parent, title: parent === '/' ? 'Athenaeum' : humanize(parent.split('/').at(-2)!),
        description: '', html: '', order: 0, tags: [], kind: 'page', generated: true, hasHeading: false },
        tree: { type: 'root', children: [] }, ids: new Set(['main']), draft: false });
    }
  }
  const assets = new Map<string, Asset>();
  const resolveLink = (input: string, source: string, currentUrl: string, image = false): string => {
    if (/^(https?:|mailto:|tel:)/i.test(input)) {
      if (image && !/^https?:/i.test(input)) fail(source, `Invalid image URL: ${input}`);
      return input;
    }
    if (/^[a-z][a-z0-9+.-]*:/i.test(input) || input.startsWith('//') || /[\\\x00-\x1f]/.test(input))
      fail(source, `Unsupported URL: ${input}`);
    const match = input.match(/^([^?#]*)(\?[^#]*)?(#.*)?$/)!;
    let pathname: string;
    try { pathname = decodeURIComponent(match[1]); } catch { fail(source, `Invalid URL encoding: ${input}`); }
    if (/[\\\x00-\x1f]/.test(pathname) || pathname.startsWith('//')) fail(source, `Invalid path: ${input}`);
    const suffix = (match[2] ?? '') + (match[3] ?? '');
    const normalized = path.posix.normalize(pathname.startsWith('/') ? pathname.slice(1) : path.posix.join(path.posix.dirname(source), pathname));
    if (normalized === '..' || normalized.startsWith('../') || normalized.split('/').some((p) => p !== '.' && p.startsWith('.')))
      fail(source, `Path escapes content or references a hidden file: ${input}`);
    let target = pathname ? bySource.get(normalized) : currentUrl;
    if (!target && !image) {
      const url = pathname.startsWith('/') ? pathname : path.posix.join(currentUrl, pathname);
      const canonical = path.posix.normalize(url).replace(/\/$/, '') + '/';
      if (routes.has(canonical)) target = canonical;
      else if (appPaths.includes(canonical.split('/')[1]) && pathname.startsWith('/')) {
        const appUrl = path.posix.normalize(pathname);
        return (appUrl.split('/').length === 2 ? appUrl + '/' : appUrl) + suffix;
      }
    }
    if (target && !image) {
      if (isHidden(target)) fail(source, `Link targets a draft page or section: ${input}`);
      if (match[3]) {
        let anchor: string;
        try { anchor = decodeURIComponent(match[3].slice(1)); } catch { fail(source, `Invalid anchor: ${input}`); }
        if (anchor && !routes.get(target)?.ids.has(anchor)) fail(source, `Missing heading/anchor: ${input}`);
      }
      return target + suffix;
    }
    if (files.has(normalized) && allowedAssets.has(path.extname(normalized).toLowerCase())) {
      if (image && !/\.(png|jpe?g|gif|webp|avif)$/i.test(normalized)) fail(source, `Not a supported image: ${input}`);
      const url = '/_content/' + normalized.split('/').map(encodeURIComponent).join('/');
      assets.set(normalized, { source: normalized, url });
      return url + suffix;
    }
    fail(source, `Broken link or unsupported asset: ${input}`);
  };
  for (const [url, entry] of routes) {
    if (isHidden(url)) continue;
    const { page, tree } = entry;
    if (page.href) page.href = resolveLink(page.href, page.source!, url);
    visit(tree, 'element', (node: Element) => {
      if (node.tagName === 'a' && typeof node.properties.href === 'string')
        node.properties.href = resolveLink(node.properties.href, page.source!, url);
      if (node.tagName === 'img' && typeof node.properties.src === 'string') {
        node.properties.src = resolveLink(node.properties.src, page.source!, url, true);
        node.properties.loading = 'lazy';
        node.properties.decoding = 'async';
      }
    });
    page.html = processor.stringify(tree);
  }
  const pages = [...routes].filter(([url]) => !isHidden(url)).map(([, entry]) => entry.page);
  if (!pages.some((p) => p.url === '/')) pages.push({ url: '/', title: 'Athenaeum', description: '',
    html: '', order: 0, tags: [], kind: 'page', generated: true, hasHeading: false });
  return { pages: sortedPages(pages), assets: [...assets.values()] };
}
