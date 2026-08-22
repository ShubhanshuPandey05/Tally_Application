import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In production Caddy serves this site and the API from one hostname, so
    // the hero's stats request is same-origin. Proxying in development keeps it
    // that way rather than making the dev build the only one that needs CORS.
    proxy: {
      '/v1': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
