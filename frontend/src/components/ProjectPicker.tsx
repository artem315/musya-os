import { useEffect, useState } from "react";
import { api, type Project } from "../lib/api";

/** Small project selector used by MUR AI and MUR Terminal windows. */
export function useProjects(initial?: string) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(initial ?? null);
  useEffect(() => {
    api.get<{ projects: Project[] }>("/api/projects").then((r) => {
      setProjects(r.projects);
      setProjectId((cur) => cur ?? r.projects[0]?.id ?? null);
    });
  }, []);
  return { projects, projectId, setProjectId };
}

export function ProjectPicker({ projects, value, onChange }: { projects: Project[]; value: string | null; onChange: (id: string) => void }) {
  return (
    <select className="input !w-auto !py-1 !text-xs" value={value ?? ""} onChange={(e) => onChange(e.target.value)} aria-label="Проект">
      {projects.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  );
}
