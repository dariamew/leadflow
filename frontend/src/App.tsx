import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "./api";
import { AuthGate } from "./Auth";
import { Dialog } from "./Dialog";
import { Icon } from "./Icons";
import { categoryLabels, formatDate } from "./format";
import { ResultView } from "./ResultView";
import { MonitorView } from "./MonitorView";
import { UsersView, roleLabels } from "./UsersView";
import type {
  AppConfig,
  Attribute,
  BitrixTextField,
  Qualification,
  HistoryFilters,
  User,
} from "./types";

type Notice = { type: "error" | "success"; text: string };
const statusLabels: Record<Qualification["status"], string> = {
  pending_review: "На проверке",
  applying: "Идёт запись",
  applied: "Записано в CRM",
  apply_unknown: "Нужна сверка",
};

export function App() {
  return (
    <AuthGate>
      {(user, logout) => (
        <Workspace
          key={`${user.id}:${user.role}`}
          user={user}
          onLogout={logout}
        />
      )}
    </AuthGate>
  );
}

function Workspace({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => Promise<void>;
}) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [view, setView] = useState<
    "lead" | "result" | "history" | "users" | "monitor"
  >("history");
  const [leadId, setLeadId] = useState("");
  const [leadTextField, setLeadTextField] = useState("");
  const [leadFields, setLeadFields] = useState<BitrixTextField[]>([]);
  const [fieldsLoading, setFieldsLoading] = useState(false);
  const [fieldsError, setFieldsError] = useState("");
  const [fieldsRefresh, setFieldsRefresh] = useState(0);
  const [run, setRun] = useState<Qualification | null>(null);
  const [history, setHistory] = useState<Qualification[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyFilters, setHistoryFilters] = useState<HistoryFilters>({});
  const [historyLeadId, setHistoryLeadId] = useState("");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyPriority, setHistoryPriority] = useState("");
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyHasNext, setHistoryHasNext] = useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [busy, setBusy] = useState<
    "logout" | "qualify" | "refresh" | "apply" | null
  >("refresh");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [uncertain, setUncertain] = useState(false);

  function remember(result: Qualification) {
    setRun(result);
  }
  function resetResult() {
    setRun(null);
    setNotice(null);
    setUncertain(false);
  }
  function openSettings() {
    setSettingsOpen(true);
  }
  async function loadWorkspace(refreshHistory = true) {
    setBusy("refresh");
    setNotice(null);
    if (refreshHistory) setHistoryRevision((value) => value + 1);
    try {
      setConfig(await api.config());
    } catch (error) {
      setNotice({
        type: "error",
        text:
          error instanceof Error
            ? error.message
            : "Не удалось загрузить данные.",
      });
    } finally {
      setBusy(null);
    }
  }
  useEffect(() => {
    void loadWorkspace(false);
  }, []);

  useEffect(() => {
    if (view !== "lead" || !config?.bitrix_configured) return;
    let active = true;
    setFieldsLoading(true);
    setFieldsError("");
    void api
      .leadTextFields()
      .then((fields) => {
        if (active) setLeadFields(fields);
      })
      .catch((error) => {
        if (active)
          setFieldsError(
            error instanceof Error
              ? error.message
              : "Не удалось загрузить поля.",
          );
      })
      .finally(() => {
        if (active) setFieldsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [view, config?.bitrix_configured, fieldsRefresh]);

  useEffect(() => {
    if (view !== "history") return;
    let active = true;
    let inFlight = false;
    async function load(background = false) {
      if (inFlight) return;
      inFlight = true;
      if (!background) {
        setHistoryLoading(true);
        setHistoryLoaded(false);
      }
      try {
        const records = await api.history(historyFilters, historyOffset);
        if (!active) return;
        setHistory(records.slice(0, 30));
        setHistoryHasNext(records.length > 30);
        setHistoryLoaded(true);
      } catch (error) {
        if (active && !background)
          setNotice({
            type: "error",
            text:
              error instanceof Error
                ? error.message
                : "Не удалось загрузить результаты.",
          });
      } finally {
        inFlight = false;
        if (active && !background) setHistoryLoading(false);
      }
    }
    void load();
    const timer = window.setInterval(() => void load(true), 15000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [view, historyFilters, historyOffset, historyRevision]);

  function filterHistory(event: FormEvent) {
    event.preventDefault();
    const id = historyLeadId.trim();
    if (
      id &&
      (!/^\d+$/.test(id) ||
        !Number.isSafeInteger(Number(id)) ||
        Number(id) <= 0)
    ) {
      setNotice({
        type: "error",
        text: "Введите положительный числовой ID лида.",
      });
      return;
    }
    setNotice(null);
    setHistoryOffset(0);
    setHistoryFilters({
      lead_id: id ? Number(id) : undefined,
      status: (historyStatus || undefined) as HistoryFilters["status"],
      priority: (historyPriority || undefined) as HistoryFilters["priority"],
    });
  }

  function resetHistoryFilters() {
    setHistoryLeadId("");
    setHistoryStatus("");
    setHistoryPriority("");
    setHistoryOffset(0);
    setHistoryFilters({});
    setNotice(null);
  }

  const historyFiltered = Boolean(
    historyFilters.lead_id || historyFilters.status || historyFilters.priority,
  );

  const defaultLeadField = leadFields.find(
    (field) => field.code === config?.bitrix_text_field,
  );

  async function logout() {
    setBusy("logout");
    try {
      await onLogout();
    } catch (error) {
      setNotice({
        type: "error",
        text: error instanceof Error ? error.message : "Не удалось выйти.",
      });
      setSettingsOpen(false);
    } finally {
      setBusy(null);
    }
  }
  async function qualify(event: FormEvent) {
    event.preventDefault();
    if (
      !/^\d+$/.test(leadId) ||
      !Number.isSafeInteger(Number(leadId)) ||
      Number(leadId) <= 0
    ) {
      setNotice({
        type: "error",
        text: "Введите положительный числовой ID лида.",
      });
      return;
    }
    await analyzeLead(Number(leadId), leadTextField || undefined);
  }
  async function analyzeLead(id: number, field?: string) {
    setBusy("qualify");
    resetResult();
    try {
      remember(await api.qualifyLead(id, field));
    } catch (error) {
      setNotice({
        type: "error",
        text:
          error instanceof Error
            ? error.message
            : "Не удалось выполнить анализ.",
      });
    } finally {
      setBusy(null);
    }
  }
  async function refresh() {
    if (!run) return;
    setBusy("refresh");
    setNotice(null);
    try {
      remember(await api.get(run.id));
      setUncertain(false);
    } catch (error) {
      setNotice({
        type: "error",
        text: error instanceof Error ? error.message : "Не удалось обновить.",
      });
    } finally {
      setBusy(null);
    }
  }
  async function apply(attributes: Attribute[], overwrite: boolean) {
    if (!run) return;
    setBusy("apply");
    setNotice(null);
    try {
      remember(await api.apply(run.id, attributes, overwrite));
      setUncertain(false);
      setNotice({
        type: "success",
        text: "Выбранные поля записаны в Битрикс24.",
      });
    } catch (error) {
      const unknown =
        error instanceof ApiError &&
        (error.status === 0 || error.status >= 500 || error.outcomeUnknown);
      setUncertain(unknown);
      setNotice({
        type: "error",
        text: unknown
          ? "Нет подтверждения записи. Проверьте поля в CRM и обновите состояние."
          : error instanceof Error
            ? error.message
            : "Запись не выполнена.",
      });
      try {
        remember(await api.get(run.id));
      } catch {
        /* Keep the last snapshot and block uncertain writes. */
      }
    } finally {
      setBusy(null);
    }
  }
  async function openRecord(record: Pick<Qualification, "id">) {
    setBusy("refresh");
    setNotice(null);
    try {
      const fresh = await api.get(record.id);
      remember(fresh);
      setUncertain(false);
      setView("result");
      setLeadId(fresh.lead_id !== null ? String(fresh.lead_id) : "");
      setLeadTextField(
        fresh.source_field_explicit ? fresh.source_text_field || "" : "",
      );
    } catch (error) {
      setNotice({
        type: "error",
        text:
          error instanceof Error
            ? error.message
            : "Не удалось открыть результат.",
      });
    } finally {
      setBusy(null);
    }
  }
  function newRequest() {
    setView("lead");
    setLeadId("");
    setLeadTextField("");
    resetResult();
  }
  function showHistory() {
    setView("history");
    setNotice(null);
  }
  async function repeatAnalysis() {
    if (run?.lead_id == null) return;
    const id = run.lead_id;
    const field = run.source_field_explicit
      ? run.source_text_field || undefined
      : undefined;
    setView("lead");
    setLeadId(String(id));
    setLeadTextField(field || "");
    await analyzeLead(id, field);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            if (!busy) showHistory();
          }}
        >
          <span className="brand-logo">L</span>
          <span>leadflow</span>
        </a>
        <nav aria-label="Основная навигация">
          <button
            className={`nav-item ${view === "history" || view === "result" ? "active" : ""}`}
            disabled={Boolean(busy)}
            onClick={showHistory}
          >
            <Icon name="history" size={19} />
            История
          </button>
          <button
            className={`nav-item ${view === "lead" ? "active" : ""}`}
            disabled={Boolean(busy)}
            onClick={newRequest}
          >
            <Icon name="link" size={19} /> Анализ по ID
          </button>
          {user.role === "admin" && (
            <>
              <button
                className={`nav-item ${view === "users" ? "active" : ""}`}
                disabled={Boolean(busy)}
                onClick={() => {
                  setView("users");
                  setNotice(null);
                }}
              >
                <Icon name="users" size={19} /> Пользователи
              </button>
              <button
                className={`nav-item ${view === "monitor" ? "active" : ""}`}
                disabled={Boolean(busy)}
                onClick={() => {
                  setView("monitor");
                  setNotice(null);
                }}
              >
                <Icon name="refresh" size={19} /> Автообработка
              </button>
            </>
          )}
        </nav>
        <div className="sidebar-bottom">
          <button
            className="nav-item settings-link"
            onClick={openSettings}
            disabled={Boolean(busy)}
          >
            <Icon name="settings" size={18} />
            Аккаунт
            <Icon name="chevron" size={14} />
          </button>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <span className="analysis-mode">Квалификация заявок</span>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <h1>
                {view === "lead"
                  ? "Анализ по ID"
                  : view === "result"
                    ? "Результат квалификации"
                    : view === "users"
                      ? "Пользователи"
                      : view === "monitor"
                        ? "Автообработка"
                        : "История квалификаций"}
              </h1>
              {view === "history" && (
                <p>
                  {user.role === "admin" ? "Все сотрудники" : "Мои результаты"}
                </p>
              )}
            </div>
            {view === "history" ? (
              <div className="page-actions">
                <button
                  className="button secondary"
                  disabled={Boolean(busy) || historyLoading}
                  onClick={() => void loadWorkspace()}
                >
                  <Icon name="refresh" size={16} /> Обновить
                </button>
                <button
                  className="button primary"
                  disabled={Boolean(busy)}
                  onClick={newRequest}
                >
                  <Icon name="plus" size={17} />
                  Загрузить лид
                </button>
              </div>
            ) : view === "result" ? (
              <div className="page-actions">
                <button
                  className="button secondary"
                  disabled={Boolean(busy)}
                  onClick={showHistory}
                >
                  К истории
                </button>
                {run?.lead_id != null && (
                  <button
                    className="button primary"
                    disabled={Boolean(busy)}
                    onClick={() => void repeatAnalysis()}
                  >
                    <Icon name="refresh" size={16} /> Повторить анализ
                  </button>
                )}
              </div>
            ) : view === "lead" ? (
              <button
                className="button secondary"
                disabled={Boolean(busy)}
                onClick={showHistory}
              >
                К истории
              </button>
            ) : null}
          </div>
          {notice && (
            <div
              className={`notice ${notice.type}`}
              role={notice.type === "error" ? "alert" : "status"}
            >
              <Icon
                name={notice.type === "success" ? "check" : "info"}
                size={18}
              />
              <span>{notice.text}</span>
              <button
                className="icon-button"
                aria-label="Скрыть уведомление"
                onClick={() => setNotice(null)}
              >
                <Icon name="close" size={16} />
              </button>
            </div>
          )}
          {!config && !busy && view !== "history" && (
            <div className="connection-banner">
              <span className="section-icon">
                <Icon name="link" size={19} />
              </span>
              <div>
                <p>Не удалось загрузить настройки.</p>
              </div>
              <button
                className="button secondary"
                onClick={() => void loadWorkspace()}
              >
                Повторить
                <Icon name="arrow" size={16} />
              </button>
            </div>
          )}

          {view === "monitor" && user.role === "admin" ? (
            <MonitorView
              user={user}
              onOpen={(id) => openRecord({ id })}
              onSourceChange={(field) =>
                setConfig((current) =>
                  current ? { ...current, bitrix_text_field: field } : current,
                )
              }
            />
          ) : view === "users" && user.role === "admin" ? (
            <UsersView currentUser={user} />
          ) : view === "lead" || view === "result" ? (
            <div
              className={`workspace-grid ${view === "result" ? "result-workspace" : ""}`}
            >
              {view === "lead" && (
                <section
                  className="card input-card"
                  aria-labelledby="input-heading"
                >
                  <div className="card-heading">
                    <h2 id="input-heading">Лид Битрикс24</h2>
                  </div>
                  <div className="input-body">
                    <form onSubmit={qualify}>
                      <div className="lead-input">
                        <label htmlFor="lead-id">ID лида в Битрикс24</label>
                        <input
                          id="lead-id"
                          type="text"
                          inputMode="numeric"
                          pattern="[0-9]+"
                          value={leadId}
                          required
                          disabled={Boolean(busy)}
                          onChange={(event) => {
                            setLeadId(event.target.value);
                            resetResult();
                          }}
                          placeholder="Введите ID лида"
                        />
                        <div className="lead-source-picker">
                          <div className="input-label">
                            <label htmlFor="lead-text-field">
                              Поле с текстом заявки
                            </label>
                            <button
                              type="button"
                              className="text-button"
                              disabled={
                                Boolean(busy) ||
                                fieldsLoading ||
                                !config?.bitrix_configured
                              }
                              onClick={() =>
                                setFieldsRefresh((value) => value + 1)
                              }
                            >
                              {fieldsLoading ? "Загружаем…" : "Обновить поля"}
                            </button>
                          </div>
                          <select
                            id="lead-text-field"
                            value={leadTextField}
                            disabled={
                              Boolean(busy) ||
                              fieldsLoading ||
                              !config?.bitrix_configured
                            }
                            onChange={(event) => {
                              setLeadTextField(event.target.value);
                              resetResult();
                            }}
                          >
                            <option value="">
                              По умолчанию
                              {defaultLeadField
                                ? ` — ${defaultLeadField.title}`
                                : ""}
                            </option>
                            {leadTextField &&
                              !leadFields.some(
                                (field) => field.code === leadTextField,
                              ) && (
                                <option value={leadTextField}>
                                  Сохранённое поле недоступно
                                </option>
                              )}
                            {leadFields.map((field) => (
                              <option key={field.code} value={field.code}>
                                {field.title}
                              </option>
                            ))}
                          </select>
                          <p>Выбор действует только для этого анализа.</p>
                          {fieldsError && (
                            <p className="connection-error" role="alert">
                              {fieldsError}
                            </p>
                          )}
                        </div>
                        {config && !config.bitrix_configured && (
                          <div className="callout warning">
                            <Icon name="info" size={17} />
                            Битрикс24 не подключён. Обратитесь к администратору.
                          </div>
                        )}
                      </div>
                      <button
                        className="button primary analyze-button"
                        type="submit"
                        disabled={Boolean(busy) || !leadId.trim()}
                      >
                        {busy === "qualify" ? (
                          <>
                            <span className="spinner" />
                            Анализируем заявку…
                          </>
                        ) : (
                          <>
                            <Icon name="spark" size={18} />
                            {run?.lead_id === Number(leadId)
                              ? "Повторить анализ"
                              : "Анализировать лид"}
                            <Icon name="arrow" size={17} />
                          </>
                        )}
                      </button>
                    </form>
                    <div className="input-footnote">
                      <Icon name="shield" size={17} />
                      <span>Запись в CRM — после подтверждения.</span>
                    </div>
                  </div>
                </section>
              )}

              <section
                className={`card result-card ${!run ? "is-empty" : ""}`}
                aria-labelledby="result-heading"
                aria-busy={busy === "qualify"}
              >
                <div className="card-heading">
                  <h2 id="result-heading">Результат</h2>
                  {run && (
                    <span className="badge green">
                      <Icon name="check" size={12} />
                      Готово
                    </span>
                  )}
                </div>
                {run ? (
                  <ResultView
                    key={run.id}
                    run={run}
                    busy={Boolean(busy)}
                    uncertain={uncertain}
                    onApply={apply}
                    onRefresh={refresh}
                  />
                ) : (
                  <div className="empty-result">
                    <span className="empty-symbol">
                      {busy === "qualify" ? (
                        <span className="spinner" />
                      ) : (
                        <Icon name="document" size={24} />
                      )}
                    </span>
                    <h3>
                      {busy === "qualify"
                        ? "Анализируем заявку"
                        : "Нет результата"}
                    </h3>
                    <p>
                      {busy === "qualify"
                        ? "Проверяем данные и цитаты."
                        : "Укажите ID лида и запустите анализ."}
                    </p>
                  </div>
                )}
              </section>
            </div>
          ) : (
            <section className="card history-card">
              <div className="card-heading">
                <Icon name="history" size={19} />
                <h2>Результаты</h2>
              </div>
              <form
                className="history-filters"
                onSubmit={filterHistory}
                aria-label="Фильтры истории"
              >
                <div className="history-filter-field">
                  <label htmlFor="history-lead-id">ID лида</label>
                  <input
                    id="history-lead-id"
                    inputMode="numeric"
                    type="text"
                    placeholder="Введите ID лида"
                    value={historyLeadId}
                    onChange={(event) => setHistoryLeadId(event.target.value)}
                  />
                </div>
                <div className="history-filter-field">
                  <label htmlFor="history-status">Статус</label>
                  <select
                    id="history-status"
                    value={historyStatus}
                    onChange={(event) => setHistoryStatus(event.target.value)}
                  >
                    <option value="">Все статусы</option>
                    {Object.entries(statusLabels).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="history-filter-field">
                  <label htmlFor="history-priority">Приоритет</label>
                  <select
                    id="history-priority"
                    value={historyPriority}
                    onChange={(event) => setHistoryPriority(event.target.value)}
                  >
                    <option value="">Любой приоритет</option>
                    <option value="normal">Обычный</option>
                    <option value="elevated">Повышенный</option>
                  </select>
                </div>
                <div className="history-filter-actions">
                  <button
                    className="button primary"
                    type="submit"
                    disabled={Boolean(busy)}
                  >
                    Найти
                  </button>
                  <button
                    className="button secondary"
                    type="button"
                    onClick={resetHistoryFilters}
                    disabled={
                      Boolean(busy) ||
                      !(
                        historyFiltered ||
                        historyLeadId ||
                        historyStatus ||
                        historyPriority
                      )
                    }
                  >
                    Сбросить
                  </button>
                </div>
              </form>
              {!historyLoaded ? (
                <div className="history-empty">
                  {historyLoading ? (
                    <p role="status">Загружаем результаты…</p>
                  ) : (
                    <>
                      <p>Не удалось загрузить результаты.</p>
                      <button
                        className="button secondary"
                        onClick={() => void loadWorkspace()}
                      >
                        Повторить
                      </button>
                    </>
                  )}
                </div>
              ) : !history.length ? (
                <div className="history-empty">
                  <Icon name="history" size={32} />
                  <h3>
                    {historyFiltered
                      ? "Ничего не найдено"
                      : historyOffset
                        ? "На этой странице нет результатов"
                        : "Результатов пока нет"}
                  </h3>
                  <p>
                    {historyFiltered
                      ? "Измените ID лида или фильтры."
                      : historyOffset
                        ? "Вернитесь на предыдущую страницу."
                        : "Загрузите лид по ID или дождитесь автообработки."}
                  </p>
                </div>
              ) : (
                <div className="history-list">
                  {history.map((item) => (
                    <button
                      className="history-row"
                      key={item.id}
                      disabled={Boolean(busy)}
                      onClick={() => void openRecord(item)}
                    >
                      <span className="history-doc">
                        <Icon name="document" size={20} />
                      </span>
                      <span className="history-description">
                        <strong>
                          {item.lead_id !== null
                            ? `Лид №${item.lead_id}`
                            : categoryLabels[item.result.facts.category]}
                        </strong>
                        <span>{item.source_text}</span>
                        {user.role === "admin" && (
                          <span>
                            {item.created_by_username
                              ? item.trigger === "automatic"
                                ? `Авто → ${item.created_by_username}`
                                : `Автор: ${item.created_by_username}`
                              : "Автор не указан"}
                          </span>
                        )}
                      </span>
                      <span className="history-badges">
                        <span
                          className={`badge ${item.status === "applied" ? "green" : "neutral"}`}
                        >
                          {statusLabels[item.status] || item.status}
                        </span>
                        <span
                          className={`badge ${item.result.priority === "elevated" ? "amber" : "neutral"}`}
                        >
                          {item.result.priority === "elevated"
                            ? "Повышенный"
                            : "Обычный"}
                        </span>
                      </span>
                      <time>{formatDate(item.created_at)}</time>
                      <Icon name="chevron" size={16} />
                    </button>
                  ))}
                </div>
              )}
              {historyLoaded && (historyOffset > 0 || historyHasNext) && (
                <div
                  className="history-pagination"
                  aria-label="Страницы истории"
                >
                  <span>Страница {historyOffset / 30 + 1}</span>
                  <div>
                    <button
                      className="button secondary"
                      disabled={
                        Boolean(busy) || historyLoading || historyOffset === 0
                      }
                      onClick={() =>
                        setHistoryOffset((value) => Math.max(0, value - 30))
                      }
                    >
                      Назад
                    </button>
                    <button
                      className="button secondary"
                      disabled={
                        Boolean(busy) || historyLoading || !historyHasNext
                      }
                      onClick={() => setHistoryOffset((value) => value + 30)}
                    >
                      Далее
                    </button>
                  </div>
                </div>
              )}
            </section>
          )}
        </main>
      </div>

      <Dialog
        title="Аккаунт"
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        busy={busy === "logout"}
      >
        <dl className="account-details">
          <div>
            <dt>Роль</dt>
            <dd>{roleLabels[user.role]}</dd>
          </div>
          <div>
            <dt>Логин</dt>
            <dd>{user.username}</dd>
          </div>
          <div>
            <dt>Битрикс24</dt>
            <dd>
              {config
                ? config.bitrix_configured
                  ? "Подключён"
                  : "Не настроен"
                : "Загружаем…"}
            </dd>
          </div>
        </dl>
        <div className="dialog-actions">
          <button
            className="button secondary"
            onClick={() => void logout()}
            disabled={Boolean(busy)}
          >
            {busy === "logout" ? (
              <span className="spinner" />
            ) : (
              <Icon name="logout" size={16} />
            )}
            Выйти
          </button>
        </div>
      </Dialog>
    </div>
  );
}
