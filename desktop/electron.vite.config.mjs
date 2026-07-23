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
        entry: resolve('src/preload/index.cjs'),
        formats: ['cjs'],
      },
      rollupOptions: {
        external: ['electron'],
      },
    },
  },
  renderer: {},
});
