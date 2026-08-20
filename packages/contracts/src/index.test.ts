import { describe, expect, it } from "vitest";
import { defaultTrainingConfig, RPC_VERSION } from "./index";

describe("contracts", () => {
  it("keeps the rpc version stable", () => {
    expect(RPC_VERSION).toBe(1);
  });

  it("clamps character and style presets", () => {
    expect(defaultTrainingConfig("character", 1).maxTrainSteps).toBe(600);
    expect(defaultTrainingConfig("character", 1000).maxTrainSteps).toBe(1600);
    expect(defaultTrainingConfig("style", 1).maxTrainSteps).toBe(1200);
    expect(defaultTrainingConfig("style", 1000).maxTrainSteps).toBe(3000);
  });
});

