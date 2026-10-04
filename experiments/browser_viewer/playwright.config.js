import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  use: {
    baseURL: 'http://127.0.0.1:8767',
    viewport: { width: 1440, height: 1000 },
    headless: true,
    channel: process.env.BROWSER_CHANNEL || undefined,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: '../../.venv/bin/python -m experiments.nozzle_browser --port 8767 --example ../../examples/nozzle-bayonette-simplified --recipe ../../examples/nozzle-bayonette-simplified/recipes/cone-plane.json',
    env: { PYTHONPATH: '../..:../../src', OPENBLAS_NUM_THREADS: '2' },
    url: 'http://127.0.0.1:8767',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
