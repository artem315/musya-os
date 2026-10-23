import { KeyRound, LogOut, Palette, ScrollText, ShieldCheck, Trash2, User as UserIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useOS } from "../desktop/os";
import { api } from "../lib/api";
import type { AppProps } from "./registry";

type Tab = "look" | "ai" | "account" | "audit";
type Providers = { providers: Record<string, { label: string; default_model: string }>; keys: { provider: string; hint: string; model: string }[]; server_key: boolean; server_provider: string | null };
type Sess = { id: string; current: boolean; last_seen: number; user_agent: string; ip: string };
type Audit = { id: number; ts: number; actor: string; event: string; tool: string; risk: string; ok: boolean; detail: Record<string, unknown> };

const ACCENTS = ["#a855f7", "#22d3ee", "#f472b6", "#34d399", "#f97316", "#facc15"];
const WALLPAPERS = ["galaxy", "aurora", "soup", "plain"];

export default function SettingsApp({ win }: AppProps) {
  const os = useOS();
  const [tab, setTab] = useState<Tab>((win.props.tab as Tab) ?? "look");
  useEffect(() => {
    if (win.props.tab) setTab(win.props.tab as Tab);
  }, [win.props.tab]);
  const tabs: [Tab, string, typeof Palette][] = [
    ["look", "Оформление", Palette],
    ["ai", "MUR AI", KeyRound],
    ["account", "Аккаунт", UserIcon],
    ["audit", "Безопасность и аудит", ScrollText],
  ];
  return (
    <div className="flex h-full min-h-0 flex-col sm:flex-row">
      <nav className="flex shrink-0 gap-1 overflow-x-auto border-b p-2 sm:w-52 sm:flex-col sm:border-b-0 sm:border-r" style={{ borderColor: "var(--panel-border)" }}>
        {tabs.map(([k, label, Icon]) => (
          <button key={k} className={`tab flex items-center gap-2 text-left ${tab === k ? "tab-active" : ""}`} onClick={() => setTab(k)}>
            <Icon size={14} /> {label}
          </button>
        ))}
        <button className="tab mt-auto flex items-center gap-2 text-left" onClick={() => os.logout()}>
          <LogOut size={14} /> Выйти
        </button>
      </nav>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {tab === "look" && <Look />}
        {tab === "ai" && <AISettings />}
        {tab === "account" && <Account />}
        {tab === "audit" && <AuditLog />}
      </div>
    </div>
  );
}

