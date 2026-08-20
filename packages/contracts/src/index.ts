export const RPC_VERSION = 1 as const;

export type ProjectProfile = "character" | "style" | "custom";
export type ReviewState = "pending" | "kept" | "rejected";
export type JobState =
  | "queued"
  | "running"
  | "pause_requested"
  | "paused"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface Project {
  id: string;
  name: string;
  workspacePath: string;
  sourceRoot: string;
  pathTemplate: string;
  profile: ProjectProfile;
  trigger: string;
  createdAt: string;
  updatedAt: string;
  settings: Record<string, unknown>;
}

export interface AssetMetrics {
  width: number | null;
  height: number | null;
  aspectRatio: number | null;
  blurScore: number | null;
  technicalScore: number | null;
  perceptualHash: string | null;
  duplicateGroup: string | null;
  semanticGroup: string | null;
  semanticSimilarity: number | null;
}

export interface Asset {
  id: string;
  sourcePath: string;
  relativePath: string;
  thumbnailPath: string | null;
  kind: "image" | "animated" | "novel" | "unsupported";
  eligible: boolean;
  exclusionReason: string | null;
  reviewState: ReviewState;
  metadata: Record<string, string | number | string[] | null>;
  metrics: AssetMetrics;
  latestCaption: string | null;
  captionStatus: "missing" | "draft" | "needs_review" | "approved";
  createdAt: string;
  updatedAt: string;
}

export interface CaptionSections {
  quality_meta_year_safety: string[];
  subject_count: string[];
  characters: string[];
  series: string[];
  artists: string[];
  general: string[];
  natural_language?: string | null;
  warnings?: string[];
}

export interface CaptionRevision {
  id: string;
  assetId: string;
  revision: number;
  profile: ProjectProfile;
  sources: Record<string, unknown>;
  sections: CaptionSections;
  finalText: string;
  status: "draft" | "needs_review" | "approved";
  createdAt: string;
}

export interface PipelineRun {
  id: string;
  stage: "scan" | "wd14" | "joycaption" | "llm_refine" | "export";
  state: JobState;
  progressCurrent: number;
  progressTotal: number;
  message: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface TrainingRun {
  id: string;
  state: JobState;
  exportPath: string;
  outputPath: string;
  config: TrainingConfig;
  currentStep: number;
  totalSteps: number;
  latestLoss: number | null;
  latestCheckpoint: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface TrainingConfig {
  profile: ProjectProfile;
  networkDim: number;
  networkAlpha: number;
  learningRate: number;
  batchSize: number;
  gradientAccumulationSteps: number;
  maxTrainSteps: number;
  minBucketResolution: number;
  maxBucketResolution: number;
  bucketResolutionSteps: number;
  saveEverySteps: number;
  sampleEverySteps: number;
  seed: number;
  advancedArgs: Record<string, string | number | boolean>;
}

export interface ModelEntry {
  id: string;
  kind: "anima_base" | "anima_inference" | "qwen3" | "vae" | "wd14" | "joycaption" | "trainer";
  name: string;
  source: string;
  localPath: string | null;
  sha256: string | null;
  status: "missing" | "downloading" | "ready" | "invalid";
  metadata: Record<string, unknown>;
}

export type AppUpdateStatus =
  | "disabled"
  | "idle"
  | "checking"
  | "available"
  | "ignored"
  | "downloading"
  | "downloaded"
  | "not-available"
  | "error";

export interface AppUpdateState {
  currentVersion: string;
  status: AppUpdateStatus;
  availableVersion?: string;
  releaseName?: string;
  releaseNotes?: string;
  releaseDate?: string;
  progress?: number;
  bytesPerSecond?: number;
  transferred?: number;
  total?: number;
  error?: string;
  checkedAt?: string;
  ignoredVersion?: string;
}

export interface RpcRequest<T = unknown> {
  v: typeof RPC_VERSION;
  id: string;
  method: string;
  params: T;
}

export interface RpcSuccess<T = unknown> {
  v: typeof RPC_VERSION;
  id: string;
  ok: true;
  result: T;
}

export interface RpcFailure {
  v: typeof RPC_VERSION;
  id: string;
  ok: false;
  error: { code: string; message: string; details?: unknown };
}

export type RpcResponse<T = unknown> = RpcSuccess<T> | RpcFailure;

export interface WorkerEvent<T = unknown> {
  v: typeof RPC_VERSION;
  event: string;
  data: T;
}

export interface AssetQuery {
  reviewState?: ReviewState | "all";
  eligible?: boolean;
  search?: string;
  age?: string;
  ai?: string;
  offset?: number;
  limit?: number;
  sort?: "path" | "score_desc" | "updated_desc";
}

export interface AssetPage {
  items: Asset[];
  total: number;
  counts: Record<ReviewState, number>;
}

export function defaultTrainingConfig(profile: ProjectProfile, imageCount: number): TrainingConfig {
  const effectiveBatch = 4;
  const rawSteps = profile === "style"
    ? Math.ceil((imageCount * 50) / effectiveBatch)
    : Math.ceil((imageCount * 100) / effectiveBatch);
  const min = profile === "style" ? 1200 : 600;
  const max = profile === "style" ? 3000 : 1600;
  const maxTrainSteps = Math.max(min, Math.min(max, rawSteps));
  const checkpoint = Math.max(100, Math.round(maxTrainSteps / 5 / 50) * 50);
  return {
    profile,
    networkDim: 32,
    networkAlpha: 32,
    learningRate: 2e-5,
    batchSize: 1,
    gradientAccumulationSteps: 4,
    maxTrainSteps,
    minBucketResolution: 512,
    maxBucketResolution: 1024,
    bucketResolutionSteps: 64,
    saveEverySteps: checkpoint,
    sampleEverySteps: checkpoint,
    seed: 42,
    advancedArgs: {}
  };
}
