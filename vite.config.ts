import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// N-01: ブラウザは public/data の静的ファイルしか読まない。
// 外部ホストへの fetch は出荷物に含めない(G-11 が走査する)。
export default defineConfig({
  plugins: [react()],
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 1200,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
