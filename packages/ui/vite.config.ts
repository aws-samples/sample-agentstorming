// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // Loopback only. "0.0.0.0" put the dev server on every network the
    // laptop joined; pass --host to choose that deliberately.
    host: "127.0.0.1",
    port: 5173,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
