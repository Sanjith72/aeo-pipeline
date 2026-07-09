/** @type {import('next').NextConfig} */
const nextConfig = {
  // Self-contained server output for a slim Docker image (.next/standalone).
  output: "standalone",
  // The backend is reached only through the server-side proxy (app/api/[...path]/route.ts),
  // configured at runtime with API_BASE_URL + API_KEY — nothing secret ships to the browser.
  async redirects() {
    return [
      // Legacy deep links minted before the /studio split arrived as
      // /?domain=…&name=…&autobuild=1#studio. The marketing page no longer reads those
      // params, so forward them to /studio (unused query params pass through automatically).
      {
        source: "/",
        has: [{ type: "query", key: "autobuild", value: "1" }],
        destination: "/studio",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
