/** @type {import('next').NextConfig} */
const nextConfig = {
  // Self-contained server output for a slim Docker image (.next/standalone).
  output: "standalone",
  // The backend is reached only through the server-side proxy (app/api/[...path]/route.ts),
  // configured at runtime with API_BASE_URL + API_KEY — nothing secret ships to the browser.
};

export default nextConfig;
