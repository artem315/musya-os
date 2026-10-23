import { AnimatePresence, motion } from "framer-motion";
import {
  Bot,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Loader2,
  Plus,
  Send,
  Shield,
  ShieldAlert,
  Square,
  User as UserIcon,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOS } from "../desktop/os";
import { api, events, q } from "../lib/api";

type Item = {
  id: string;
  type: "user" | "assistant" | "tool" | "error" | "notice";
  text?: string;
  tool?: string;
  input?: Record<string, unknown>;
  status?: string;
  risk?: string;
  permission?: string;
  result?: string;
  reason?: string;
  data?: Record<string, unknown>;
  duration_ms?: number;
};
type Approval = { approval_id: string; conversation: string; tool: string; input: Record<string, unknown>; risk: string; permission?: string; reason: string; item: string };
type ConvSummary = { id: string; title: string; mode: string; provider: string; model: string; permissions: string[]; running: boolean };
type Providers = {
  providers: Record<string, { label: string; default_model: string }>;
  keys: { provider: string; hint: string }[];
  server_key: boolean;
  server_provider: string | null;
  permissions: string[];
  default_permissions: string[];
};

const MODES: [string, string][] = [
  ["chat", "Chat — только ответы"],
  ["code", "Code — пишет код"],
  ["agent", "Agent — план + подтверждения"],
  ["auto", "Auto — сам делает безопасные шаги"],
];

const PERM_LABELS: Record<string, string> = {
  "files.read": "Читать файлы проекта",
  "files.write": "Изменять файлы проекта",
  "browser.read": "Читать страницы",
  "browser.navigate": "Открывать сайты",
  "browser.click": "Кликать и прокручивать",
  "browser.type": "Вводить текст",
  "browser.screenshot": "Скриншоты",
  "browser.download": "Скачивать файлы",
  "events.emit": "События",
  "scripts.run": "Запускать .mysya и тесты",
  "preview.run": "Запускать локальный веб-сервер",
};

function Markdown({ text }: { text: string }) {
  const parts = text.split(/```(\w*)\n?([\s\S]*?)```/g);
  const out = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 3 === 0) {
      if (parts[i].trim())
        out.push(
          <div key={i} className="whitespace-pre-wrap break-words">
            {parts[i].trim()}
          </div>,
        );
    } else if (i % 3 === 2) {
      out.push(
        <pre key={i} className="my-1.5 overflow-auto rounded-xl bg-black/40 p-2.5 font-mono text-[12px]">
          {parts[i]}
        </pre>,
      );
    }
  }
  return <>{out}</>;
}

function statusChip(status?: string) {
  const map: Record<string, [string, string]> = {
    checking: ["проверка", "text-sky-300"],
    awaiting_approval: ["ждёт вас", "text-amber-300"],
    running: ["выполняется", "text-sky-300"],
    done: ["готово", "text-emerald-300"],
    failed: ["ошибка", "text-rose-300"],
    denied: ["отклонено", "text-rose-300"],
  };
  const [label, cls] = map[status ?? ""] ?? [status ?? "", "muted"];
  return <span className={`chip ${cls}`}>{status === "running" && <Loader2 size={10} className="animate-spin" />}{label}</span>;
}

