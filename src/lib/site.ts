import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { z } from 'zod';
import { loadSite } from './content.ts';

// Resolve from the project root, including when Vite transforms the loader.
export const contentRoot = fileURLToPath(new URL('../../content/', import.meta.url));
export const reservedFile = fileURLToPath(new URL('../../deploy/reserved-paths.json', import.meta.url));

export async function readSite() {
  const reserved = z.array(z.string().regex(/^[a-z0-9][a-z0-9-]*$/)).parse(JSON.parse(await readFile(reservedFile, 'utf8')));
  return loadSite(contentRoot, reserved);
}
