/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The dashboard renders nothing without an authenticated API call, so there
  // is no static content worth pre-rendering and no server-side data fetching
  // that would need the API to be reachable at build time.
  poweredByHeader: false,

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            // `microphone=(self)` allows this origin and nothing else. An empty
            // allowlist, `microphone=()`, disables the feature for the page
            // itself, which silently overrides any permission the user grants
            // in the browser: getUserMedia fails with NotAllowedError and the
            // Permissions API reports "denied" no matter what the site settings
            // say. The browser test call needs the microphone, so it is allowed
            // for self only; geolocation and camera stay fully disabled.
            key: "Permissions-Policy",
            value: "geolocation=(), microphone=(self), camera=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
