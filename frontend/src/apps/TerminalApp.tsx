import { ProjectPicker, useProjects } from "../components/ProjectPicker";
import { TerminalView } from "../components/TerminalView";
import type { AppProps } from "./registry";

export default function TerminalApp({ win }: AppProps) {
  const { projects, projectId, setProjectId } = useProjects(win.props.projectId as string | undefined);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b p-1.5 text-xs" style={{ borderColor: "var(--panel-border)" }}>
        <span className="muted">Рабочая папка:</span>
        <ProjectPicker projects={projects} value={projectId} onChange={setProjectId} />
        <span className="ml-auto muted hidden sm:inline">Это MUSYA REPL, а не системный shell. Linux-shell — в MUR PC.</span>
      </div>
      <div className="min-h-0 flex-1">{projectId && <TerminalView key={projectId} projectId={projectId} />}</div>
    </div>
  );
}
