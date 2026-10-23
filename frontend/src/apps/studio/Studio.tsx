import {
  Bug,
  ChevronDown,
  ChevronRight,
  CloudUpload,
  File as FileIcon,
  FilePlus,
  Folder,
  FolderOpen,
  FolderPlus,
  Hammer,
  PanelRight,
  Pencil,
  Play,
  Plus,
  Save,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AIPanel } from "../../components/AIPanel";
import { BrowserView } from "../../components/BrowserView";
import { Editor } from "../../components/Editor";
import { TerminalView } from "../../components/TerminalView";
import { useOS } from "../../desktop/os";
import { api, events, q, type Diagnostic, type DriveItem, type FileEntry, type MurEvent, type Project } from "../../lib/api";
import type { AppProps } from "../registry";

type OpenFile = { path: string; content: string; saved: string; diagnostics: Diagnostic[]; binary?: boolean };
type Run = { id: string; file: string; status: string; output: string[]; error?: { type: string; message: string; line?: number } | null; tests: { name: string; passed: boolean; error?: { message: string; line?: number } }[]; permissions: string[] };
type BottomTab = "terminal" | "output" | "problems" | "logs" | "browser" | "preview";

function buildTree(files: FileEntry[]) {
  type Node = { name: string; path: string; is_dir: boolean; children: Node[] };
  const root: Node = { name: "", path: "", is_dir: true, children: [] };
  const index = new Map<string, Node>([["", root]]);
  for (const f of [...files].sort((a, b) => a.path.split("/").length - b.path.split("/").length)) {
    const parent = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "";
    const node: Node = { name: f.name, path: f.path, is_dir: f.is_dir, children: [] };
    (index.get(parent) ?? root).children.push(node);
    index.set(f.path, node);
  }
  const sort = (n: Node) => {
    n.children.sort((a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name));
    n.children.forEach(sort);
  };
  sort(root);
  return root;
}

