import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import { Dialog } from "./Dialog";
import { Icon } from "./Icons";
import { formatDate } from "./format";
import type { InvitationIssued, ManagedUser, User, UserPage } from "./types";

export const roleLabels = { admin: "Администратор", manager: "Менеджер" };
type Action =
  | { kind: "create" }
  | { kind: "role" | "toggle" | "invite" | "reset"; user: ManagedUser };

export function UsersView({ currentUser }: { currentUser: User }) {
  const [page, setPage] = useState<UserPage>({ items: [], total: 0 });
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<User["role"]>("manager");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [issued, setIssued] = useState<InvitationIssued | null>(null);
  const [copied, setCopied] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      setPage(await api.users(offset));
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "Не удалось загрузить пользователей.",
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [offset]);

  function open(next: Action) {
    setAction(next);
    setIssued(null);
    setFormError("");
    setCopied(false);
    setUsername("");
    setRole(next.kind === "role" ? next.user.role : "manager");
  }
  function close() {
    setAction(null);
    setIssued(null);
    setFormError("");
    setCopied(false);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!action) return;
    setSaving(true);
    setFormError("");
    try {
      if (action.kind === "create")
        setIssued(await api.createUser(username.trim(), role));
      else if (action.kind === "invite")
        setIssued(await api.inviteUser(action.user.id));
      else if (action.kind === "reset")
        setIssued(await api.resetUserPassword(action.user.id));
      else {
        await api.updateUser(
          action.user.id,
          action.kind === "role"
            ? { role }
            : { is_active: !action.user.is_active },
        );
        close();
      }
      await load();
    } catch (error) {
      setFormError(
        error instanceof Error
          ? error.message
          : "Не удалось сохранить изменения.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function copyLink() {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.invitation_url);
      setCopied(true);
      setFormError("");
    } catch {
      setFormError("Не удалось скопировать. Выделите ссылку вручную.");
    }
  }

  const title = issued
    ? "Ссылка для установки пароля"
    : action?.kind === "create"
      ? "Новый пользователь"
      : action?.kind === "role"
        ? "Изменить роль"
        : action?.kind === "invite"
          ? "Создать новую ссылку"
          : action?.kind === "reset"
            ? "Сбросить пароль"
            : action?.kind === "toggle" && action.user.is_active
              ? "Отключить пользователя"
              : "Включить пользователя";

  return (
    <>
      <section
        className="card users-card"
        aria-labelledby="users-heading"
        aria-busy={loading}
      >
        <div className="card-heading users-heading">
          <h2 id="users-heading">Сотрудники</h2>
          <button
            className="button primary"
            disabled={saving}
            onClick={() => open({ kind: "create" })}
          >
            <Icon name="plus" size={16} /> Добавить пользователя
          </button>
        </div>
        {error ? (
          <div className="users-message">
            <p role="alert">{error}</p>
            <button className="button secondary" onClick={() => void load()}>
              Повторить
            </button>
          </div>
        ) : loading && !page.items.length ? (
          <p className="users-message" role="status">
            Загружаем пользователей…
          </p>
        ) : (
          <div className="users-table-scroll">
            <table className="users-table">
              <thead>
                <tr>
                  <th scope="col">Логин</th>
                  <th scope="col">Роль</th>
                  <th scope="col">Статус</th>
                  <th scope="col">Действия</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((user) => (
                  <tr key={user.id}>
                    <th scope="row">
                      <span className="user-name">{user.username}</span>
                      {user.id === currentUser.id && (
                        <span className="user-self">Вы</span>
                      )}
                    </th>
                    <td data-label="Роль">{roleLabels[user.role]}</td>
                    <td data-label="Статус">
                      <span
                        className={`badge ${!user.is_active ? "neutral" : user.password_set ? "green" : "purple"}`}
                      >
                        {!user.is_active
                          ? "Отключён"
                          : user.password_set
                            ? "Активен"
                            : "Ожидает установки пароля"}
                      </span>
                    </td>
                    <td
                      data-label="Действия"
                      className={
                        user.id === currentUser.id ? "self-actions" : ""
                      }
                    >
                      <div className="user-actions">
                        {user.id !== currentUser.id && (
                          <>
                            <button
                              className="text-button"
                              aria-label={`Изменить роль ${user.username}`}
                              disabled={saving || loading}
                              onClick={() => open({ kind: "role", user })}
                            >
                              Роль
                            </button>
                            {user.is_active && !user.password_set && (
                              <button
                                className="text-button"
                                aria-label={`Создать ссылку для ${user.username}`}
                                disabled={saving || loading}
                                onClick={() => open({ kind: "invite", user })}
                              >
                                Ссылка
                              </button>
                            )}
                            {user.is_active && user.password_set && (
                              <button
                                className="text-button"
                                aria-label={`Сбросить пароль ${user.username}`}
                                disabled={saving || loading}
                                onClick={() => open({ kind: "reset", user })}
                              >
                                Пароль
                              </button>
                            )}
                            <button
                              className={`text-button ${user.is_active ? "danger-text" : ""}`}
                              aria-label={`${user.is_active ? "Отключить" : "Включить"} ${user.username}`}
                              disabled={saving || loading}
                              onClick={() => open({ kind: "toggle", user })}
                            >
                              {user.is_active ? "Отключить" : "Включить"}
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!error && (
          <div className="users-pagination">
            <span>
              {page.total
                ? `${offset + 1}–${Math.min(offset + 20, page.total)} из ${page.total}`
                : "Нет пользователей"}
            </span>
            <div>
              <button
                className="button quiet"
                disabled={offset === 0 || loading || saving}
                onClick={() => setOffset((value) => Math.max(0, value - 20))}
              >
                Назад
              </button>
              <button
                className="button quiet"
                disabled={offset + 20 >= page.total || loading || saving}
                onClick={() => setOffset((value) => value + 20)}
              >
                Далее
              </button>
            </div>
          </div>
        )}
      </section>

      <Dialog
        title={title}
        open={action !== null}
        onClose={close}
        busy={saving}
      >
        {issued ? (
          <>
            <p className="dialog-description">
              Передайте ссылку пользователю{" "}
              <strong>{issued.user.username}</strong>. Он сам задаст пароль.
            </p>
            <label className="field-label" htmlFor="invitation-url">
              Одноразовая ссылка
            </label>
            <input
              id="invitation-url"
              value={issued.invitation_url}
              readOnly
              onFocus={(event) => event.target.select()}
            />
            <p className="invitation-expiry">
              Действует до {formatDate(issued.expires_at)}. Ссылка показывается
              только сейчас.
            </p>
            {formError && (
              <p className="connection-error" role="alert">
                {formError}
              </p>
            )}
            <div className="dialog-actions">
              <button className="button secondary" onClick={close}>
                Готово
              </button>
              <button
                className="button primary"
                onClick={() => void copyLink()}
              >
                {copied ? "Скопировано" : "Скопировать ссылку"}
              </button>
            </div>
          </>
        ) : (
          action && (
            <form onSubmit={submit}>
              {action.kind === "create" ? (
                <>
                  <label className="field-label" htmlFor="new-username">
                    Логин
                  </label>
                  <input
                    id="new-username"
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    required
                    minLength={3}
                    maxLength={64}
                    pattern="[a-zA-Z0-9][a-zA-Z0-9._\-]{2,63}"
                    value={username}
                    disabled={saving}
                    onChange={(event) => setUsername(event.target.value)}
                    aria-describedby="username-help"
                  />
                  <p className="field-help" id="username-help">
                    3–64 символа: латиница, цифры, точка, дефис или
                    подчёркивание.
                  </p>
                </>
              ) : (
                <p className="dialog-description">
                  Пользователь: <strong>{action.user.username}</strong>
                </p>
              )}
              {(action.kind === "create" || action.kind === "role") && (
                <>
                  <label className="field-label" htmlFor="user-role">
                    Роль
                  </label>
                  <select
                    id="user-role"
                    value={role}
                    disabled={saving}
                    onChange={(event) =>
                      setRole(event.target.value as User["role"])
                    }
                  >
                    <option value="manager">Менеджер</option>
                    <option value="admin">Администратор</option>
                  </select>
                  <p className="field-help">
                    {role === "admin"
                      ? "Управляет пользователями и обрабатывает заявки."
                      : "Обрабатывает заявки и подтверждает запись в CRM."}
                  </p>
                  {action.kind === "role" && (
                    <p className="field-help">
                      После изменения роли потребуется новый вход.
                    </p>
                  )}
                </>
              )}
              {action.kind === "toggle" && (
                <p className="dialog-description">
                  {action.user.is_active
                    ? "Пользователь потеряет доступ. Действующие ссылки для установки пароля перестанут работать."
                    : "Пользователь снова сможет войти. Если пароль ещё не установлен, создайте новую ссылку."}
                </p>
              )}
              {action.kind === "invite" && (
                <p className="dialog-description">
                  Предыдущая ссылка перестанет работать.
                </p>
              )}
              {action.kind === "reset" && (
                <p className="dialog-description">
                  Создадим одноразовую ссылку. Старый пароль действует до
                  установки нового. После смены пароля потребуется заново войти
                  на всех устройствах.
                </p>
              )}
              {formError && (
                <p className="connection-error" role="alert">
                  {formError}
                </p>
              )}
              <div className="dialog-actions">
                <button
                  type="button"
                  className="button secondary"
                  onClick={close}
                  disabled={saving}
                >
                  Отмена
                </button>
                <button
                  className="button primary"
                  disabled={
                    saving ||
                    (action.kind === "create" && username.trim().length < 3) ||
                    (action.kind === "role" && role === action.user.role)
                  }
                >
                  {saving
                    ? "Сохраняем…"
                    : action.kind === "create"
                      ? "Создать пользователя"
                      : action.kind === "role"
                        ? "Сохранить роль"
                        : action.kind === "invite" || action.kind === "reset"
                          ? "Создать ссылку"
                          : action.user.is_active
                            ? "Отключить"
                            : "Включить"}
                </button>
              </div>
            </form>
          )
        )}
      </Dialog>
    </>
  );
}
