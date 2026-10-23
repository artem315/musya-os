import { ArrowUp, Download, File as FileIcon, FileText, Folder, FolderPlus, Image as ImageIcon, Pencil, RefreshCw, Search, Trash2, Upload, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useOS } from "../desktop/os";
import { api, events, formatBytes, q, type DriveItem } from "../lib/api";
import type { AppProps } from "./registry";

type Preview = DriveItem & { kind: "text" | "image" | "audio" | "video" | "pdf" | "binary"; text?: string; truncated?: boolean };

export default function DriveApp({ win }: AppProps) {
  const os = useOS();
  const [path, setPath] = useState<string>((win.props.path as string) ?? "/");
  const [items, setItems] = useState<DriveItem[]>([]);
  const [usage, setUsage] = useState({ used: 0, quota: 1 });
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DriveItem[] | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ items: DriveItem[]; used: number; quota: number }>("/api/drive/list" + q({ path }));
      setItems(r.items);
      setUsage({ used: r.used, quota: r.quota });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [path]);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => events.on("drive.changed", () => load()), [load]);

  const upload = async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      setUploading(f.name);
      const fd = new FormData();
      fd.append("file", f);
      fd.append("path", path);
      try {
        await api.post("/api/drive/upload", fd);
      } catch (e) {
        os.notify(`Не удалось загрузить ${f.name}`, (e as Error).message, "error");
      }
    }
    setUploading(null);
    load();
  };

  const open = async (it: DriveItem) => {
    if (it.is_dir) {
      setPath(it.path);
      setResults(null);
      setPreview(null);
      return;
    }
    try {
      setPreview(await api.get<Preview>("/api/drive/preview" + q({ path: it.path })));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const crumbs = path === "/" ? [] : path.slice(1).split("/");
  const list = results ?? items;
  const icon = (it: DriveItem) =>
    it.is_dir ? <Folder size={18} className="text-amber-300" /> : it.mime.startsWith("image/") ? <ImageIcon size={18} className="text-pink-300" /> : it.mime.startsWith("text/") ? <FileText size={18} className="text-sky-300" /> : <FileIcon size={18} className="muted" />;

  return (
    <div
      className="relative flex h-full min-h-0 flex-col"
      onDragOver={(e) => (e.preventDefault(), setDragOver(true))}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
      }}
      data-testid="drive"
    >
      <div className="flex flex-wrap items-center gap-1 border-b p-1.5" style={{ borderColor: "var(--panel-border)" }}>
        <button className="btn btn-sm" disabled={path === "/"} onClick={() => setPath(path.split("/").slice(0, -1).join("/") || "/")} aria-label="Вверх">
          <ArrowUp size={14} />
        </button>
        <div className="flex min-w-0 items-center gap-0.5 overflow-x-auto text-sm">
          <button className="tab" onClick={() => setPath("/")}>
            MUR Drive
          </button>
          {crumbs.map((c, i) => (
            <span key={i} className="flex items-center">
              <span className="muted">/</span>
              <button className="tab" onClick={() => setPath("/" + crumbs.slice(0, i + 1).join("/"))}>
                {c}
              </button>
            </span>
          ))}
        </div>
        <form
          className="ml-auto flex items-center gap-1"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!query.trim()) return setResults(null);
            const r = await api.get<{ items: DriveItem[] }>("/api/drive/search" + q({ q: query }));
            setResults(r.items);
          }}
        >
          <input className="input !w-40 !py-1 !text-xs" placeholder="Поиск" value={query} onChange={(e) => setQuery(e.target.value)} />
          <button className="btn btn-sm" aria-label="Искать">
            <Search size={13} />
          </button>
        </form>
        <button className="btn btn-sm" onClick={() => act(async () => { const n = prompt("Имя папки:"); if (n) await api.post("/api/drive/mkdir", { path: `${path === "/" ? "" : path}/${n}` }); })}>
          <FolderPlus size={13} /> Папка
        </button>
        <button className="btn btn-sm btn-primary" onClick={() => fileInput.current?.click()}>
          <Upload size={13} /> Загрузить
        </button>
        <button className="btn btn-sm btn-ghost" onClick={load} aria-label="Обновить">
          <RefreshCw size={13} />
        </button>
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => e.target.files && upload(e.target.files)} data-testid="drive-upload" />
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto p-2">
          {results && (
            <div className="mb-2 flex items-center gap-2 text-xs muted">
              Найдено: {results.length}
              <button className="btn btn-sm" onClick={() => setResults(null)}>
                <X size={12} /> Сбросить
              </button>
            </div>
          )}
          {list.length === 0 && <div className="p-6 text-center text-sm muted">Папка пуста. Перетащите сюда файлы.</div>}
          <table className="w-full text-sm">
            <tbody>
              {list.map((it) => (
                <tr key={it.id} className="group cursor-pointer rounded-lg hover:bg-white/5" onDoubleClick={() => open(it)} onClick={() => !it.is_dir && open(it)}>
                  <td className="w-8 py-1.5 pl-2">{icon(it)}</td>
                  <td className="max-w-0 truncate py-1.5">
                    <button className="truncate text-left" onClick={(e) => (e.stopPropagation(), open(it))}>
                      {results ? it.path : it.name}
                    </button>
                  </td>
                  <td className="hidden w-24 py-1.5 text-right text-xs muted sm:table-cell">{it.is_dir ? "" : formatBytes(it.size)}</td>
                  <td className="hidden w-36 py-1.5 text-right text-xs muted md:table-cell">{new Date(it.updated_at * 1000).toLocaleString("ru-RU")}</td>
                  <td className="w-28 py-1.5 pr-2 text-right">
                    <span className="inline-flex gap-0.5 opacity-60 group-hover:opacity-100">
                      {!it.is_dir && (
                        <a className="btn btn-sm btn-ghost !px-1.5" href={`/api/drive/download${q({ path: it.path })}`} onClick={(e) => e.stopPropagation()} aria-label="Скачать" download>
                          <Download size={13} />
                        </a>
                      )}
                      <button
                        className="btn btn-sm btn-ghost !px-1.5"
                        aria-label="Переименовать"
                        onClick={(e) => {
                          e.stopPropagation();
                          const np = prompt("Новый путь:", it.path);
                          if (np && np !== it.path) act(() => api.post("/api/drive/rename", { path: it.path, new_path: np }));
                        }}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="btn btn-sm btn-ghost !px-1.5"
                        aria-label="Удалить"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (confirm(`Удалить ${it.path}${it.is_dir ? " со всем содержимым" : ""}?`)) act(() => api.post("/api/drive/delete", { path: it.path }));
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {preview && (
          <aside className="flex w-[45%] max-w-md shrink-0 flex-col border-l" style={{ borderColor: "var(--panel-border)" }}>
            <div className="flex items-center gap-2 border-b p-2 text-sm" style={{ borderColor: "var(--panel-border)" }}>
              <span className="min-w-0 flex-1 truncate font-semibold">{preview.name}</span>
              <span className="text-xs muted">{formatBytes(preview.size)}</span>
              <button className="btn btn-sm btn-ghost" onClick={() => setPreview(null)} aria-label="Закрыть просмотр">
                <X size={13} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-2">
              {preview.kind === "text" && <pre className="whitespace-pre-wrap break-words font-mono text-xs">{preview.text}{preview.truncated && "\n…"}</pre>}
              {preview.kind === "image" && <img className="mx-auto max-h-full rounded-xl" src={`/api/drive/download${q({ path: preview.path, inline: true })}`} alt={preview.name} />}
              {preview.kind === "audio" && <audio controls className="w-full" src={`/api/drive/download${q({ path: preview.path, inline: true })}`} />}
              {preview.kind === "video" && <video controls className="w-full rounded-xl" src={`/api/drive/download${q({ path: preview.path, inline: true })}`} />}
              {preview.kind === "pdf" && <iframe title={preview.name} className="h-full min-h-[400px] w-full rounded-xl bg-white" src={`/api/drive/download${q({ path: preview.path, inline: true })}`} />}
              {preview.kind === "binary" && <div className="text-sm muted">Предпросмотр недоступен для этого типа ({preview.mime}). Скачайте файл.</div>}
            </div>
          </aside>
        )}
      </div>

      <div className="flex items-center gap-2 border-t px-2 py-1 text-[11px] muted" style={{ borderColor: "var(--panel-border)" }}>
        <div className="h-1.5 w-32 overflow-hidden rounded-full bg-white/10">
          <div className="h-full bg-gradient-to-r from-violet-500 to-cyan-400" style={{ width: `${Math.min(100, (usage.used / usage.quota) * 100)}%` }} />
        </div>
        {formatBytes(usage.used)} из {formatBytes(usage.quota)}
        {uploading && <span className="text-cyan-300">Загрузка {uploading}…</span>}
        {error && <span className="text-rose-300">{error}</span>}
      </div>
      {dragOver && <div className="pointer-events-none absolute inset-2 grid place-items-center rounded-mur border-2 border-dashed border-violet-400 bg-violet-500/10 text-lg">Отпустите, чтобы загрузить в {path}</div>}
    </div>
  );
}
