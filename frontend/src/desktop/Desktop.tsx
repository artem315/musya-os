import { AnimatePresence, motion } from "framer-motion";
import { Bell, LogOut, Search, Wifi, WifiOff, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { APP_IDS, APPS, type AppId } from "../apps/registry";
import { events } from "../lib/api";
import { useOS } from "./os";
import { Window } from "./Window";

const DESKTOP_ICONS: AppId[] = ["studio", "ai", "browser", "drive", "terminal", "pc", "gaming", "rtx", "soup", "packages", "monitor", "settings"];

function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="text-right leading-tight">
      <div className="text-sm font-semibold">{now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</div>
      <div className="text-[10px] muted">{now.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}</div>
    </div>
  );
}

function ConnectionDot() {
  const [s, setS] = useState(events.status);
  useEffect(() => events.onStatus(setS), []);
  return s === "open" ? (
    <Wifi size={16} className="text-emerald-400" aria-label="Связь с сервером есть" />
  ) : (
    <WifiOff size={16} className="text-rose-400" aria-label="Нет связи с сервером" />
  );
}

function Launcher() {
  const os = useOS();
  const [query, setQuery] = useState("");
  const list = useMemo(
    () => APP_IDS.filter((id) => `${APPS[id].title} ${APPS[id].description}`.toLowerCase().includes(query.toLowerCase())),
    [query],
  );
  return (
    <AnimatePresence>
      {os.launcherOpen && (
        <motion.div className="fixed inset-0 z-[9000]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => os.setLauncherOpen(false)}>
          <motion.div
            className="glass rounded-mur absolute bottom-20 left-1/2 w-[min(640px,calc(100%-24px))] -translate-x-1/2 p-4"
            data-testid="launcher"
            initial={{ y: 30, scale: 0.97 }}
            animate={{ y: 0, scale: 1 }}
            exit={{ y: 30, scale: 0.97 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center gap-2">
              <Search size={18} className="muted" />
              <input
                autoFocus
                className="input"
                placeholder="Найти приложение… (Ctrl+K)"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && list[0]) {
                    os.open(list[0]);
                    os.setLauncherOpen(false);
                  }
                }}
              />
            </div>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {list.map((id) => {
                const a = APPS[id];
                return (
                  <button
                    key={id}
                    className="flex flex-col items-center gap-1.5 rounded-2xl p-3 text-center hover:bg-white/10"
                    onClick={() => {
                      os.open(id);
                      os.setLauncherOpen(false);
                    }}
                  >
                    <a.icon size={28} style={{ color: a.color }} />
                    <span className="text-xs font-medium leading-tight">{a.title}</span>
                  </button>
                );
              })}
            </div>
            <div className="mt-3 flex items-center justify-between border-t pt-3 text-xs muted" style={{ borderColor: "var(--panel-border)" }}>
              <span>
                {os.user.display_name} · MURCLOUD 10.0.0 Beta · ARTYONIX STUDIOS
              </span>
              <button className="btn btn-sm" onClick={() => os.logout()}>
                <LogOut size={14} /> Выйти
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Notifications({ open, onClose }: { open: boolean; onClose: () => void }) {
  const os = useOS();
  const toasts = os.notes.filter((n) => !n.read && Date.now() - n.ts < 6000).slice(0, 3);
  useEffect(() => {
    if (!toasts.length) return;
    const t = setTimeout(() => toasts.forEach((n) => os.dismiss(n.id)), 6000);
    return () => clearTimeout(t);
  }, [toasts, os]);
  const color = { info: "var(--accent-2)", ok: "var(--ok)", error: "var(--danger)", ai: "var(--accent)" };
  return (
    <>
      <div className="pointer-events-none fixed bottom-[76px] right-3 z-[9500] flex w-[min(340px,calc(100%-24px))] flex-col-reverse gap-2">
        <AnimatePresence>
          {!open &&
            toasts.map((n) => (
              <motion.div
                key={n.id}
                layout
                initial={{ opacity: 0, x: 40 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 40 }}
                className="glass pointer-events-auto rounded-2xl p-3"
                style={{ borderLeft: `3px solid ${color[n.kind]}` }}
                onClick={() => os.dismiss(n.id)}
              >
                <div className="text-sm font-semibold">{n.title}</div>
                {n.body && <div className="mt-0.5 line-clamp-3 text-xs muted">{n.body}</div>}
              </motion.div>
            ))}
        </AnimatePresence>
      </div>
      <AnimatePresence>
        {open && (
          <motion.aside
            initial={{ x: 380 }}
            animate={{ x: 0 }}
            exit={{ x: 380 }}
            className="glass fixed bottom-[72px] right-3 top-3 z-[9400] flex w-[min(360px,calc(100%-24px))] flex-col rounded-mur p-3"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="font-semibold">Уведомления</span>
              <div className="flex gap-1">
                <button className="btn btn-sm" onClick={os.clearNotes}>
                  Очистить
                </button>
                <button className="btn btn-sm" onClick={onClose} aria-label="Закрыть">
                  <X size={14} />
                </button>
              </div>
            </div>
            <div className="flex-1 space-y-2 overflow-auto">
              {os.notes.length === 0 && <div className="muted text-sm">Пока тихо. Мур.</div>}
              {os.notes.map((n) => (
                <div key={n.id} className="glass-soft rounded-xl p-2.5" style={{ borderLeft: `3px solid ${color[n.kind]}` }}>
                  <div className="text-sm font-medium">{n.title}</div>
                  {n.body && <div className="text-xs muted">{n.body}</div>}
                  <div className="mt-1 text-[10px] muted">{new Date(n.ts).toLocaleTimeString("ru-RU")}</div>
                </div>
              ))}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}

export function Desktop() {
  const os = useOS();
  const [notesOpen, setNotesOpen] = useState(false);
  const wallpaper = String(os.user.settings.wallpaper ?? "galaxy");
  const unread = os.notes.filter((n) => !n.read).length;

  return (
    <div className={`relative h-full w-full overflow-hidden wallpaper-${wallpaper} stars`}>
      {/* desktop icons */}
      <div className="absolute inset-0 bottom-16 grid auto-rows-[92px] grid-flow-col grid-rows-[repeat(auto-fill,92px)] content-start gap-1 p-3 sm:w-auto">
        {DESKTOP_ICONS.map((id) => {
          const a = APPS[id];
          return (
            <button
              key={id}
              className="flex w-[84px] flex-col items-center gap-1 rounded-2xl p-2 text-center hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onDoubleClick={() => os.open(id)}
              onClick={(e) => (os.isMobile || e.detail === 0 ? os.open(id) : undefined)}
              title={a.description}
            >
              <span className="grid h-12 w-12 place-items-center rounded-2xl glass-soft" style={{ boxShadow: `0 0 18px ${a.color}55` }}>
                <a.icon size={24} style={{ color: a.color }} />
              </span>
              <span className="line-clamp-2 text-[11px] font-medium leading-tight drop-shadow">{a.title.replace("MUR ", "")}</span>
            </button>
          );
        })}
      </div>

      <AnimatePresence>
        {os.windows.map((w) => (
          <Window key={w.id} win={w} />
        ))}
      </AnimatePresence>

      {/* taskbar */}
      <nav className="glass fixed bottom-2 left-2 right-2 z-[8000] flex h-14 items-center gap-1.5 rounded-mur px-2" aria-label="Панель задач">
        <button className="btn btn-primary h-10 !rounded-2xl !px-3" onClick={() => os.setLauncherOpen(!os.launcherOpen)} aria-label="Меню приложений">
          <img src="/murcloud.svg" alt="" className="h-6 w-6" />
          <span className="hidden font-bold sm:inline">MURCLOUD</span>
        </button>
        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {os.windows.map((w) => {
            const a = APPS[w.app];
            const active = os.activeId === w.id;
            return (
              <button
                key={w.id}
                className={`btn h-10 shrink-0 !rounded-2xl ${active ? "!bg-[color-mix(in_srgb,var(--accent)_30%,transparent)]" : "btn-ghost"} ${w.minimized ? "opacity-60" : ""}`}
                onClick={() => (active ? os.minimize(w.id) : os.focus(w.id))}
                title={w.title}
              >
                <a.icon size={16} style={{ color: a.color }} />
                <span className="hidden max-w-[140px] truncate md:inline">{w.title}</span>
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-3 pr-1">
          <ConnectionDot />
          <button className="relative btn btn-ghost !px-2" onClick={() => setNotesOpen((v) => !v)} aria-label="Уведомления">
            <Bell size={18} />
            {unread > 0 && <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-bold">{unread}</span>}
          </button>
          <Clock />
        </div>
      </nav>

      <Launcher />
      <Notifications open={notesOpen} onClose={() => setNotesOpen(false)} />
    </div>
  );
}
