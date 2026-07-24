import { defineConfig } from 'electron-vite';
import { resolve } from 'node:path';

export default defineConfig({
  main: {
    build: {
      lib: {
        entry: resolve('src/main/index.cjs'),
        formats: ['cjs'],
      },
      rollupOptions: {
        external: ['electron'],
      },
    },
  },
  preload: {
    build: {
      lib: {
        entry: {
          index: resolve('src/preload/index.cjs'),
          'browser-page-security': resolve('src/preload/browser-page-security.cjs'),
        },
        formats: ['cjs'],
      },
      rollupOptions: {
        external: ['electron'],
      },
    },
  },
  renderer: {},
});
