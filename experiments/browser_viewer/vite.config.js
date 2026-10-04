import { defineConfig } from 'vite';

// Keep the adapter's static allowlist small: one JS entry and one stylesheet.
export default defineConfig({
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    lib: { entry: 'workspace.jsx', formats: ['es'], fileName: () => 'workspace.js', cssFileName: 'workspace' },
    rolldownOptions: { output: { codeSplitting: false } },
  },
});
