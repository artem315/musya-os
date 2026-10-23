import { useEffect, useRef, useState } from "react";
import { api, events, type Job } from "../lib/api";

type Line = { id: number; kind: "in" | "out" | "err" | "sys" | "job"; text: string };
let lid = 0;

const HISTORY_KEY = "murcloud.terminal.history";

export function TerminalView({ projectId, showJobs = true }: { projectId: string; showJobs?: boolean }) {
  const [lines, setLines] = useState<Line[]>([
    { id: lid++, kind: "sys", text: "MUR Terminal · MUSYA REPL + Service Engine. Введите help." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    } catch {
      return [];
    }
  });
  const [hpos, setHpos] = useState(-1);
  const box = useRef<HTMLDivElement>(null);
  const runIds = useRef(new Set<string>());

  const push = (kind: Line["kind"], text: string | string[]) =>
    setLines((ls) => [...ls, ...(Array.isArray(text) ? text : [text]).map((t) => ({ id: lid++, kind, text: t }))].slice(-1500));

  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight });
  }, [lines]);

  useEffect(() => {
    const offs = [
      events.on("run.output", (e) => {
        if (runIds.current.has(String(e.run))) push("out", String(e.text));
      }),
      events.on("run.finished", (e) => {
        const r = e.run as { id: string; status: string; error?: { type: string; message: string; line?: number } };
        if (!runIds.current.has(r.id)) return;
        runIds.current.delete(r.id);
        if (r.error) push("err", `✖ ${r.error.type}: ${r.error.message}${r.error.line ? ` (line ${r.error.line})` : ""}`);
        push("sys", `■ run ${r.id} ${r.status}`);
      }),
    ];
    if (showJobs) {
      offs.push(
        events.on("job.started", (e) => {
          const j = e.job as Job;
          if (j.source !== "terminal") push("job", `▶ [${j.source}] ${j.command}`);
        }),
        events.on("job.failed", (e) => {
          const j = e.job as Job;
          push("err", `✖ [${j.source}] ${j.command}: ${j.error?.message}`);
        }),
        events.on("job.timeout", (e) => push("err", `⏱ ${(e.job as Job).error?.message}`)),
      );
    }
    return () => offs.forEach((o) => o());
  }, [showJobs]);

  const exec = async (line: string) => {
    if (!line.trim()) return;
    push("in", line);
    const h = [line, ...history.filter((x) => x !== line)].slice(0, 100);
    setHistory(h);
    setHpos(-1);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(h));
    } catch {
      /* ignore */
    }
    if (line.trim() === "clear") {
      setLines([]);
      return;
    }
    setBusy(true);
    try {
      const r = await api.post<{ output?: string[]; error?: { type: string; message: string; line?: number }; run?: string }>(
        "/api/engine/terminal",
        { line, project_id: projectId },
      );
      if (r.run) runIds.current.add(r.run);
      if (r.output?.length) push("out", r.output);
      if (r.error) push("err", `✖ ${r.error.type}: ${r.error.message}`);
    } catch (e) {
      push("err", `✖ ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const color: Record<Line["kind"], string> = {
    in: "text-cyan-300",
    out: "text-[var(--text)]",
    err: "text-rose-300",
    sys: "text-violet-300",
    job: "text-amber-200/80",
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-black/35 font-mono text-[12.5px]" onClick={() => document.getElementById(`term-${projectId}`)?.focus()} data-testid="terminal">
      <div ref={box} className="min-h-0 flex-1 overflow-auto p-2.5">
        {lines.map((l) => (
          <div key={l.id} className={`whitespace-pre-wrap break-words ${color[l.kind]}`}>
            {l.kind === "in" ? `mur> ${l.text}` : l.text}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 border-t border-white/10 px-2.5 py-1.5">
        <span className="text-cyan-300">{busy ? "…" : "mur>"}</span>
        <input
          id={`term-${projectId}`}
          className="min-w-0 flex-1 bg-transparent outline-none"
          value={input}
          spellCheck={false}
          autoComplete="off"
          aria-label="Команда терминала"
          placeholder='service load "https://example.com"'
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              const v = input;
              setInput("");
              exec(v);
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              const p = Math.min(hpos + 1, history.length - 1);
              if (p >= 0) {
                setHpos(p);
                setInput(history[p]);
              }
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              const p = hpos - 1;
              setHpos(Math.max(p, -1));
              setInput(p >= 0 ? history[p] : "");
            } else if (e.key === "l" && e.ctrlKey) {
              e.preventDefault();
              setLines([]);
            }
          }}
        />
      </div>
    </div>
  );
}
