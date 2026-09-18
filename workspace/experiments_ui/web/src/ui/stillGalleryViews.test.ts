import { describe, expect, it } from "vitest";
import { GRID_EAGER_THUMBS, stillFocusMediaLoadMode } from "./stillGalleryViews";

describe("stillFocusMediaLoadMode", () => {
  it("eager-loads the focused still and neighbors", () => {
    expect(stillFocusMediaLoadMode(4, 4)).toBe("eager");
    expect(stillFocusMediaLoadMode(3, 4)).toBe("eager");
    expect(stillFocusMediaLoadMode(5, 4)).toBe("eager");
  });

  it("lazily prefetches two away and skips the rest", () => {
    expect(stillFocusMediaLoadMode(2, 4)).toBe("lazy");
    expect(stillFocusMediaLoadMode(6, 4)).toBe("lazy");
    expect(stillFocusMediaLoadMode(0, 4)).toBe("off");
    expect(stillFocusMediaLoadMode(20, 4)).toBe("off");
  });

  it("treats a missing focus as the first still", () => {
    expect(stillFocusMediaLoadMode(0, -1)).toBe("eager");
    expect(stillFocusMediaLoadMode(1, -1)).toBe("eager");
    expect(stillFocusMediaLoadMode(2, -1)).toBe("lazy");
    expect(stillFocusMediaLoadMode(3, -1)).toBe("off");
  });
});

describe("GRID_EAGER_THUMBS", () => {
  it("covers about a first phone viewport of tiles", () => {
    expect(GRID_EAGER_THUMBS).toBe(12);
  });
});
