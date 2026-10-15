import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PageErrorBoundary } from "../PageErrorBoundary";

function Boom(): never {
  throw new Error("chunk failed to load");
}

describe("PageErrorBoundary", () => {
  it("shows a way out instead of a blank page", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <PageErrorBoundary resetKey="/e/abc">
        <Boom />
      </PageErrorBoundary>,
    );
    expect(screen.getByRole("heading", { name: "Couldn't load this page" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });

  it("gives the next page a fresh chance after navigation", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender } = render(
      <PageErrorBoundary resetKey="/a">
        <Boom />
      </PageErrorBoundary>,
    );
    rerender(
      <PageErrorBoundary resetKey="/b">
        <p>Next page</p>
      </PageErrorBoundary>,
    );
    expect(screen.getByText("Next page")).toBeInTheDocument();
  });
});
