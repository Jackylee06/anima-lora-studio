import { describe, expect, it } from "vitest";
import { assetClassification, galleryMetadataQuery } from "./galleryFilters";

describe("gallery metadata filters", () => {
  it("omits inactive filters and maps active age/AI filters to the asset query", () => {
    expect(galleryMetadataQuery("", "")).toEqual({ age: undefined, ai: undefined });
    expect(galleryMetadataQuery("R-18", "AI")).toEqual({ age: "R-18", ai: "AI" });
    expect(galleryMetadataQuery("All Ages", "non_ai")).toEqual({ age: "All Ages", ai: "non_ai" });
  });

  it("builds explicit age and AI marks from Pixiv metadata", () => {
    expect(assetClassification({ age: "R-18G", AI: "AI" })).toEqual({
      ageLabel: "R-18G", ageClass: "r18g", aiLabel: "AI 生成", aiClass: "generated",
    });
    expect(assetClassification({ age: null, AI: null })).toEqual({
      ageLabel: "年龄未知", ageClass: "unknown", aiLabel: "非 AI", aiClass: "not-ai",
    });
  });
});
