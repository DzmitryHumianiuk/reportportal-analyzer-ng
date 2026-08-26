import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Relative base: dist asset URLs stay relative so the backend's <base href> tag
// (injected in place of the <!--BASE--> comment) decides the deploy prefix.
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:5005',
      '/healthz': 'http://127.0.0.1:5005',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
  },
});
