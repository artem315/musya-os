import { AIPanel } from "../components/AIPanel";
import { ProjectPicker, useProjects } from "../components/ProjectPicker";
import type { AppProps } from "./registry";

export default function AIApp({ win }: AppProps) {
  const { projects, projectId, setProjectId } = useProjects(win.props.projectId as string | undefined);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b p-2 text-xs" style={{ borderColor: "var(--panel-border)" }}>
        <span className="muted">Проект, в котором работает ИИ:</span>
        <ProjectPicker projects={projects} value={projectId} onChange={setProjectId} />
      </div>
      <div className="min-h-0 flex-1">{projectId && <AIPanel key={projectId} projectId={projectId} />}</div>
    </div>
  );
}
