import { defineConfig } from 'astro/config';
import publishedAssets from './src/lib/assets.ts';

export default defineConfig({
  site: 'https://valentemath.com',
  output: 'static',
  trailingSlash: 'always',
  integrations: [publishedAssets()],
  build: {
    format: 'directory',
    inlineStylesheets: 'never',
  },
  vite: {
    build: {
      // Keep scripts and fonts external to comply with the static site's CSP.
      assetsInlineLimit: (filePath) => /\.(?:js|ts|woff2?|ttf|otf)$/.test(filePath) ? false : undefined,
    },
  },
  devToolbar: {
    enabled: false,
  },
});
