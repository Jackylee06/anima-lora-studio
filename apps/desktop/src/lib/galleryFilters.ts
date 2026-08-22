import type { Asset, AssetQuery } from "@anima/contracts";

export type AgeFilter = "" | "All Ages" | "R-18" | "R-18G";
export type AiFilter = "" | "AI" | "non_ai";

export const AGE_FILTER_OPTIONS: Array<{ value: AgeFilter; label: string }> = [
  { value: "", label: "全部年龄" },
  { value: "All Ages", label: "全年龄" },
  { value: "R-18", label: "R-18" },
  { value: "R-18G", label: "R-18G" },
];

export const AI_FILTER_OPTIONS: Array<{ value: AiFilter; label: string }> = [
  { value: "", label: "全部来源" },
  { value: "AI", label: "AI 生成" },
  { value: "non_ai", label: "非 AI" },
];

export function galleryMetadataQuery(age: AgeFilter, ai: AiFilter): Pick<AssetQuery, "age" | "ai"> {
  return {
    age: age || undefined,
    ai: ai || undefined,
  };
}

export function assetClassification(metadata: Asset["metadata"]): {
  ageLabel: string;
  ageClass: "all-ages" | "r18" | "r18g" | "unknown";
  aiLabel: "AI 生成" | "非 AI";
  aiClass: "generated" | "not-ai";
} {
  const age = String(metadata.age || "");
  const ageInfo = age === "All Ages"
    ? { ageLabel: "全年龄", ageClass: "all-ages" as const }
    : age === "R-18"
      ? { ageLabel: "R-18", ageClass: "r18" as const }
      : age === "R-18G"
        ? { ageLabel: "R-18G", ageClass: "r18g" as const }
        : { ageLabel: "年龄未知", ageClass: "unknown" as const };
  const generated = metadata.AI === "AI";
  return {
    ...ageInfo,
    aiLabel: generated ? "AI 生成" : "非 AI",
    aiClass: generated ? "generated" : "not-ai",
  };
}
