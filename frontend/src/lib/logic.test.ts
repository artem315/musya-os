import { describe, expect, it } from "vitest";
import { move2048 } from "../apps/gaming/games";
import { formatBytes } from "./api";
import { KEYWORDS, SERVICE_VERBS } from "./mysyaLang";

describe("2048 rules", () => {
  it("merges equal tiles once per move", () => {
    const [g, gained, moved] = move2048(
      [
        [2, 2, 2, 2],
        [0, 0, 0, 0],
        [4, 0, 4, 8],
        [0, 0, 0, 0],
      ],
      "left",
    );
    expect(g[0]).toEqual([4, 4, 0, 0]);
    expect(g[2]).toEqual([8, 8, 0, 0]);
    expect(gained).toBe(16);
    expect(moved).toBe(true);
  });
  it("reports no move when nothing changes", () => {
    const grid = [
      [2, 4, 2, 4],
      [4, 2, 4, 2],
      [2, 4, 2, 4],
      [4, 2, 4, 2],
    ];
    for (const d of ["up", "down", "left", "right"] as const) expect(move2048(grid, d)[2]).toBe(false);
  });
});

describe("helpers", () => {
  it("formats bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
  it("editor knows the MUSYA language", () => {
    expect(KEYWORDS).toContain("service");
    expect(SERVICE_VERBS).toEqual(expect.arrayContaining(["load", "search", "click", "type", "screenshot", "download"]));
  });
});
