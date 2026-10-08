import { defineConfig } from 'vite';
import pkg from './node_modules/flexlayout-react/package.json' with { type: 'json' };

// PR #530 ships source. Build its runtime with the viewer's existing toolchain,
// including after CI's npm ci --ignore-scripts install.
export default defineConfig({
  define: { __VERSION__: JSON.stringify(pkg.version) },
  oxc: { jsx: { runtime: 'automatic' } },
  build: {
    lib: {
      entry: 'node_modules/flexlayout-react/src/index.ts',
      formats: ['es'],
      fileName: () => 'index.js',
    },
    outDir: 'node_modules/flexlayout-react/dist',
    emptyOutDir: true,
    rolldownOptions: { external: /^react(?:-dom)?(?:\/|$)/ },
  },
});