export default function Studio({ win }: AppProps) {
  const os = useOS();
  const [projects, setProjects] = useState<Project[]>([]);
  const [templates, setTemplates] = useState<string[]>([]);
  const [projectId, setProjectId] = useState<string | null>((win.props.projectId as string) ?? null);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [open, setOpen] = useState<OpenFile[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set([""]));
  const [bottom, setBottom] = useState<BottomTab>("terminal");
  const [bottomOpen, setBottomOpen] = useState(true);
  const [aiOpen, setAiOpen] = useState(!os.isMobile);
  const [mobilePane, setMobilePane] = useState<"files" | "editor" | "ai">("editor");
  const [run, setRun] = useState<Run | null>(null);
  const [problems, setProblems] = useState<Diagnostic[]>([]);
  const [logs, setLogs] = useState<MurEvent[]>([]);
  const [preview, setPreview] = useState<{ user_url: string; url: string } | null>(null);
  const [aiPrompt, setAiPrompt] = useState<{ text: string; nonce: number } | null>(null);
  const [newDialog, setNewDialog] = useState(false);
  const [openDialog, setOpenDialog] = useState(false);
  const [status, setStatus] = useState("");
  const saveTimers = useRef(new Map<string, number>());
  const restored = useRef<string | null>(null);

  const current = open.find((f) => f.path === active) ?? null;
  const project = projects.find((p) => p.id === projectId) ?? null;

  // ------------------------------------------------------------- loading
  const loadProjects = useCallback(async () => {
    const r = await api.get<{ projects: Project[]; templates: string[] }>("/api/projects");
    setProjects(r.projects);
    setTemplates(r.templates);
    setProjectId((cur) => cur ?? r.projects[0]?.id ?? null);
  }, []);
  const loadFiles = useCallback(async () => {
    if (!projectId) return;
    setFiles(await api.get<FileEntry[]>(`/api/projects/${projectId}/files`));
  }, [projectId]);

  useEffect(() => {
    loadProjects().catch((e) => os.notify("Studio", e.message, "error"));
  }, [loadProjects, os]);

  const openFile = useCallback(
    async (path: string) => {
      if (!projectId) return;
      setActive(path);
      setMobilePane("editor");
      if (open.some((f) => f.path === path)) return;
      try {
        const r = await api.get<{ content?: string; binary: boolean }>(`/api/projects/${projectId}/file` + q({ path }));
        setOpen((fs) => (fs.some((f) => f.path === path) ? fs : [...fs, { path, content: r.content ?? "", saved: r.content ?? "", diagnostics: [], binary: r.binary }]));
      } catch (e) {
        os.notify("Не удалось открыть файл", (e as Error).message, "error");
      }
    },
    [projectId, open, os],
  );

  // Restore open tabs of the project (saved on the server in project.state).
  useEffect(() => {
    if (!projectId || restored.current === projectId) return;
    restored.current = projectId;
    setOpen([]);
    setActive(null);
    setRun(null);
    setPreview(null);
    loadFiles();
    api.get<Project & { files: FileEntry[] }>(`/api/projects/${projectId}`).then(async (p) => {
      const st = (p.state ?? {}) as { open?: string[]; active?: string };
      const want = st.open?.length ? st.open : p.files.some((f) => f.path === "main.mysya") ? ["main.mysya"] : [];
      const opened: OpenFile[] = [];
      for (const path of want.slice(0, 12)) {
        try {
          const r = await api.get<{ content?: string; binary: boolean }>(`/api/projects/${projectId}/file` + q({ path }));
          opened.push({ path, content: r.content ?? "", saved: r.content ?? "", diagnostics: [], binary: r.binary });
        } catch {
          /* file was deleted */
        }
      }
      setOpen(opened);
      setActive(st.active && opened.some((o) => o.path === st.active) ? st.active : opened[0]?.path ?? null);
    });
  }, [projectId, loadFiles]);

  useEffect(() => {
    if (!projectId || restored.current !== projectId) return;
    const t = setTimeout(() => {
      api.patch(`/api/projects/${projectId}`, { state: { open: open.map((f) => f.path), active } }).catch(() => undefined);
    }, 800);
    return () => clearTimeout(t);
  }, [projectId, open, active]);

  // Live updates: AI or other windows changed files; run output; engine logs.
  useEffect(() => {
    const offs = [
      events.on("project.file_changed", async (e) => {
        if (e.project !== projectId) return;
        loadFiles();
        const path = String(e.path);
        if (e.action === "saved") return;
        const f = open.find((o) => o.path === path);
        if (f && f.content === f.saved && projectId) {
          const r = await api.get<{ content?: string }>(`/api/projects/${projectId}/file` + q({ path })).catch(() => null);
          if (r?.content !== undefined) setOpen((fs) => fs.map((x) => (x.path === path ? { ...x, content: r.content!, saved: r.content! } : x)));
        }
      }),
      events.on("run.", (e) => {
        if (e.type === "run.started") {
          const r = e.run as Run & { project_id: string };
          if (r.project_id === projectId) setRun(r);
        } else if (e.type === "run.output") {
          setRun((r) => (r && r.id === e.run ? { ...r, output: [...r.output, String(e.text)] } : r));
        } else if (e.type === "run.finished") {
          const r = e.run as Run;
          setRun((cur) => (cur && cur.id === r.id ? r : cur));
          if (r.error?.line && r.file) {
            setOpen((fs) => fs.map((f) => (f.path === r.file ? { ...f, diagnostics: [{ severity: "error", line: r.error!.line!, column: 1, message: `${r.error!.type}: ${r.error!.message}` }] } : f)));
          }
        }
      }),
      events.on("*", (e) => {
        if (/^(job|browser|run|preview|custom|ai\.tool)/.test(e.type)) setLogs((l) => [e, ...l].slice(0, 400));
      }),
    ];
    return () => offs.forEach((o) => o());
  }, [projectId, open, loadFiles]);

  // ------------------------------------------------------------- editing
  const save = useCallback(
    async (path?: string) => {
      const f = open.find((o) => o.path === (path ?? active));
      if (!f || !projectId || f.binary) return;
      try {
        const r = await api.put<{ diagnostics: Diagnostic[] }>(`/api/projects/${projectId}/file`, { path: f.path, content: f.content });
        setOpen((fs) => fs.map((x) => (x.path === f.path ? { ...x, saved: f.content, diagnostics: r.diagnostics } : x)));
        setStatus(`Сохранено ${f.path}`);
      } catch (e) {
        os.notify("Ошибка сохранения", (e as Error).message, "error");
      }
    },
    [open, active, projectId, os],
  );

  const onChange = (value: string) => {
    if (!active) return;
    setOpen((fs) => fs.map((f) => (f.path === active ? { ...f, content: value } : f)));
    // Autosave after a short pause so nothing is lost on reload or crash.
    const path = active;
    window.clearTimeout(saveTimers.current.get(path));
    saveTimers.current.set(
      path,
      window.setTimeout(() => {
        setOpen((fs) => {
          const f = fs.find((x) => x.path === path);
          if (f && f.content !== f.saved && projectId) {
            api
              .put<{ diagnostics: Diagnostic[] }>(`/api/projects/${projectId}/file`, { path, content: f.content })
              .then((r) => setOpen((xs) => xs.map((x) => (x.path === path ? { ...x, saved: f.content, diagnostics: r.diagnostics } : x))))
              .catch(() => undefined);
          }
          return fs;
        });
      }, 1200),
    );
  };

  const saveAll = async () => {
    for (const f of open) if (f.content !== f.saved) await save(f.path);
  };

  const closeTab = (path: string) => {
    const f = open.find((x) => x.path === path);
    if (f && f.content !== f.saved && !confirm(`Закрыть ${path} без сохранения?`)) return;
    setOpen((fs) => fs.filter((x) => x.path !== path));
    if (active === path) setActive(open.filter((x) => x.path !== path).slice(-1)[0]?.path ?? null);
  };

  // ------------------------------------------------------------- actions
  const doRun = async (test = false, debug = false) => {
    if (!projectId) return;
    await saveAll();
    const path = current?.path.endsWith(".mysya") ? current.path : "";
    try {
      const r = await api.post<Run>(`/api/projects/${projectId}/run`, { path, test });
      // Live events may already have delivered output (or the finish) before this response.
      setRun((prev) => (prev && prev.id === r.id ? prev : r));
      setBottom(debug ? "logs" : "output");
      setBottomOpen(true);
      if (debug) setLogs([]);
    } catch (e) {
      os.notify("Запуск не удался", (e as Error).message, "error");
    }
  };
  // Safety net for missed WebSocket events: poll the run while it is running.
  useEffect(() => {
    if (!run || run.status !== "running" || !projectId) return;
    const t = setInterval(() => {
      api
        .get<Run>(`/api/projects/${projectId}/runs/${run.id}`)
        .then((r) => r.status !== "running" && setRun(r))
        .catch(() => undefined);
    }, 2000);
    return () => clearInterval(t);
  }, [run, projectId]);

  const stopRun = () => run && projectId && api.post(`/api/projects/${projectId}/runs/${run.id}/stop`);

  const build = async () => {
    if (!projectId) return;
    await saveAll();
    const r = await api.post<{ ok: boolean; files: number; problems: Diagnostic[] }>(`/api/projects/${projectId}/build`);
    setProblems(r.problems);
    setBottom("problems");
    setBottomOpen(true);
    os.notify(r.ok ? "Сборка: OK" : "Сборка: есть ошибки", `${r.files} файлов, проблем: ${r.problems.length}`, r.ok ? "ok" : "error");
    return r.ok;
  };

  const deploy = async () => {
    if (!projectId) return;
    await saveAll();
    try {
      const r = await api.post<{ path: string; size: number }>(`/api/projects/${projectId}/export`);
      os.notify("Проект упакован", `${r.path} в MUR Drive. Сайт доступен во вкладке Preview по защищённой ссылке.`, "ok");
    } catch (e) {
      os.notify("Deploy остановлен", (e as Error).message, "error");
      build();
    }
  };

  const aiFix = () => {
    if (!current) return;
    const diag = current.diagnostics.map((d) => `строка ${d.line}: ${d.message}`).join("\n");
    const runErr = run?.error ? `Последний запуск ${run.file}: ${run.error.type} (строка ${run.error.line}): ${run.error.message}` : "";
    const text = `Исправь ошибки в файле ${current.path}.\n${diag}\n${runErr}\nПрочитай файл, исправь, затем запусти тесты или скрипт и проверь результат.`.replace(/\n+/g, "\n");
    setAiOpen(true);
    setMobilePane("ai");
    setAiPrompt({ text, nonce: Date.now() });
  };

  const startPreview = async () => {
    if (!projectId) return;
    await saveAll();
    const folder = files.some((f) => f.path === "index.html") ? "." : files.find((f) => f.path.endsWith("/index.html"))?.path.replace(/\/index\.html$/, "") ?? ".";
    try {
      const r = await api.post<{ user_url: string; url: string }>(`/api/projects/${projectId}/preview/start`, { path: folder });
      setPreview(r);
    } catch (e) {
      os.notify("Preview", (e as Error).message, "error");
    }
  };

  // ------------------------------------------------------------- files
  const newFile = async (dir = "") => {
    const name = prompt("Имя файла (например main.mysya, index.html, app.yml):", "new.mysya");
    if (!name || !projectId) return;
    const path = (dir ? `${dir}/` : "") + name;
    await api.put(`/api/projects/${projectId}/file`, { path, content: "" });
    await loadFiles();
    openFile(path);
  };
  const newFolder = async (dir = "") => {
    const name = prompt("Имя папки:");
    if (!name || !projectId) return;
    await api.post(`/api/projects/${projectId}/mkdir`, { path: (dir ? `${dir}/` : "") + name });
    loadFiles();
  };
  const removePath = async (path: string) => {
    if (!projectId || !confirm(`Удалить ${path}?`)) return;
    await api.post(`/api/projects/${projectId}/delete`, { path });
    setOpen((fs) => fs.filter((f) => f.path !== path && !f.path.startsWith(path + "/")));
    loadFiles();
  };
  const renamePath = async (path: string) => {
    const np = prompt("Новое имя / путь:", path);
    if (!np || np === path || !projectId) return;
    await api.post(`/api/projects/${projectId}/rename`, { path, new_path: np });
    setOpen((fs) => fs.map((f) => (f.path === path ? { ...f, path: np } : f)));
    if (active === path) setActive(np);
    loadFiles();
  };

  const tree = useMemo(() => buildTree(files), [files]);
  type TreeNode = ReturnType<typeof buildTree>;
  const renderNode = (n: TreeNode, depth: number): React.ReactNode =>
    n.children.map((c) => (
      <div key={c.path}>
        <div
          className={`group flex cursor-pointer items-center gap-1 rounded-lg px-1.5 py-0.5 text-[13px] hover:bg-white/10 ${active === c.path ? "bg-white/10" : ""}`}
          style={{ paddingLeft: 6 + depth * 12 }}
          onClick={() => {
            if (c.is_dir) setExpanded((s) => (s.has(c.path) ? new Set([...s].filter((x) => x !== c.path)) : new Set([...s, c.path])));
            else openFile(c.path);
          }}
          data-testid={`file-${c.path}`}
        >
          {c.is_dir ? expanded.has(c.path) ? <ChevronDown size={12} /> : <ChevronRight size={12} /> : <span className="w-3" />}
          {c.is_dir ? expanded.has(c.path) ? <FolderOpen size={14} className="text-amber-300" /> : <Folder size={14} className="text-amber-300" /> : <FileIcon size={14} className={c.name.endsWith(".mysya") ? "text-violet-300" : "muted"} />}
          <span className="min-w-0 flex-1 truncate">{c.name}</span>
          <span className="hidden gap-0.5 group-hover:flex">
            {c.is_dir && (
              <button onClick={(e) => (e.stopPropagation(), newFile(c.path))} title="Новый файл">
                <Plus size={12} />
              </button>
            )}
            <button onClick={(e) => (e.stopPropagation(), renamePath(c.path))} title="Переименовать">
              <Pencil size={12} />
            </button>
            <button onClick={(e) => (e.stopPropagation(), removePath(c.path))} title="Удалить">
              <Trash2 size={12} />
            </button>
          </span>
        </div>
        {c.is_dir && expanded.has(c.path) && renderNode(c, depth + 1)}
      </div>
    ));

  const dark = String(os.user.settings.theme ?? "neon") !== "light";
  const running = run?.status === "running";

  // ------------------------------------------------------------- render
  const explorer = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 p-2">
        <span className="text-xs font-semibold uppercase tracking-wide muted">Проект</span>
        <div className="ml-auto flex gap-0.5">
          <button className="btn btn-sm btn-ghost !px-1.5" onClick={() => newFile()} title="Новый файл" aria-label="Новый файл">
            <FilePlus size={14} />
          </button>
          <button className="btn btn-sm btn-ghost !px-1.5" onClick={() => newFolder()} title="Новая папка" aria-label="Новая папка">
            <FolderPlus size={14} />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-1 pb-2">{renderNode(tree, 0)}</div>
    </div>
  );

  const editorArea = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-9 shrink-0 items-center gap-0.5 overflow-x-auto border-b px-1" style={{ borderColor: "var(--panel-border)" }}>
        {open.map((f) => (
          <div key={f.path} className={`tab flex shrink-0 items-center gap-1.5 ${active === f.path ? "tab-active" : ""}`} onClick={() => setActive(f.path)}>
            <span>{f.path.split("/").pop()}</span>
            {f.content !== f.saved && <span className="h-1.5 w-1.5 rounded-full bg-amber-300" title="Не сохранено" />}
            {f.diagnostics.some((d) => d.severity === "error") && <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />}
            <button onClick={(e) => (e.stopPropagation(), closeTab(f.path))} aria-label={`Закрыть ${f.path}`}>
              <X size={11} />
            </button>
          </div>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        {current ? (
          current.binary ? (
            <div className="grid h-full place-items-center muted text-sm">Бинарный файл — откройте его в MUR Drive.</div>
          ) : (
            <Editor path={current.path} value={current.content} onChange={onChange} onSave={() => save()} onRun={() => doRun()} diagnostics={current.diagnostics} dark={dark} />
          )
        ) : (
          <div className="grid h-full place-items-center p-6 text-center text-sm muted">
            <div>
              <Sparkles className="mx-auto mb-2 text-violet-300" />
              Откройте файл слева или попросите MUR AI создать проект.
              <div className="mt-2 text-xs">Ctrl+S — сохранить · Ctrl+Enter / F5 — запустить</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  const bottomPanel = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-0.5 overflow-x-auto border-b px-1.5 py-1" style={{ borderColor: "var(--panel-border)" }}>
        {(
          [
            ["terminal", "Терминал"],
            ["output", "Вывод"],
            ["problems", `Проблемы${problems.length ? ` (${problems.length})` : ""}`],
            ["logs", "Logs"],
            ["browser", "Browser"],
            ["preview", "Preview"],
          ] as [BottomTab, string][]
        ).map(([k, label]) => (
          <button key={k} className={`tab ${bottom === k ? "tab-active" : ""}`} onClick={() => (setBottom(k), setBottomOpen(true))} data-testid={`tab-${k}`}>
            {label}
          </button>
        ))}
        <button className="btn btn-sm btn-ghost ml-auto" onClick={() => setBottomOpen((v) => !v)} aria-label="Свернуть панель">
          {bottomOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
      </div>
      {bottomOpen && projectId && (
        <div className="min-h-0 flex-1">
          <div className={bottom === "terminal" ? "h-full" : "hidden"}>
            <TerminalView projectId={projectId} />
          </div>
          {bottom === "output" && (
            <div className="h-full overflow-auto bg-black/35 p-2.5 font-mono text-[12.5px]" data-testid="run-output">
              {!run && <div className="muted">Нажмите Run, чтобы запустить {current?.path.endsWith(".mysya") ? current.path : "entry из app.yml"}.</div>}
              {run && (
                <>
                  <div className="mb-1 text-violet-300">
                    ▶ {run.file} · {run.status} · разрешения: {run.permissions.join(", ") || "нет"}
                  </div>
                  {run.output.map((l, i) => (
                    <div key={i} className="whitespace-pre-wrap">
                      {l}
                    </div>
                  ))}
                  {run.tests?.map((t) => (
                    <div key={t.name} className={t.passed ? "text-emerald-300" : "text-rose-300"}>
                      {t.passed ? "✔" : "✖"} {t.name} {t.error && `— ${t.error.message} (строка ${t.error.line})`}
                    </div>
                  ))}
                  {run.error && (
                    <div className="text-rose-300">
                      ✖ {run.error.type}: {run.error.message} {run.error.line && `(строка ${run.error.line})`}
                    </div>
                  )}
                  {run.status !== "running" && <div className="mt-1 muted">■ завершено: {run.status}</div>}
                </>
              )}
            </div>
          )}
          {bottom === "problems" && (
            <div className="h-full overflow-auto p-2 text-sm">
              {problems.length === 0 && <div className="muted">Нажмите Build, чтобы проверить все .mysya, app.yml и .driver.</div>}
              {problems.map((p, i) => (
                <button key={i} className="flex w-full items-start gap-2 rounded-lg px-2 py-1 text-left hover:bg-white/10" onClick={() => p.file && openFile(p.file)}>
                  <span className={p.severity === "error" ? "text-rose-300" : "text-amber-300"}>{p.severity === "error" ? "✖" : "⚠"}</span>
                  <span className="font-mono text-xs muted">
                    {p.file}:{p.line}
                  </span>
                  <span>{p.message}</span>
                </button>
              ))}
            </div>
          )}
          {bottom === "logs" && (
            <div className="h-full overflow-auto bg-black/35 p-2 font-mono text-[11.5px]">
              {logs.length === 0 && <div className="muted">Здесь видны все реальные действия Service Engine, запуски и события.</div>}
              {logs.map((e, i) => {
                const job = e.job as { command?: string; status?: string; duration_ms?: number; error?: { message: string } } | undefined;
                return (
                  <div key={i} className={/failed|timeout|blocked|error/.test(e.type) ? "text-rose-300" : e.type.startsWith("job") ? "text-amber-100" : "muted"}>
                    {new Date((e.ts ?? 0) * 1000).toLocaleTimeString("ru-RU")} {e.type}{" "}
                    {job?.command ?? (e.url as string) ?? (e.text as string) ?? ""}
                    {job?.duration_ms != null && ` (${job.duration_ms} мс)`}
                    {job?.error && ` — ${job.error.message}`}
                  </div>
                );
              })}
            </div>
          )}
          {bottom === "browser" && <BrowserView />}
          {bottom === "preview" && (
            <div className="flex h-full flex-col">
              <div className="flex items-center gap-2 border-b p-1.5 text-xs" style={{ borderColor: "var(--panel-border)" }}>
                <button className="btn btn-sm btn-primary" onClick={startPreview}>
                  <Play size={12} /> Запустить веб-сервер
                </button>
                {preview && (
                  <>
                    <button className="btn btn-sm" onClick={() => api.post("/api/engine/command", { verb: "load", args: [preview.url] }).then(() => setBottom("browser"))}>
                      Открыть в Service Engine
                    </button>
                    <a className="btn btn-sm" href={preview.user_url} target="_blank" rel="noreferrer">
                      Новая вкладка
                    </a>
                    <span className="muted truncate">{preview.url}</span>
                  </>
                )}
              </div>
              {preview ? (
                <iframe title="Preview" src={preview.user_url} className="min-h-0 flex-1 bg-white" sandbox="allow-scripts allow-forms allow-modals" />
              ) : (
                <div className="grid flex-1 place-items-center text-sm muted">Нужен index.html в проекте.</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="studio">
      {/* toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-1 border-b p-1.5" style={{ borderColor: "var(--panel-border)" }}>
        <select className="input !w-auto max-w-[180px] !py-1 !text-xs" value={projectId ?? ""} onChange={(e) => (restored.current = null, setProjectId(e.target.value))} aria-label="Проект" data-testid="project-select">
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button className="btn btn-sm" onClick={() => setNewDialog(true)} title="Новый проект">
          <Plus size={13} />
        </button>
        <span className="mx-1 h-5 w-px bg-white/15" />
        <button className="btn btn-sm btn-primary" onClick={() => doRun()} disabled={running} data-testid="btn-run">
          <Play size={13} /> Run
        </button>
        <button className="btn btn-sm" onClick={stopRun} disabled={!running}>
          <Square size={12} /> Stop
        </button>
        <button className="btn btn-sm" onClick={() => doRun(false, true)} disabled={running} title="Запуск с трассировкой всех действий Service Engine">
          <Bug size={13} /> Debug
        </button>
        <button className="btn btn-sm" onClick={() => doRun(true)} disabled={running} title="Запустить test-блоки">
          ✔ Test
        </button>
        <button className="btn btn-sm" onClick={() => save()} disabled={!current} data-testid="btn-save">
          <Save size={13} /> Save
        </button>
        <button className="btn btn-sm" onClick={() => setOpenDialog(true)} title="Открыть файл из MUR Drive">
          <FolderOpen size={13} /> Open
        </button>
        <button className="btn btn-sm" onClick={build}>
          <Hammer size={13} /> Build
        </button>
        <button className="btn btn-sm" onClick={deploy} title="Проверить и упаковать проект в MUR Drive/Exports">
          <CloudUpload size={13} /> Deploy
        </button>
        <button className="btn btn-sm" onClick={aiFix} disabled={!current} title="Попросить MUR AI исправить ошибки">
          <Sparkles size={13} /> AI Fix
        </button>
        <span className="ml-auto hidden truncate text-[11px] muted md:inline">{status}</span>
        <button className={`btn btn-sm ${aiOpen ? "tab-active" : ""}`} onClick={() => setAiOpen((v) => !v)} title="Панель MUR AI">
          <PanelRight size={13} /> AI
        </button>
      </div>

      {os.isMobile && (
        <div className="flex shrink-0 gap-1 border-b p-1" style={{ borderColor: "var(--panel-border)" }}>
          {(["files", "editor", "ai"] as const).map((p) => (
            <button key={p} className={`tab flex-1 ${mobilePane === p ? "tab-active" : ""}`} onClick={() => setMobilePane(p)}>
              {p === "files" ? "Файлы" : p === "editor" ? "Код" : "MUR AI"}
            </button>
          ))}
        </div>
      )}

      {os.isMobile ? (
        <div className="min-h-0 flex-1">
          {mobilePane === "files" && explorer}
          {mobilePane === "editor" && (
            <div className="flex h-full flex-col">
              <div className="min-h-0 flex-1">{editorArea}</div>
              <div className={bottomOpen ? "h-[45%]" : ""}>{bottomPanel}</div>
            </div>
          )}
          {mobilePane === "ai" && projectId && <AIPanelWithPrompt projectId={projectId} prompt={aiPrompt} />}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <aside className="w-56 shrink-0 border-r" style={{ borderColor: "var(--panel-border)" }}>
            {explorer}
          </aside>
          <main className="flex min-w-0 flex-1 flex-col">
            <div className="min-h-0 flex-1">{editorArea}</div>
            <div className={`shrink-0 border-t ${bottomOpen ? "h-[38%]" : ""}`} style={{ borderColor: "var(--panel-border)" }}>
              {bottomPanel}
            </div>
          </main>
          {aiOpen && projectId && (
            <aside className="w-[360px] shrink-0 border-l" style={{ borderColor: "var(--panel-border)" }}>
              <AIPanelWithPrompt projectId={projectId} prompt={aiPrompt} />
            </aside>
          )}
        </div>
      )}

      {newDialog && (
        <NewProjectDialog
          templates={templates}
          onClose={() => setNewDialog(false)}
          onCreated={async (p) => {
            setNewDialog(false);
            await loadProjects();
            restored.current = null;
            setProjectId(p.id);
          }}
        />
      )}
      {openDialog && projectId && (
        <OpenFromDrive
          projectId={projectId}
          onClose={() => setOpenDialog(false)}
          onDone={() => {
            setOpenDialog(false);
            loadFiles();
          }}
        />
      )}
      {!project && projects.length === 0 && <div className="absolute inset-0 grid place-items-center muted">Загрузка проектов…</div>}
    </div>
  );
}

function AIPanelWithPrompt({ projectId, prompt }: { projectId: string; prompt: { text: string; nonce: number } | null }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!prompt || !host.current) return;
    const ta = host.current.querySelector<HTMLTextAreaElement>("[data-testid=ai-input]");
    if (!ta) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    setter?.call(ta, prompt.text);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus();
  }, [prompt]);
  return (
    <div ref={host} className="h-full">
      <AIPanel projectId={projectId} compact />
    </div>
  );
}

function NewProjectDialog({ templates, onClose, onCreated }: { templates: string[]; onClose: () => void; onCreated: (p: Project) => void }) {
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("blank");
  const [err, setErr] = useState<string | null>(null);
  const labels: Record<string, string> = { blank: "Пустой (.mysya)", browser: "Браузерный бот + .driver", website: "Сайт (HTML/CSS/JS)" };
  return (
    <div className="absolute inset-0 z-50 grid place-items-center bg-black/50 p-4" onClick={onClose}>
      <form
        className="glass rounded-mur w-full max-w-sm space-y-3 p-5"
        onClick={(e) => e.stopPropagation()}
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            onCreated(await api.post<Project>("/api/projects", { name, template }));
          } catch (x) {
            setErr((x as Error).message);
          }
        }}
      >
        <div className="font-semibold">Новый проект</div>
        <input className="input" autoFocus placeholder="Название" value={name} onChange={(e) => setName(e.target.value)} required />
        <div className="grid gap-1.5">
          {templates.map((t) => (
            <label key={t} className="flex items-center gap-2 text-sm">
              <input type="radio" name="template" checked={template === t} onChange={() => setTemplate(t)} /> {labels[t] ?? t}
            </label>
          ))}
        </div>
        {err && <div className="text-sm text-rose-300">{err}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn btn-primary">Создать</button>
        </div>
      </form>
    </div>
  );
}

function OpenFromDrive({ projectId, onClose, onDone }: { projectId: string; onClose: () => void; onDone: () => void }) {
  const [path, setPath] = useState("/");
  const [items, setItems] = useState<DriveItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api
      .get<{ items: DriveItem[] }>("/api/drive/list" + q({ path }))
      .then((r) => setItems(r.items))
      .catch((e) => setErr(e.message));
  }, [path]);
  const pick = async (it: DriveItem) => {
    try {
      await api.post("/api/drive/to-project", { path: it.path, project_id: projectId });
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="absolute inset-0 z-50 grid place-items-center bg-black/50 p-4" onClick={onClose}>
      <div className="glass rounded-mur flex max-h-[80%] w-full max-w-md flex-col p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 font-semibold">Открыть из MUR Drive → в проект</div>
        <div className="mb-2 font-mono text-xs muted">{path}</div>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-auto">
          {path !== "/" && (
            <button className="w-full rounded-lg px-2 py-1 text-left text-sm hover:bg-white/10" onClick={() => setPath(path.split("/").slice(0, -1).join("/") || "/")}>
              ..
            </button>
          )}
          {items.map((it) => (
            <div key={it.id} className="flex items-center gap-2 rounded-lg px-2 py-1 text-sm hover:bg-white/10">
              {it.is_dir ? <Folder size={14} className="text-amber-300" /> : <FileIcon size={14} />}
              <button className="min-w-0 flex-1 truncate text-left" onClick={() => (it.is_dir ? setPath(it.path) : pick(it))}>
                {it.name}
              </button>
              <button className="btn btn-sm" onClick={() => pick(it)}>
                Импорт
              </button>
            </div>
          ))}
        </div>
        {err && <div className="mt-2 text-sm text-rose-300">{err}</div>}
        <button className="btn mt-3 self-end" onClick={onClose}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
