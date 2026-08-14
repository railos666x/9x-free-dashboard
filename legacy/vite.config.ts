import { defineConfig } from 'vite';

export default defineConfig({
  root: '.',
  publicDir: 'public',
  build: {
    outDir: 'dist',
    sourcemap: true,
    target: 'es2022',
    minify: 'esbuild',
  },
  server: {
    port: 3000,
    host: true,
  },
  optimizeDeps: {
    include: [],
  },
});