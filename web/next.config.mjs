/** @type {import('next').NextConfig} */
const nextConfig = {
  // Self-contained server output for a slim Docker image (.next/standalone).
  output: "standalone",
  // The backend API base is read client-side from NEXT_PUBLIC_API_BASE (see .env.example).
};

export default nextConfig;
