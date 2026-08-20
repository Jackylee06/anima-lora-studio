export interface JobLike {
  id: string;
  progressCurrent: number;
  progressTotal: number;
}

export function progressPercent(job: JobLike): number {
  if (!Number.isFinite(job.progressCurrent) || !Number.isFinite(job.progressTotal) || job.progressTotal <= 0) return 0;
  return Math.max(0, Math.min(100, job.progressCurrent / job.progressTotal * 100));
}

export function upsertNewest<T extends { id: string }>(items: T[], item: T, limit = 50): T[] {
  return [item, ...items.filter((candidate) => candidate.id !== item.id)].slice(0, limit);
}
