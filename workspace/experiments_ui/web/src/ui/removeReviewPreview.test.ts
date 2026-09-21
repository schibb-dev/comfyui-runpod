import { describe, expect, it } from "vitest";
import { removeReviewPreviewUrls } from "./removeReviewPreview";

describe("removeReviewPreviewUrls", () => {
  it("uses the still as the poster", () => {
    expect(removeReviewPreviewUrls("og/2026-09-21/clip.jpg")).toEqual({
      poster: "/files/og%2F2026-09-21%2Fclip.jpg",
    });
  });

  it("pairs an mp4 with a sibling png poster", () => {
    expect(removeReviewPreviewUrls("og/2026-09-21/clip.mp4")).toEqual({
      poster: "/files/og%2F2026-09-21%2Fclip.png",
      video: "/files/og%2F2026-09-21%2Fclip.mp4",
    });
  });

  it("treats an extensionless stem as png poster + mp4", () => {
    expect(removeReviewPreviewUrls("og/2026-09-21/clip")).toEqual({
      poster: "/files/og%2F2026-09-21%2Fclip.png",
      video: "/files/og%2F2026-09-21%2Fclip.mp4",
    });
  });
});
