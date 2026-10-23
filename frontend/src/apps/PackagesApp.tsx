import { Download, Play, ShieldAlert, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ProjectPicker, useProjects } from "../components/ProjectPicker";
import { useOS } from "../desktop/os";
import { api, ApiError } from "../lib/api";

type Pkg = { id: string; name: string; version: string; kind: string; description: string; author: string; permissions: string[]; sensitive_permissions: string[]; valid: boolean; errors?: string[] };
type Installed = { id: string; name: string; version: string; kind: string; permissions: string[]; source: string };

export default function PackagesApp() {
  const os = useOS();
  const [catalog, setCatalog] = useState<Pkg[]>([]);
  const [installed, setInstalled] = useState<Installed[]>([]);
  const [confirm, setConfirm] = useState<{ id: string; name: string; permissions: string[]; needs: string[]; fromProject?: string } | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const { projects, projectId, setProjectId } = useProjects();

  const load = useCallback(() => api.get<{ catalog: Pkg[]; installed: Installed[] }>("/api/packages").then((r) => (setCatalog(r.catalog), setInstalled(r.installed))), []);
  useEffect(() => {
    load();
  }, [load]);

  const install = async (id: string, name: string, approved: string[] = [], fromProject?: string) => {
    try {
      if (fromProject) await api.post("/api/packages/install-from-project", { project_id: fromProject, approved_permissions: approved });
      else await api.post(`/api/packages/${id}/install`, { approved_permissions: approved });
      setConfirm(null);
      os.notify("Установлено", name, "ok");
      load();
    } catch (e) {
      const err = e as ApiError;
      const d = err.data as { needs_approval?: string[]; permissions?: string[] } | undefined;
      if (err.status === 403 && d?.needs_approval) {
        setChecked([]);
        setConfirm({ id, name, permissions: d.permissions ?? [], needs: d.needs_approval, fromProject });
      } else os.notify("Ошибка установки", err.message, "error");
    }
  };

  const runPkg = async (id: string) => {
    try {
      await api.post(`/api/packages/${id}/run`);
      os.notify("Пакет запущен", `${id}: вывод в уведомлениях и Logs Studio`, "info");
    } catch (e) {
      os.notify("Не удалось запустить", (e as Error).message, "error");
    }
  };

  return (
    <div className="h-full overflow-auto p-3">
      <h3 className="mb-2 font-semibold">Каталог драйверов и пакетов</h3>
      <div className="grid gap-2 md:grid-cols-2">
        {catalog.map((p) => {
          const inst = installed.find((i) => i.id === p.id);
          return (
            <div key={p.id} className="glass-soft rounded-2xl p-3">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">
                    {p.name} <span className="text-xs muted">{p.version}</span>
                  </div>
                  <div className="text-xs muted">
                    {p.kind} · {p.author}
                  </div>
                </div>
                {inst ? (
                  <span className="chip text-emerald-300">установлен</span>
                ) : (
                  <button className="btn btn-sm btn-primary" disabled={!p.valid} onClick={() => install(p.id, p.name)}>
                    <Download size={12} /> Установить
                  </button>
                )}
              </div>
              {p.valid ? <p className="mt-1.5 text-xs">{p.description}</p> : <p className="mt-1.5 text-xs text-rose-300">Невалидный манифест: {p.errors?.join("; ")}</p>}
              <div className="mt-2 flex flex-wrap gap-1">
                {p.permissions?.map((perm) => (
                  <span key={perm} className={`chip font-mono ${p.sensitive_permissions?.includes(perm) ? "text-amber-300" : ""}`}>
                    {perm}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <h3 className="mb-2 mt-5 font-semibold">Установлено</h3>
      {installed.length === 0 && <div className="text-sm muted">Пока ничего. Из терминала: service install murx-click</div>}
      <div className="space-y-1.5">
        {installed.map((i) => (
          <div key={i.id} className="glass-soft flex items-center gap-2 rounded-xl p-2 text-sm">
            <b>{i.name}</b> <span className="text-xs muted">{i.version}</span>
            <span className="chip">{i.source}</span>
            <span className="hidden truncate font-mono text-[10px] muted md:inline">{i.permissions.join(", ")}</span>
            <div className="ml-auto flex gap-1">
              <button className="btn btn-sm" onClick={() => runPkg(i.id)}>
                <Play size={12} /> Run
              </button>
              <button className="btn btn-sm btn-danger" onClick={() => api.del(`/api/packages/${i.id}`).then(load)} aria-label="Удалить">
                <Trash2 size={12} />
              </button>
            </div>
          </div>
        ))}
      </div>

      <h3 className="mb-2 mt-5 font-semibold">Установить свой драйвер из проекта Studio</h3>
      <div className="flex items-center gap-2 text-sm">
        <ProjectPicker projects={projects} value={projectId} onChange={setProjectId} />
        <button className="btn btn-sm" disabled={!projectId} onClick={() => projectId && install("", "проект", [], projectId)}>
          Проверить .driver и установить
        </button>
      </div>

      {confirm && (
        <div className="fixed inset-0 z-[9600] grid place-items-center bg-black/50 p-4">
          <div className="glass rounded-mur w-full max-w-md p-5">
            <div className="mb-2 flex items-center gap-2 font-semibold">
              <ShieldAlert className="text-amber-300" /> {confirm.name} запрашивает разрешения
            </div>
            <div className="mb-2 flex flex-wrap gap-1">
              {confirm.permissions.map((p) => (
                <span key={p} className="chip font-mono">
                  {p}
                </span>
              ))}
            </div>
            <div className="mb-2 text-sm">Подтвердите чувствительные разрешения:</div>
            {confirm.needs.map((p) => (
              <label key={p} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={checked.includes(p)} onChange={(e) => setChecked(e.target.checked ? [...checked, p] : checked.filter((x) => x !== p))} />
                <span className="font-mono">{p}</span>
              </label>
            ))}
            <div className="mt-4 flex justify-end gap-2">
              <button className="btn" onClick={() => setConfirm(null)}>
                Отмена
              </button>
              <button className="btn btn-primary" disabled={!confirm.needs.every((p) => checked.includes(p))} onClick={() => install(confirm.id, confirm.name, checked, confirm.fromProject)}>
                Разрешить и установить
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
