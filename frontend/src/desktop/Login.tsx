import { motion } from "framer-motion";
import { useState, type FormEvent } from "react";
import { api, type User } from "../lib/api";

export function Login({ onLogin, offline, onRetry }: { onLogin: (u: User) => void; offline: string | null; onRetry: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = mode === "login" ? { username, password } : { username, password, display_name: displayName };
      const r = await api.post<{ user: User }>(`/api/auth/${mode}`, body);
      onLogin(r.user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wallpaper-galaxy stars relative grid h-full place-items-center overflow-auto p-4">
      <motion.form
        onSubmit={submit}
        initial={{ opacity: 0, y: 24, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        className="glass rounded-mur relative w-full max-w-sm p-7"
      >
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <motion.img src="/murcloud.svg" alt="" className="h-20 w-20" animate={{ y: [0, -6, 0] }} transition={{ repeat: Infinity, duration: 3 }} />
          <h1 className="text-2xl font-black tracking-tight">
            <span className="neon-text">MURCLOUD</span> 10 000 ULTRA
          </h1>
          <p className="text-xs muted">MURCLOUD OS 10.0.0 Beta · ARTYONIX STUDIOS</p>
        </div>

        {offline && (
          <div className="mb-4 rounded-xl border border-amber-400/40 bg-amber-400/10 p-3 text-sm">
            {offline}{" "}
            <button type="button" className="underline" onClick={onRetry}>
              Повторить
            </button>
          </div>
        )}

        <div className="mb-4 flex rounded-2xl glass-soft p-1">
          {(["login", "register"] as const).map((m) => (
            <button key={m} type="button" className={`tab flex-1 ${mode === m ? "tab-active" : ""}`} onClick={() => setMode(m)}>
              {m === "login" ? "Вход" : "Регистрация"}
            </button>
          ))}
        </div>

        <label className="mb-3 block text-xs muted">
          Имя пользователя
          <input className="input mt-1" name="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required minLength={3} />
        </label>
        {mode === "register" && (
          <label className="mb-3 block text-xs muted">
            Как вас называть
            <input className="input mt-1" name="display_name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Муся" />
          </label>
        )}
        <label className="mb-4 block text-xs muted">
          Пароль {mode === "register" && "(минимум 8 символов)"}
          <input
            className="input mt-1"
            type="password"
            name="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={mode === "register" ? 8 : 1}
          />
        </label>
        {error && (
          <div role="alert" className="mb-3 rounded-xl border border-rose-400/40 bg-rose-500/10 p-2.5 text-sm">
            {error}
          </div>
        )}
        <button className="btn btn-primary w-full !py-2.5" disabled={busy} type="submit">
          {busy ? "Подождите…" : mode === "login" ? "Войти в MURCLOUD" : "Создать аккаунт"}
        </button>
      </motion.form>
    </div>
  );
}
