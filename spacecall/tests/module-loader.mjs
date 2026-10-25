import { registerHooks } from 'node:module';
import { spacetimedbModuleTestPlugin } from 'spacetimedb/server/test-utils/vitest';

// Reuse the SDK's official host stubs with Node's test runner, without Vitest.
const plugin = spacetimedbModuleTestPlugin();
const stub = plugin.load('\0spacetimedb-module-test-sys');
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('spacetime:sys@2.')) {
      return { url: 'spacetime-test:sys', format: 'module', shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url === 'spacetime-test:sys') {
      return { source: stub, format: 'module', shortCircuit: true };
    }
    return nextLoad(url, context);
  }
});
