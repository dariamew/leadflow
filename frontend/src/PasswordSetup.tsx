import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import type { InvitationInfo } from "./types";

export function PasswordSetup({
  token,
  onLogin,
}: {
  token: string;
  onLogin: (username: string) => Promise<void>;
}) {
  const [info, setInfo] = useState<InvitationInfo | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function check() {
    setChecking(true);
    setInfo(null);
    setDone(false);
    setPassword("");
    setConfirmation("");
    setError("");
    try {
      setInfo(await api.checkInvitation(token));
    } catch (error) {
      setError(
        error instanceof Error ? error.message : "Не удалось проверить ссылку.",
      );
    } finally {
      setChecking(false);
    }
  }
  useEffect(() => {
    void check();
  }, [token]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirmation) {
      setError("Пароли не совпадают.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.acceptInvitation(token, password);
      setPassword("");
      setConfirmation("");
      setDone(true);
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "Не удалось установить пароль.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function goToLogin() {
    setBusy(true);
    setError("");
    try {
      await onLogin(info?.username || "");
    } catch (error) {
      setError(
        error instanceof Error ? error.message : "Не удалось открыть вход.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="setup-heading">
        <div className="brand auth-brand">
          <span className="brand-logo">L</span>
          <span>leadflow</span>
        </div>
        <h1 id="setup-heading">
          {done ? "Пароль установлен" : "Установите пароль"}
        </h1>
        {checking ? (
          <p role="status">Проверяем ссылку…</p>
        ) : done ? (
          <>
            <p className="auth-description">
              Войдите с логином {info?.username} и новым паролем.
            </p>
            {error && (
              <p role="alert" className="connection-error">
                {error}
              </p>
            )}
            <button
              className="button primary login-button"
              disabled={busy}
              onClick={() => void goToLogin()}
            >
              Перейти ко входу
            </button>
          </>
        ) : !info ? (
          <>
            <p role="alert" className="connection-error">
              {error}
            </p>
            <button
              className="button secondary login-button"
              onClick={() => void check()}
            >
              Проверить снова
            </button>
            <button
              className="text-button setup-back"
              onClick={() => void goToLogin()}
            >
              Перейти ко входу
            </button>
          </>
        ) : (
          <>
            <form onSubmit={submit}>
              <label className="field-label" htmlFor="setup-username">
                Логин
              </label>
              <input
                id="setup-username"
                name="username"
                autoComplete="username"
                readOnly
                value={info.username}
              />
              <label className="field-label" htmlFor="new-password">
                Новый пароль
              </label>
              <input
                id="new-password"
                name="password"
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                required
                value={password}
                disabled={busy}
                onChange={(event) => setPassword(event.target.value)}
                aria-describedby="password-help"
              />
              <p className="password-help" id="password-help">
                От 12 до 128 символов.
              </p>
              <label className="field-label" htmlFor="confirm-password">
                Повторите пароль
              </label>
              <input
                id="confirm-password"
                name="password-confirmation"
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                required
                value={confirmation}
                disabled={busy}
                onChange={(event) => setConfirmation(event.target.value)}
              />
              {error && (
                <p role="alert" className="connection-error">
                  {error}
                </p>
              )}
              <button
                className="button primary login-button"
                disabled={
                  busy || password.length < 12 || confirmation.length < 12
                }
              >
                {busy ? "Сохраняем…" : "Установить пароль"}
              </button>
            </form>
          </>
        )}
      </section>
    </main>
  );
}
