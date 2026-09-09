import type { AstroIntegration } from 'astro';
import { copyFile, mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { contentRoot, readSite } from './site.ts';

const mime: Record<string, string> = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.avif': 'image/avif', '.pdf': 'application/pdf',
  '.csv': 'text/csv; charset=utf-8', '.txt': 'text/plain; charset=utf-8',
};

export default function publishedAssets(): AstroIntegration {
  return {
    name: 'athenaeum-published-assets',
    hooks: {
      'astro:build:done': async ({ dir }) => {
        const { assets } = await readSite();
        for (const asset of assets) {
          const target = path.join(fileURLToPath(dir), '_content', asset.source);
          await mkdir(path.dirname(target), { recursive: true });
          await copyFile(path.join(contentRoot, asset.source), target);
        }
      },
      'astro:server:setup': ({ server }) => {
        server.middlewares.use(async (request, response, next) => {
          if (!request.url?.startsWith('/_content/')) return next();
          try {
            const site = await readSite();
            const url = request.url.split(/[?#]/)[0];
            const asset = site.assets.find((item) => item.url === url);
            if (!asset) { response.statusCode = 404; response.end('Not found'); return; }
            response.setHeader('Content-Type', mime[path.extname(asset.source).toLowerCase()]);
            response.setHeader('X-Content-Type-Options', 'nosniff');
            response.end(await readFile(path.join(contentRoot, asset.source)));
          } catch {
            response.statusCode = 503;
            response.end('Content validation failed; see the development log.');
          }
        });
      },
    },
  };
}
