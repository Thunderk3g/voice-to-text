/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output is consumed by the Docker (Linux) runner stage.
  // On Windows dev machines it is skipped: writing .next/standalone requires
  // creating symlinks (pnpm layout), which Windows forbids without
  // Developer Mode / admin rights, and the local dev flow never uses it.
  ...(process.platform !== "win32" ? { output: "standalone" } : {}),
  reactStrictMode: true,
  async rewrites() {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL || "http://api:8080";
    return [
      {
        source: "/api/v2t/:path*",
        destination: `${base}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
