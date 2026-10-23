import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { Desktop } from "./desktop/Desktop";
import { Login } from "./desktop/Login";
import { OSProvider } from "./desktop/os";
import { api, ApiError, events, type User } from "./lib/api";

function Boot() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [offline, setOffline] = useState<string | null>(null);

  const check = () => {
    setOffline(null);
    api
      .get<User>("/api/auth/me")
      .then(setUser)
      .catch((e: ApiError) => {
        if (e.status === 0) setOffline(e.message);
        setUser(null);
      });
  };
  useEffect(check, []);

  useEffect(() => {
    const theme = String(user?.settings?.theme ?? "neon");
    document.documentElement.dataset.theme = theme;
    const accent = user?.settings?.accent;
    if (typeof accent === "string" && /^#[0-9a-f]{6}$/i.test(accent)) document.documentElement.style.setProperty("--accent", accent);
    if (user) events.connect();
  }, [user]);

  if (user === undefined)
    return (
      <div className="grid h-full place-items-center">
        <img src="/murcloud.svg" className="h-20 w-20 animate-pulse" alt="MURCLOUD" />
      </div>
    );
  if (!user) return <Login onLogin={setUser} offline={offline} onRetry={check} />;
  return (
    <OSProvider initialUser={user} onLogout={() => setUser(null)}>
      <Desktop />
    </OSProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boot />
  </StrictMode>,
);
