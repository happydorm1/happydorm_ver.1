import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 개발/미리보기 서버는 /api 요청을 FastAPI 백엔드로 넘긴다(같은 출처로 동작 → CORS 설정 불필요).
// 운영에서는 웹서버(nginx 등)가 같은 경로로 백엔드에 넘기도록 구성한다.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": backend } },
  preview: { proxy: { "/api": backend } },
});
