import { sveltekit } from '@sveltejs/kit/vite';

// node node_modules/vite/bin/vite.js --config tests/phone-preview.config.mjs --port 4178
// Open /tests/phone-preview.html. No camera or call service is used.
export default {
  plugins: [{
    name: 'mock-phone-call',
    enforce: 'pre',
    load(id) {
      const file = id.replaceAll('\\', '/');
      if (file.endsWith('/src/lib/stdb.ts') || file.endsWith('/src/lib/callRuntime.ts')) {
        return "export * from '/tests/phoneMocks.ts';";
      }
    }
  }, sveltekit()]
};
