import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Tests run against TypeScript source; the alias avoids requiring a build of
// the workspace dependency before `pnpm test`.
export default defineConfig({
  resolve: {
    alias: {
      '@genai-sdk/langfuse-client': fileURLToPath(
        new URL('./packages/langfuse-client/src/index.ts', import.meta.url),
      ),
    },
  },
  test: {
    include: ['packages/*/test/**/*.test.ts'],
    environment: 'node',
  },
});
