import { motion } from "framer-motion";
import { Maximize2, Minimize2, Minus, X, ChevronLeft } from "lucide-react";
import { Suspense, useRef, type PointerEvent as RPointerEvent } from "react";
import { APPS } from "../apps/registry";
import { useOS, type Win } from "./os";

const MIN_W = 320;
const MIN_H = 220;
type Edge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

export function Window({ win }: { win: Win }) {
  const os = useOS();
  const def = APPS[win.app];
  const App = def.component;
  const drag = useRef<{ dx: number; dy: number } | null>(null);
  const rs = useRef<{ edge: Edge; x: number; y: number; w: number; h: number; px: number; py: number } | null>(null);
  const full = win.maximized || os.isMobile;
  const active = os.activeId === win.id;

  const onTitleDown = (e: RPointerEvent) => {
    os.focus(win.id);
    if (full || (e.target as HTMLElement).closest("button")) return;
    drag.current = { dx: e.clientX - win.x, dy: e.clientY - win.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onTitleMove = (e: RPointerEvent) => {
    if (!drag.current) return;
    const x = Math.min(Math.max(e.clientX - drag.current.dx, -win.w + 80), window.innerWidth - 80);
    const y = Math.min(Math.max(e.clientY - drag.current.dy, 0), window.innerHeight - 90);
    os.patch(win.id, { x, y });
  };
  const onTitleUp = () => (drag.current = null);

  const startResize = (edge: Edge) => (e: RPointerEvent) => {
    e.stopPropagation();
    os.focus(win.id);
    rs.current = { edge, x: win.x, y: win.y, w: win.w, h: win.h, px: e.clientX, py: e.clientY };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onResize = (e: RPointerEvent) => {
    const r = rs.current;
    if (!r) return;
    const dx = e.clientX - r.px;
    const dy = e.clientY - r.py;
    let { x, y, w, h } = r;
    if (r.edge.includes("e")) w = Math.max(MIN_W, r.w + dx);
    if (r.edge.includes("s")) h = Math.max(MIN_H, r.h + dy);
    if (r.edge.includes("w")) {
      w = Math.max(MIN_W, r.w - dx);
      x = r.x + (r.w - w);
    }
    if (r.edge.includes("n")) {
      h = Math.max(MIN_H, r.h - dy);
      y = Math.max(0, r.y + (r.h - h));
    }
    os.patch(win.id, { x, y, w, h });
  };
  const endResize = () => (rs.current = null);

  const style = full
    ? { left: 0, top: 0, width: "100%", height: os.isMobile ? "calc(100% - 64px)" : "calc(100% - 64px)", zIndex: win.z }
    : { left: win.x, top: win.y, width: win.w, height: win.h, zIndex: win.z };

  const edges: [Edge, string][] = [
    ["n", "top-0 left-3 right-3 h-1.5 cursor-n-resize"],
    ["s", "bottom-0 left-3 right-3 h-1.5 cursor-s-resize"],
    ["e", "right-0 top-3 bottom-3 w-1.5 cursor-e-resize"],
    ["w", "left-0 top-3 bottom-3 w-1.5 cursor-w-resize"],
    ["ne", "right-0 top-0 w-3 h-3 cursor-ne-resize"],
    ["nw", "left-0 top-0 w-3 h-3 cursor-nw-resize"],
    ["se", "right-0 bottom-0 w-4 h-4 cursor-se-resize"],
    ["sw", "left-0 bottom-0 w-3 h-3 cursor-sw-resize"],
  ];

  return (
    <motion.section
      role="dialog"
      aria-label={win.title}
      data-app={win.app}
      initial={{ opacity: 0, scale: 0.94, y: 16 }}
      animate={win.minimized ? { opacity: 0, scale: 0.85, y: 60, pointerEvents: "none" } : { opacity: 1, scale: 1, y: 0, pointerEvents: "auto" }}
      exit={{ opacity: 0, scale: 0.92 }}
      transition={{ type: "spring", stiffness: 420, damping: 34 }}
      className={`absolute flex flex-col overflow-hidden window-surface ${full ? (os.isMobile ? "" : "rounded-none") : "rounded-mur"}`}
      style={{ ...style, visibility: os.isMobile && !active ? "hidden" : undefined, boxShadow: active ? "0 0 0 1px color-mix(in srgb, var(--accent) 50%, transparent), var(--glow), 0 30px 80px rgba(0,0,0,.45)" : undefined }}
      onPointerDown={() => !active && os.focus(win.id)}
    >
      <header
        className="flex h-10 shrink-0 items-center gap-2 border-b px-3 select-none touch-none"
        style={{ borderColor: "var(--panel-border)" }}
        onPointerDown={onTitleDown}
        onPointerMove={onTitleMove}
        onPointerUp={onTitleUp}
        onDoubleClick={() => !os.isMobile && os.toggleMax(win.id)}
      >
        {os.isMobile && (
          <button className="btn btn-ghost btn-sm -ml-2" onClick={() => os.minimize(win.id)} aria-label="Назад">
            <ChevronLeft size={18} />
          </button>
        )}
        <def.icon size={16} style={{ color: def.color }} />
        <span className="truncate text-sm font-semibold">{win.title}</span>
        <div className="ml-auto flex items-center gap-1">
          {!os.isMobile && (
            <>
              <button className="btn btn-ghost btn-sm !px-1.5" aria-label="Свернуть" onClick={() => os.minimize(win.id)}>
                <Minus size={14} />
              </button>
              <button className="btn btn-ghost btn-sm !px-1.5" aria-label="Развернуть" onClick={() => os.toggleMax(win.id)}>
                {win.maximized ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
            </>
          )}
          <button className="btn btn-ghost btn-sm !px-1.5 hover:!bg-rose-500/70" aria-label="Закрыть" onClick={() => os.close(win.id)}>
            <X size={14} />
          </button>
        </div>
      </header>
      <div className="relative min-h-0 flex-1">
        <Suspense fallback={<div className="grid h-full place-items-center muted text-sm">Загрузка {def.title}…</div>}>
          <App win={win} />
        </Suspense>
      </div>
      {!full &&
        edges.map(([edge, cls]) => (
          <div key={edge} className={`absolute touch-none ${cls}`} onPointerDown={startResize(edge)} onPointerMove={onResize} onPointerUp={endResize} />
        ))}
    </motion.section>
  );
}
