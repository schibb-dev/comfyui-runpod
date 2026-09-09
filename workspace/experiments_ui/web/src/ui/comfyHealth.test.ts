import { describe, expect, it } from "vitest";
import {
  comfyHealthIsBackoff,
  comfyHealthRetryRemaining,
  comfyHealthSummary,
  formatComfyRetry,
} from "./comfyHealth";

describe("comfyHealth", () => {
  it("treats backoff or ok=false as the paused state", () => {
    expect(comfyHealthIsBackoff(undefined)).toBe(false);
    expect(comfyHealthIsBackoff({ status: "ok", ok: true })).toBe(false);
    expect(comfyHealthIsBackoff({ status: "backoff", ok: false })).toBe(true);
    expect(comfyHealthIsBackoff({ ok: false })).toBe(true);
  });

  it("formats retry waits", () => {
    expect(formatComfyRetry(0)).toBe("now");
    expect(formatComfyRetry(12)).toBe("12s");
    expect(formatComfyRetry(60)).toBe("1m");
    expect(formatComfyRetry(75)).toBe("1m 15s");
  });

  it("counts down from next_probe_at", () => {
    const now = Date.parse("2026-09-09T12:00:00.000Z");
    expect(
      comfyHealthRetryRemaining(
        { status: "backoff", ok: false, next_probe_at: "2026-09-09T12:01:10Z", retry_in_sec: 99 },
        now,
      ),
    ).toBe(70);
    expect(comfyHealthSummary({ status: "backoff", ok: false }, 70)).toContain("1m 10s");
    expect(comfyHealthSummary({ status: "backoff", ok: false }, 0)).toContain("next drain health check");
  });
});
