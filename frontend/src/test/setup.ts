import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// The default 1 s is too tight when the whole suite runs in parallel on a busy
// machine; real failures still surface, just a little later.
configure({ asyncUtilTimeout: 4000 });

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
