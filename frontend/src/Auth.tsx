import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { api, ApiError, onUnauthorized } from "./api";
import { Icon } from "./Icons";
import { PasswordSetup } from "./PasswordSetup";
import type { User } from "./types";

export function AuthGate({
  children,
}: {
  children: (user: User, logout: () => Promise<void>) => ReactNode;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [restoreError, setRestoreError] = useState("");
  const [error, setError] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [invitation, setInvitation] = useState(() => readInvitation());
  const authChannel = useRef<BroadcastChannel | null>(null);

  function readInvitation() {
    return window.location.hash.startsWith("#set-password=")
      ? window.location.hash.slice(14)
      : null;
  }

  useEffect(() => {
    const update = () => setInvitation(readInvitation());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  useEffect(
    () =>
      onUnauthorized(() => {
        setUser(null);
        setPassword("");
        setError("Сессия завершена. Войдите снова.");
      }),
    [],
  );

  async function restore(showLoading = true) {
    if (showLoading) setChecking(true);
    setRestoreError("");
    try {
      setUser((await api.me()).user);
      setError("");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setUser(null);
        setError("");
      } else {
        setRestoreError(
          error instanceof Error ? error.message : "Не удалось проверить вход.",
        );
      }
    } finally {
      setChecking(false);
    }
  }
  useEffect(() => {
    void restore();
    // Cookies are shared across tabs. Drop cached workspace data when another
    // tab signs in or out, and recheck the session when returning to this tab.
    const channel =
      typeof window.BroadcastChannel === "function"
        ? new window.BroadcastChannel("leadflow-auth")
        : null;
    authChannel.current = channel;
    if (channel)
      channel.onmessage = () => {
        setUser(null);
        void restore();
      };
    const refresh = () => {
      void restore(false);
    };
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      channel?.close();
      authChannel.current = null;
    };
  }, []);

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const session = await api.login(username.trim(), password);
      setPassword("");
      setShowPassword(false);
      setUser(session.user);
      authChannel.current?.postMessage("changed");
    } catch (error) {
      setError(error instanceof Error ? error.message : "Не удалось войти.");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    await api.logout();
    authChannel.current?.postMessage("changed");
    setUser(null);
    setPassword("");
    setError("");
  }

  if (invitation !== null)
    return (
      <PasswordSetup
        token={invitation}
        onLogin={async (username) => {
          try {
            await api.me();
            await api.logout();
            authChannel.current?.postMessage("changed");
          } catch (error) {
            if (!(error instanceof ApiError && error.status === 401))
              throw error;
          }
          window.history.replaceState(
            null,
            "",
            window.location.pathname + window.location.search,
          );
          setUser(null);
          setUsername(username);
          setError("");
          setChecking(false);
          setRestoreError("");
          setInvitation(null);
        }}
      />
    );

  if (user) return children(user, logout);

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="login-heading">
        <div className="brand auth-brand">
          <span className="brand-logo">L</span>
          <span>leadflow</span>
        </div>
        {checking ? (
          <>
            <h1 id="login-heading">Вход в сервис</h1>
            <div role="status" className="auth-loading">
              <span className="spinner" /> Проверяем вход…
            </div>
          </>
        ) : restoreError ? (
          <>
            <h1 id="login-heading">Сервис недоступен</h1>
            <p className="connection-error" role="alert">
              {restoreError}
            </p>
            <button className="button primary" onClick={() => void restore()}>
              Повторить
            </button>
          </>
        ) : (
          <>
            <h1 id="login-heading">Вход в сервис</h1>
            <form onSubmit={login}>
              <label className="field-label" htmlFor="username">
                Логин
              </label>
              <input
                id="username"
                value={username}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                maxLength={64}
                disabled={busy}
                onChange={(event) => setUsername(event.target.value)}
              />
              <label className="field-label" htmlFor="password">
                Пароль
              </label>
              <div className="password-input">
                <input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  autoComplete="current-password"
                  required
                  maxLength={128}
                  disabled={busy}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  type="button"
                  className="text-button"
                  aria-label={
                    showPassword ? "Скрыть пароль" : "Показать пароль"
                  }
                  onClick={() => setShowPassword((value) => !value)}
                >
                  {showPassword ? "Скрыть" : "Показать"}
                </button>
              </div>
              {error && (
                <p className="connection-error" role="alert">
                  {error}
                </p>
              )}
              <button
                type="submit"
                className="button primary login-button"
                disabled={busy || !username.trim() || !password}
              >
                {busy ? (
                  <>
                    <span className="spinner" /> Входим…
                  </>
                ) : (
                  <>
                    Войти <Icon name="arrow" size={16} />
                  </>
                )}
              </button>
            </form>
          </>
        )}
      </section>
    </main>
  );
}
