import { defineCollection } from 'astro:content';
import type { Loader } from 'astro/loaders';
import { pageSchema } from './lib/content.ts';
import { readSite, contentRoot, reservedFile } from './lib/site.ts';

const loader: Loader = {
  name: 'athenaeum-directory',
  async load({ store, parseData, watcher, logger }) {
    const refresh = async () => {
      const site = await readSite();
      const entries = await Promise.all(site.pages.map(async (page) => ({
        id: page.url, data: await parseData({ id: page.url, data: page }),
      })));
      store.clear();
      for (const entry of entries) store.set(entry);
    };
    await refresh();
    if (watcher) {
      watcher.add([contentRoot, reservedFile]);
      let pending = Promise.resolve();
      watcher.on('all', (_event, file) => {
        if (file !== reservedFile && !file.startsWith(contentRoot)) return;
        pending = pending.then(refresh).catch((error) => {
          store.clear();
          logger.error(String(error));
        });
      });
    }
  },
};

export const collections = { pages: defineCollection({ loader, schema: pageSchema }) };
