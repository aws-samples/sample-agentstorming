#!/usr/bin/env node
// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { runMCP } from "./server.js";
runMCP().catch((e: unknown) => {
  console.error(e);
  process.exit(1);
});
