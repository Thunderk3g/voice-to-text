/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  async rewrites() {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL || "http://api:8000";
    return [
      {
        source: "/api/v2t/:path*",
        destination: `${base}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
