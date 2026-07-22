import { describe, expect, it } from "vitest";
import { displayToOriginal, originalToDisplay } from "./region-coordinates";

describe("question region coordinates", () => {
  it.each([0, 90, 180, 270] as const)(
    "round trips at %s degrees",
    (rotation) => {
      const source = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 };
      const result = displayToOriginal(
        originalToDisplay(source, rotation),
        rotation,
      );
      expect(result.x).toBeCloseTo(source.x);
      expect(result.y).toBeCloseTo(source.y);
      expect(result.width).toBeCloseTo(source.width);
      expect(result.height).toBeCloseTo(source.height);
    },
  );
});
