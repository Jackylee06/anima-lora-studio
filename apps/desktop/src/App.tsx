import type { Project, WorkerEvent } from "@anima/contracts";
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, LoaderCircle, X } from "lucide-react";
import { Sidebar, type Page } from "./components/Sidebar";
import { Welcome } from "./components/Welcome";
import { CaptionsPage } from "./pages/Captions";
import { Dashboard } from "./pages/Dashboard";
import { Gallery } from "./pages/Gallery";
import { SettingsPage } from "./pages/Settings";
import { TrainingPage } from "./pages/Training";
import { errorMessage, onWorkerEvent, rpc } from "./lib/api";

interface JobNotice { id: string; kind: string; state: string; progressCurrent: number; progressTotal: number; message: string; error?: string }

export function App() {
  const [project, setProject] = useState<Project | null | undefined>(undefined);
  const [page, setPage] = useState<Page>("dashboard");
  const [jobs, setJobs] = useState<JobNotice[]>([]);
  const [startupError, setStartupError] = useState<string | null>(null);

  useEffect(() => {
    void rpc<Project | null>("project.current").then(setProject).catch((error) => { setStartupError(errorMessage(error)); setProject(null); });
    return onWorkerEvent((event: WorkerEvent) => {
      if (event.event === "job.updated") {
        const job = event.data as JobNotice;
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)].slice(0, 5));
      }
    });
  }, []);

  if (project === undefined) return <div className="app-loading"><LoaderCircle className="spin" /><span>正在启动本地 worker…</span></div>;
  if (!project) return <><Welcome onOpen={(opened, created) => { setProject(opened); setPage(created ? "settings" : "dashboard"); }} />{startupError && <div className="startup-error">{startupError}</div>}</>;

  return <div className="app-shell">
    <Sidebar project={project} page={page} onPage={setPage} />
    <main className="main-shell">
      {page === "dashboard" && <Dashboard project={project} onPage={setPage} />}
      {page === "gallery" && <Gallery />}
      {page === "captions" && <CaptionsPage project={project} />}
      {page === "training" && <TrainingPage project={project} />}
      {page === "settings" && <SettingsPage project={project} onProject={setProject} />}
    </main>
    <JobDock jobs={jobs} onDismiss={(id) => setJobs((current) => current.filter((job) => job.id !== id))} />
  </div>;
}

function JobDock({ jobs, onDismiss }: { jobs: JobNotice[]; onDismiss: (id: string) => void }) {
  const visible = jobs.filter((job) => ["queued", "running", "pause_requested", "succeeded", "failed"].includes(job.state));
  if (!visible.length) return null;
  return <div className="job-dock">{visible.map((job) => <div className={`job-toast ${job.state}`} key={job.id}>
    <div className="toast-icon">{job.state === "failed" ? <AlertTriangle /> : job.state === "succeeded" ? <CheckCircle2 /> : <LoaderCircle className="spin" />}</div>
    <div><strong>{job.kind}</strong><p>{job.error || job.message || (job.state === "queued" ? "等待执行" : "正在运行")}</p>{job.progressTotal > 0 && <div className="mini-progress"><span style={{ width: `${Math.min(100, job.progressCurrent / job.progressTotal * 100)}%` }} /></div>}</div>
    {["succeeded", "failed"].includes(job.state) && <button onClick={() => onDismiss(job.id)}><X size={14} /></button>}
  </div>)}</div>;
}
