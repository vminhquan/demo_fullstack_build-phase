import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // The default bottom-left badge covers the logout button at the bottom of the navigation rail.
  devIndicators: { position: "bottom-right" },
};

export default nextConfig;
