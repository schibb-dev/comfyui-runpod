import { filesUrlForRelpath } from "./workProductMediaFocus";

const VIDEO_EXT = /\.(mp4|webm|mov|mkv)$/i;
const IMAGE_EXT = /\.(png|jpe?g|webp|gif)$/i;

/** Poster (and optional video) URLs for a remove-review deletion candidate. */
export function removeReviewPreviewUrls(relpath: string): { poster: string; video?: string } {
  const rel = String(relpath || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  if (!rel) return { poster: "" };
  const media = filesUrlForRelpath(rel);
  if (VIDEO_EXT.test(rel)) {
    return { poster: filesUrlForRelpath(rel.replace(VIDEO_EXT, ".png")), video: media };
  }
  if (IMAGE_EXT.test(rel)) return { poster: media };
  // Appetite keys are often extensionless stems (mp4 + companion png).
  return { poster: filesUrlForRelpath(`${rel}.png`), video: filesUrlForRelpath(`${rel}.mp4`) };
}
