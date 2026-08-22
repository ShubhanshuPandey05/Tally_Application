import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Where the portal is mounted, which differs by deployment:
//
//   '/'         production -- the portal has its own hostname
//               (pd-tallyflow...), so it is served from the root.
//   '/portal/'  UAT -- one hostname serves everything, and Caddy strips the
//               prefix with `handle_path /portal*`.
//
// vite bakes this into every asset URL in the bundle, so it cannot be decided
// at runtime. Get it wrong and index.html asks for /assets/index.js, which the
// site's catch-all answers with its own homepage and a 200 -- the portal
// renders as a blank page with no error anywhere.
//
// Set by the PORTAL_BASE build argument in web.Dockerfile. main.jsx derives the
// router's basename from vite's own BASE_URL rather than repeating the literal,
// so the two cannot drift apart.
const BASE = process.env.PORTAL_BASE || '/';

export default defineConfig({
  plugins: [react()],
  base: BASE,
  server: {
    port: 5174,
    // Dev talks to a local `python run.py dev`. Proxied rather than pointed at
    // http://127.0.0.1:8000 directly so the browser sees one origin here as it
    // does in production -- a portal token must never become a cross-origin
    // credential in development and same-origin in production, because then
    // the CORS behaviour is only ever exercised by the environment that does
    // not matter.
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // The log tail is a streaming response. Without this the proxy buffers
        // it and the live view shows nothing until the request ends, which for
        // an open stream is never.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (String(proxyRes.headers['content-type']).includes('text/event-stream')) {
              proxyRes.headers['x-no-compression'] = '1';
            }
          });
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