function ToolCard({ item }: { item: Item }) {
  const [open, setOpen] = useState(false);
  const steps = (item.tool === "update_plan" ? (item.input?.steps as { title: string; status: string }[]) : null) ?? null;
  const shot = item.data?.screenshot as string | undefined;
  const cmd = item.tool === "browser" ? `service ${item.input?.command} ${((item.input?.args as unknown[]) ?? []).map((a) => JSON.stringify(a)).join(" ")}` : null;
  if (steps) {
    return (
      <div className="glass-soft rounded-2xl p-3">
        <div className="mb-1.5 text-xs font-semibold muted">План</div>
        <ol className="space-y-1 text-sm">
          {steps.map((s, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] ${s.status === "done" ? "bg-emerald-500" : s.status === "in_progress" ? "bg-sky-500" : s.status === "failed" ? "bg-rose-500" : "bg-white/15"}`}>
                {s.status === "done" ? <Check size={10} /> : i + 1}
              </span>
              <span className={s.status === "done" ? "line-through opacity-70" : ""}>{s.title}</span>
            </li>
          ))}
        </ol>
      </div>
    );
  }
  return (
    <div className="glass-soft rounded-2xl p-2.5 text-sm" data-testid="tool-card" data-status={item.status}>
      <button className="flex w-full items-center gap-2 text-left" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="font-mono text-[12px] font-semibold">{item.tool}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] muted">{cmd ?? String(item.input?.path ?? item.input?.folder ?? "")}</span>
        {item.risk && <span className={`chip ${item.risk === "high" ? "text-rose-300" : item.risk === "medium" ? "text-amber-300" : ""}`}>{item.risk}</span>}
        {statusChip(item.status)}
      </button>
      {shot && (
        <img src={`/api/engine/screenshots/${encodeURIComponent(shot)}`} alt="screenshot" className="mt-2 max-h-56 w-full rounded-xl object-contain bg-black/30" />
      )}
      {open && (
        <div className="mt-2 space-y-1.5">
          <pre className="max-h-40 overflow-auto rounded-lg bg-black/30 p-2 font-mono text-[11px]">{JSON.stringify(item.input, null, 2)}</pre>
          {item.result && <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-2 font-mono text-[11px]">{item.result}</pre>}
          {item.reason && <div className="text-[11px] muted">Политика: {item.reason}{item.duration_ms != null && ` · ${item.duration_ms} мс`}</div>}
        </div>
      )}
    </div>
  );
}

export function AIPanel({ projectId, compact = false }: { projectId: string; compact?: boolean }) {
  const os = useOS();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [convs, setConvs] = useState<ConvSummary[]>([]);
  const [convId, setConvId] = useState<string | null>(null);
  const [conv, setConv] = useState<ConvSummary | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [running, setRunning] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showPerms, setShowPerms] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  const loadList = useCallback(async () => {
    const [p, list] = await Promise.all([
      api.get<Providers>("/api/ai/providers"),
      api.get<ConvSummary[]>("/api/ai/conversations" + q({ project_id: projectId })),
    ]);
    setProviders(p);
    setConvs(list);
    setConvId((cur) => (cur && list.some((c) => c.id === cur) ? cur : list[0]?.id ?? null));
  }, [projectId]);

  const loadConv = useCallback(async (id: string) => {
    const c = await api.get<ConvSummary & { transcript: Item[]; approvals: Approval[] }>(`/api/ai/conversations/${id}`);
    setConv(c);
    setItems(c.transcript);
    setApprovals(c.approvals);
    setRunning(c.running);
  }, []);

  useEffect(() => {
    loadList().catch((e) => setError(e.message));
  }, [loadList]);
  useEffect(() => {
    if (convId) loadConv(convId).catch((e) => setError(e.message));
    else {
      setConv(null);
      setItems([]);
    }
  }, [convId, loadConv]);

  useEffect(() => {
    return events.on("ai.", (e) => {
      if (e.conversation !== convId) return;
      if (e.type === "ai.turn_started") {
        setRunning(true);
      } else if (e.type === "ai.thinking") {
        setThinking(true);
      } else if (e.type === "ai.message" || e.type === "ai.tool" || e.type === "ai.error") {
        setThinking(false);
        const it = e.item as Item;
        setItems((xs) => {
          const i = xs.findIndex((x) => x.id === it.id);
          if (i === -1) return [...xs, it];
          const copy = xs.slice();
          copy[i] = { ...copy[i], ...it };
          return copy;
        });
        if (e.type === "ai.tool" && (it.status === "done" || it.status === "failed" || it.status === "denied" || it.status === "running"))
          setApprovals((as) => as.filter((a) => a.item !== it.id));
      } else if (e.type === "ai.approval_required") {
        const a = e.approval as Approval;
        setApprovals((as) => [...as.filter((x) => x.approval_id !== a.approval_id), a]);
        const it = e.item as Item;
        setItems((xs) => (xs.some((x) => x.id === it.id) ? xs.map((x) => (x.id === it.id ? { ...x, ...it } : x)) : [...xs, it]));
      } else if (e.type === "ai.turn_finished") {
        setRunning(false);
        setThinking(false);
      } else if (e.type === "ai.saved") {
        loadConv(convId!).catch(() => undefined);
      }
    });
  }, [convId, loadConv]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [items, approvals, thinking]);

  const defaultProvider = useMemo(() => {
    if (!providers) return "anthropic";
    const withKey = providers.keys[0]?.provider;
    return withKey ?? providers.server_provider ?? "anthropic";
  }, [providers]);

  const newConv = async () => {
    setError(null);
    const c = await api.post<ConvSummary>("/api/ai/conversations", {
      project_id: projectId,
      provider: conv?.provider ?? defaultProvider,
      mode: conv?.mode ?? String(os.user.settings.ai_mode ?? "agent"),
    });
    await loadList();
    setConvId(c.id);
  };

  const patchConv = async (patch: Record<string, unknown>) => {
    if (!convId) return;
    try {
      const c = await api.patch<ConvSummary>(`/api/ai/conversations/${convId}`, patch);
      setConv((old) => ({ ...(old as ConvSummary), ...c }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const send = async () => {
    const msg = text.trim();
    if (!msg) return;
    setError(null);
    let id = convId;
    try {
      if (!id) {
        const c = await api.post<ConvSummary>("/api/ai/conversations", {
          project_id: projectId,
          provider: defaultProvider,
          mode: String(os.user.settings.ai_mode ?? "agent"),
        });
        id = c.id;
        setConvId(c.id);
        setConv(c);
        await loadList();
      }
      setText("");
      setItems((xs) => [...xs, { id: `local-${Date.now()}`, type: "user", text: msg }]);
      setRunning(true);
      await api.post(`/api/ai/conversations/${id}/messages`, { text: msg });
    } catch (e) {
      setRunning(false);
      setError((e as Error).message);
    }
  };

  const decide = async (a: Approval, decision: "once" | "session" | "deny") => {
    try {
      await api.post(`/api/ai/approvals/${a.approval_id}`, { decision });
      setApprovals((as) => as.filter((x) => x.approval_id !== a.approval_id));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const hasKey = !!providers && (conv?.provider === "scripted" || providers.keys.some((k) => k.provider === (conv?.provider ?? defaultProvider)) || (providers.server_key && providers.server_provider === (conv?.provider ?? defaultProvider)));

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="ai-panel">
      <div className="flex flex-wrap items-center gap-1.5 border-b p-2" style={{ borderColor: "var(--panel-border)" }}>
        <Bot size={16} className="text-cyan-300" />
        <select className="input !w-auto max-w-[150px] !py-1 !text-xs" value={convId ?? ""} onChange={(e) => setConvId(e.target.value || null)} aria-label="Диалог">
          {convs.length === 0 && <option value="">Новый диалог</option>}
          {convs.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <button className="btn btn-sm" onClick={newConv} title="Новый диалог" aria-label="Новый диалог">
          <Plus size={14} />
        </button>
        <select
          className="input !w-auto !py-1 !text-xs"
          value={conv?.mode ?? String(os.user.settings.ai_mode ?? "agent")}
          onChange={(e) => patchConv({ mode: e.target.value })}
          disabled={!convId || running}
          aria-label="Режим ИИ"
          data-testid="ai-mode"
        >
          {MODES.map(([m, label]) => (
            <option key={m} value={m}>
              {compact ? m : label}
            </option>
          ))}
        </select>
        {providers && (
          <select
            className="input !w-auto !py-1 !text-xs"
            value={conv?.provider ?? defaultProvider}
            onChange={(e) => patchConv({ provider: e.target.value })}
            disabled={!convId || running}
            aria-label="Провайдер"
          >
            {Object.entries(providers.providers).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
        )}
        <button className="btn btn-sm" onClick={() => setShowPerms((v) => !v)} title="Разрешения ИИ" disabled={!convId}>
          <Shield size={14} />
        </button>
        {running && (
          <button className="btn btn-sm btn-danger ml-auto" onClick={() => convId && api.post(`/api/ai/conversations/${convId}/stop`)}>
            <Square size={12} /> Стоп
          </button>
        )}
      </div>

      <AnimatePresence>
        {showPerms && conv && providers && (
          <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }} className="overflow-hidden border-b" style={{ borderColor: "var(--panel-border)" }}>
            <div className="grid grid-cols-1 gap-1 p-2 text-xs sm:grid-cols-2">
              {providers.permissions.map((p) => (
                <label key={p} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={conv.permissions.includes(p)}
                    disabled={running}
                    onChange={(e) => patchConv({ permissions: e.target.checked ? [...conv.permissions, p] : conv.permissions.filter((x) => x !== p) })}
                  />
                  <span>{PERM_LABELS[p] ?? p}</span>
                  <span className="font-mono text-[10px] muted">{p}</span>
                </label>
              ))}
              <div className="col-span-full text-[11px] muted">Удаление файлов и скачивание всегда требуют отдельного подтверждения.</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="min-h-0 flex-1 space-y-2.5 overflow-auto p-3 text-sm">
        {!hasKey && providers && (
          <div className="rounded-2xl border border-amber-400/40 bg-amber-400/10 p-3 text-xs">
            Для MUR AI нужен ключ API провайдера (Anthropic, OpenAI или OpenRouter).{" "}
            <button className="underline" onClick={() => os.open("settings", { tab: "ai" })}>
              Добавить ключ в MUR Settings
            </button>
            . Ключ хранится на сервере в зашифрованном виде.
          </div>
        )}
        {items.length === 0 && (
          <div className="mt-6 text-center muted">
            <Bot size={36} className="mx-auto mb-2 text-cyan-300" />
            <div className="font-semibold">MUR AI готов помочь</div>
            <div className="mt-1 text-xs">Например: «Создай красивый сайт для Муси, запусти и открой его»</div>
          </div>
        )}
        {items.map((it) =>
          it.type === "user" ? (
            <div key={it.id} className="flex justify-end gap-2">
              <div className="max-w-[85%] rounded-2xl rounded-tr-md bg-[color-mix(in_srgb,var(--accent)_35%,transparent)] px-3 py-2 whitespace-pre-wrap">{it.text}</div>
              <UserIcon size={16} className="mt-2 shrink-0 muted" />
            </div>
          ) : it.type === "assistant" ? (
            <div key={it.id} className="flex gap-2" data-testid="ai-message">
              <Bot size={16} className="mt-2 shrink-0 text-cyan-300" />
              <div className="max-w-[90%] rounded-2xl rounded-tl-md glass-soft px-3 py-2">
                <Markdown text={it.text ?? ""} />
              </div>
            </div>
          ) : it.type === "tool" ? (
            <ToolCard key={it.id} item={it} />
          ) : (
            <div key={it.id} className={`flex items-start gap-2 rounded-xl p-2 text-xs ${it.type === "error" ? "bg-rose-500/10 text-rose-200" : "muted"}`}>
              <CircleAlert size={14} className="mt-0.5 shrink-0" /> {it.text}
            </div>
          ),
        )}
        {approvals.map((a) => (
          <motion.div key={a.approval_id} initial={{ scale: 0.96, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="rounded-2xl border border-amber-400/50 bg-amber-400/10 p-3" data-testid="approval">
            <div className="mb-1 flex items-center gap-2 text-sm font-semibold">
              <ShieldAlert size={16} className="text-amber-300" /> MUR AI просит разрешения
            </div>
            <div className="text-xs">
              Инструмент <b className="font-mono">{a.tool}</b> · риск <b>{a.risk}</b> {a.permission && <>· {a.permission}</>}
            </div>
            <pre className="my-2 max-h-32 overflow-auto rounded-lg bg-black/30 p-2 font-mono text-[11px]">{JSON.stringify(a.input, null, 2).slice(0, 1500)}</pre>
            <div className="flex flex-wrap gap-1.5">
              <button className="btn btn-sm btn-primary" onClick={() => decide(a, "once")}>
                <Check size={13} /> Разрешить
              </button>
              {a.risk !== "high" && (
                <button className="btn btn-sm" onClick={() => decide(a, "session")} title="Больше не спрашивать для этого инструмента в этом диалоге">
                  <CheckCheck size={13} /> Всегда в этом диалоге
                </button>
              )}
              <button className="btn btn-sm btn-danger" onClick={() => decide(a, "deny")}>
                <X size={13} /> Запретить
              </button>
            </div>
          </motion.div>
        ))}
        {running && (thinking || approvals.length === 0) && (
          <div className="flex items-center gap-2 text-xs muted">
            <Loader2 size={14} className="animate-spin" /> MUR AI работает…
          </div>
        )}
        {error && <div className="rounded-xl bg-rose-500/10 p-2 text-xs text-rose-200">{error}</div>}
        <div ref={bottom} />
      </div>

      <div className="border-t p-2" style={{ borderColor: "var(--panel-border)" }}>
        <div className="flex items-end gap-2">
          <textarea
            className="input min-h-[42px] resize-none !rounded-2xl"
            rows={Math.min(5, Math.max(1, text.split("\n").length))}
            placeholder="Попросите MUR AI… (Enter — отправить)"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!running) send();
              }
            }}
            data-testid="ai-input"
          />
          <button className="btn btn-primary h-[42px] !rounded-2xl" onClick={send} disabled={running || !text.trim()} aria-label="Отправить">
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