function Look() {
  const os = useOS();
  const s = os.user.settings;
  const set = (patch: Record<string, unknown>) => {
    os.updateSettings(patch).then(() => {
      if (patch.theme) document.documentElement.dataset.theme = String(patch.theme);
      if (patch.accent) document.documentElement.style.setProperty("--accent", String(patch.accent));
    });
  };
  return (
    <div className="max-w-xl space-y-5">
      <section>
        <h3 className="mb-2 font-semibold">Тема</h3>
        <div className="flex gap-2">
          {[
            ["neon", "Неон (тёмная)"],
            ["dark", "Тёмная"],
            ["light", "Светлая"],
          ].map(([k, label]) => (
            <button key={k} className={`btn ${s.theme === k ? "btn-primary" : ""}`} onClick={() => set({ theme: k })}>
              {label}
            </button>
          ))}
        </div>
      </section>
      <section>
        <h3 className="mb-2 font-semibold">Акцент</h3>
        <div className="flex gap-2">
          {ACCENTS.map((c) => (
            <button key={c} className="h-9 w-9 rounded-full border-2" style={{ background: c, borderColor: s.accent === c ? "#fff" : "transparent" }} onClick={() => set({ accent: c })} aria-label={c} />
          ))}
        </div>
      </section>
      <section>
        <h3 className="mb-2 font-semibold">Обои</h3>
        <div className="grid grid-cols-4 gap-2">
          {WALLPAPERS.map((w) => (
            <button key={w} className={`wallpaper-${w} h-16 rounded-2xl border-2`} style={{ borderColor: s.wallpaper === w ? "var(--accent)" : "transparent" }} onClick={() => set({ wallpaper: w })} aria-label={w} />
          ))}
        </div>
      </section>
      <section className="text-sm muted">
        Горячие клавиши: Ctrl+K / Alt+Space — меню, Alt+Q — закрыть окно, Alt+M — свернуть, Alt+Enter — развернуть, Alt+` — переключить окно, Ctrl+S /
        Ctrl+Enter — сохранить / запустить в Studio.
      </section>
    </div>
  );
}

function AISettings() {
  const os = useOS();
  const [p, setP] = useState<Providers | null>(null);
  const [provider, setProvider] = useState("anthropic");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(() => api.get<Providers>("/api/ai/providers").then(setP), []);
  useEffect(() => {
    load();
  }, [load]);
  const save = async () => {
    setMsg(null);
    try {
      await api.put("/api/ai/keys", { provider, api_key: key, model });
      setKey("");
      setMsg("Ключ сохранён (зашифрован на сервере).");
      load();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };
  return (
    <div className="max-w-xl space-y-5">
      <section>
        <h3 className="mb-1 font-semibold">Ключи API</h3>
        <p className="mb-3 text-xs muted">
          MUR AI работает через ваш ключ Anthropic, OpenAI или OpenRouter. Ключ шифруется (Fernet) и никогда не возвращается в браузер — показываются только
          последние 4 символа. Оплата запросов идёт по вашему ключу.
        </p>
        <div className="space-y-2">
          {p?.keys.map((k) => (
            <div key={k.provider} className="glass-soft flex items-center gap-2 rounded-2xl p-2.5 text-sm">
              <ShieldCheck size={16} className="text-emerald-300" />
              <b>{p.providers[k.provider]?.label ?? k.provider}</b> <span className="font-mono muted">{k.hint}</span>
              {k.model && <span className="chip">{k.model}</span>}
              <button className="btn btn-sm btn-danger ml-auto" onClick={() => api.del(`/api/ai/keys/${k.provider}`).then(load)}>
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {p?.server_key && <div className="text-xs muted">На сервере также настроен общий ключ для {p.server_provider}.</div>}
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-[150px_1fr]">
          <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {["anthropic", "openai", "openrouter"].map((k) => (
              <option key={k} value={k}>
                {p?.providers[k]?.label ?? k}
              </option>
            ))}
          </select>
          <input className="input font-mono" type="password" autoComplete="off" placeholder="sk-…" value={key} onChange={(e) => setKey(e.target.value)} />
          <span className="text-xs muted self-center">Модель (необязательно)</span>
          <input className="input font-mono" placeholder={p?.providers[provider]?.default_model} value={model} onChange={(e) => setModel(e.target.value)} />
        </div>
        <button className="btn btn-primary mt-2" disabled={key.length < 10} onClick={save}>
          Сохранить ключ
        </button>
        {msg && <div className="mt-2 text-sm">{msg}</div>}
      </section>
      <section>
        <h3 className="mb-2 font-semibold">Режим по умолчанию</h3>
        <select className="input !w-auto" value={String(os.user.settings.ai_mode ?? "agent")} onChange={(e) => os.updateSettings({ ai_mode: e.target.value })}>
          <option value="chat">Chat</option>
          <option value="code">Code</option>
          <option value="agent">Agent</option>
          <option value="auto">Auto</option>
        </select>
      </section>
    </div>
  );
}

function Account() {
  const os = useOS();
  const [name, setName] = useState(os.user.display_name);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Sess[]>([]);
  const load = useCallback(() => api.get<Sess[]>("/api/auth/sessions").then(setSessions), []);
  useEffect(() => {
    load();
  }, [load]);
  return (
    <div className="max-w-xl space-y-5">
      <section>
        <h3 className="mb-2 font-semibold">Профиль</h3>
        <div className="text-sm muted">Логин: {os.user.username}</div>
        <div className="mt-2 flex gap-2">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn" onClick={() => api.patch<typeof os.user>("/api/settings", { settings: {}, display_name: name }).then(os.setUser)}>
            Сохранить
          </button>
        </div>
      </section>
      <section>
        <h3 className="mb-2 font-semibold">Пароль</h3>
        <div className="grid gap-2">
          <input className="input" type="password" placeholder="Текущий пароль" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" />
          <input className="input" type="password" placeholder="Новый пароль (8+ символов)" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
          <button
            className="btn self-start"
            onClick={() =>
              api
                .post("/api/auth/password", { current_password: cur, new_password: next })
                .then(() => (setMsg("Пароль изменён, другие сеансы завершены."), setCur(""), setNext(""), load()))
                .catch((e) => setMsg(e.message))
            }
          >
            Сменить пароль
          </button>
          {msg && <div className="text-sm">{msg}</div>}
        </div>
      </section>
      <section>
        <h3 className="mb-2 font-semibold">Активные сеансы</h3>
        <div className="space-y-1.5">
          {sessions.map((s) => (
            <div key={s.id} className="glass-soft rounded-xl p-2 text-xs">
              <b>{s.current ? "Этот сеанс" : s.ip}</b> · {new Date(s.last_seen * 1000).toLocaleString("ru-RU")}
              <div className="truncate muted">{s.user_agent}</div>
            </div>
          ))}
        </div>
        <button className="btn btn-sm mt-2" onClick={() => api.post("/api/auth/sessions/revoke-others").then(load)}>
          Завершить другие сеансы
        </button>
      </section>
    </div>
  );
}

function AuditLog() {
  const [rows, setRows] = useState<Audit[]>([]);
  useEffect(() => {
    api.get<Audit[]>("/api/ai/audit").then(setRows);
  }, []);
  return (
    <div>
      <h3 className="mb-1 font-semibold">Журнал действий</h3>
      <p className="mb-3 text-xs muted">Все вызовы инструментов MUR AI, подтверждения, входы и изменения безопасности. Секреты вырезаются автоматически.</p>
      <table className="w-full text-xs">
        <thead className="muted">
          <tr className="text-left">
            <th className="py-1">Время</th>
            <th>Кто</th>
            <th>Событие</th>
            <th>Инструмент</th>
            <th>Риск</th>
            <th>OK</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-white/5 align-top" title={JSON.stringify(r.detail).slice(0, 600)}>
              <td className="py-1 pr-2 whitespace-nowrap">{new Date(r.ts * 1000).toLocaleString("ru-RU")}</td>
              <td className="pr-2">{r.actor}</td>
              <td className="pr-2">{r.event}</td>
              <td className="pr-2 font-mono">{r.tool}</td>
              <td className="pr-2">{r.risk}</td>
              <td>{r.ok ? "✔" : "✖"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
