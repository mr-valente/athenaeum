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
  devToolbar: {
    enabled: false,
  },
});
