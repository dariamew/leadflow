import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import { formatDate } from "./format";
import { Icon } from "./Icons";
import { SourceFieldView } from "./SourceFieldView";
import type { LeadJob, MonitorStatus, User } from "./types";

const statusLabels: Record<LeadJob["status"], string> = {
  queued: "В очереди",
  processing: "Анализируем",
  retry: "Ожидает повтора",
  done: "Готово",
  failed: "Ошибка",
};
const errors: Record<string, string> = {
  INVALID_RESPONSE:
    "Не удалось прочитать ответ Битрикс24. Следующая проверка выполнится автоматически.",
  PORTAL_CHANGED: "Портал изменился. Включите отслеживание заново.",
  OWNER_UNAVAILABLE:
    "Получатель отключён или не установил пароль. Включите аккаунт и повторите задачу.",
  LEAD_TEXT_UNAVAILABLE:
    "В поле заявки нет текста или текст превышает 20 000 символов.",
  EXTRACTION_FAILED: "Не удалось получить корректный результат анализа.",
  ACCESS_DENIED:
    "Нет доступа к лидам в Битрикс24. Обратитесь к администратору.",
  insufficient_scope:
    "Недостаточно прав для работы с Битрикс24. Обратитесь к администратору.",
  INVALID_CREDENTIALS:
    "Подключение к Битрикс24 недействительно. Обратитесь к администратору.",
  NOT_CONFIGURED: "Битрикс24 не подключён. Обратитесь к администратору.",
  TRANSPORT_ERROR: "Битрикс24 не ответил. Проверим снова автоматически.",
  QUERY_LIMIT_EXCEEDED:
    "Битрикс24 ограничил запросы. Проверим снова автоматически.",
};
function errorText(code: string) {
  return (
    errors[code] ||
    "Не удалось обработать заявку. Повторите попытку или обратитесь к администратору."
  );
}

export function MonitorView({
  user,
  onOpen,
  onSourceChange,
}: {
  user: User;
  onOpen: (id: string) => Promise<void>;
  onSourceChange?: (field: string) => void;
}) {
  const [data, setData] = useState<MonitorStatus | null>(null);
  const [owner, setOwner] = useState(user.id);
  const [interval, setIntervalSeconds] = useState(60);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    let loading = false;
    async function load(initial = false) {
      if (loading) return;
      loading = true;
      try {
        const next = await api.monitor();
        if (!active) return;
        setData(next);
        if (initial) {
          setOwner(next.owner_id || user.id);
          setIntervalSeconds(next.interval_seconds);
        }
      } catch (failure) {
        if (active)
          setError(
            failure instanceof Error
              ? failure.message
              : "Не удалось загрузить состояние.",
          );
      } finally {
        loading = false;
      }
    }
    void load(true);
    const timer = window.setInterval(() => {
      void load();
    }, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [user.id]);

  async function action(
    operation: () => Promise<MonitorStatus>,
    message: string,
  ) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setData(await operation());
      setNotice(message);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Не удалось сохранить настройки.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    await action(
      () =>
        api.configureMonitor({
          enabled: true,
          owner_id: owner,
          interval_seconds: interval,
        }),
      "Отслеживание включено.",
    );
  }
  const pending = data
    ? (data.counts.queued || 0) +
      (data.counts.retry || 0) +
      (data.counts.processing || 0)
    : 0;

  return (
    <div className="monitor-view">
      <SourceFieldView onSaved={onSourceChange} />
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="notice success" role="status">
          {notice}
        </div>
      )}
      {!data ? (
        <p role="status">Загружаем состояние…</p>
      ) : (
        <>
          <section className="card" aria-labelledby="monitor-settings">
            <div className="card-heading monitor-heading">
              <h2 id="monitor-settings">Новые лиды Битрикс24</h2>
              <span className={`badge ${data.enabled ? "green" : "neutral"}`}>
                {data.enabled ? "Включено" : "Выключено"}
              </span>
            </div>
            <div className="monitor-body">
              <p>
                Квалифицируем новые лиды и передадим результаты выбранному
                сотруднику.
              </p>
              <form onSubmit={submit}>
                <div className="monitor-fields">
                  <div>
                    <label className="field-label" htmlFor="monitor-owner">
                      Получатель результатов
                    </label>
                    <select
                      id="monitor-owner"
                      value={owner}
                      onChange={(event) => setOwner(event.target.value)}
                      disabled={busy}
                      required
                    >
                      <option value="">Выберите сотрудника</option>
                      {data.recipients.map((person) => (
                        <option key={person.id} value={person.id}>
                          {person.username}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="field-label" htmlFor="monitor-interval">
                      Проверять каждые
                    </label>
                    <select
                      id="monitor-interval"
                      value={interval}
                      onChange={(event) =>
                        setIntervalSeconds(Number(event.target.value))
                      }
                      disabled={busy}
                    >
                      <option value={30}>30 секунд</option>
                      <option value={60}>1 минуту</option>
                      <option value={300}>5 минут</option>
                      <option value={900}>15 минут</option>
                      {![30, 60, 300, 900].includes(interval) && (
                        <option value={interval}>{interval} секунд</option>
                      )}
                    </select>
                  </div>
                </div>
                <p className="monitor-hint">
                  При первом включении начнём с новых лидов. После паузы
                  обработаем пропущенные. Уже поставленные в очередь заявки
                  сохранят прежнего получателя.
                </p>
                {!data.bitrix_configured && (
                  <p className="connection-error">
                    Битрикс24 не подключён. Обратитесь к администратору.
                  </p>
                )}
                {!data.worker_available && (
                  <p className="connection-error">
                    Автообработка недоступна. Обратитесь к администратору.
                  </p>
                )}
                {data.last_error && (
                  <p className="connection-error" role="alert">
                    {errorText(data.last_error)}
                  </p>
                )}
                <div className="monitor-actions">
                  <button
                    className="button primary"
                    type="submit"
                    disabled={
                      busy ||
                      !owner ||
                      !data.bitrix_configured ||
                      !data.worker_available
                    }
                  >
                    {busy
                      ? "Сохраняем…"
                      : data.enabled
                        ? "Сохранить настройки"
                        : "Включить отслеживание"}
                  </button>
                  {data.enabled && (
                    <>
                      <button
                        className="button secondary"
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void action(
                            () =>
                              api.configureMonitor({
                                enabled: false,
                                owner_id: owner || null,
                                interval_seconds: interval,
                              }),
                            "Отслеживание приостановлено. Текущий анализ завершится.",
                          )
                        }
                      >
                        Приостановить
                      </button>
                      <button
                        className="text-button"
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void action(
                            () => api.checkLeads(),
                            "Проверка запланирована.",
                          )
                        }
                      >
                        Проверить сейчас
                      </button>
                    </>
                  )}
                </div>
              </form>
              <div className="input-footnote">
                <Icon name="shield" size={17} />
                <span>Запись в CRM — после подтверждения.</span>
              </div>
            </div>
          </section>
          <section className="card" aria-labelledby="monitor-jobs">
            <div className="card-heading">
              <h2 id="monitor-jobs">Обработка лидов</h2>
            </div>
            <div className="monitor-body">
              <dl className="monitor-stats">
                <div>
                  <dt>В очереди и работе</dt>
                  <dd>{pending}</dd>
                </div>
                <div>
                  <dt>Квалифицировано</dt>
                  <dd>{data.counts.done || 0}</dd>
                </div>
                <div>
                  <dt>С ошибкой</dt>
                  <dd>{data.counts.failed || 0}</dd>
                </div>
              </dl>
              <p className="monitor-hint">
                Последняя проверка:{" "}
                {data.last_poll_at
                  ? formatDate(data.last_poll_at)
                  : "ещё не выполнялась"}
                {data.enabled && !data.worker_recent
                  ? ". Ожидаем начала проверки."
                  : ""}
              </p>
            </div>
            {!data.jobs.length ? (
              <div className="monitor-empty">Новых лидов пока нет.</div>
            ) : (
              <ul className="monitor-jobs">
                {data.jobs.map((job) => (
                  <li key={job.id}>
                    <div className="monitor-job-title">
                      <strong>Лид №{job.lead_id}</strong>
                      <span
                        className={`badge ${job.status === "done" ? "green" : "neutral"}`}
                      >
                        {statusLabels[job.status]}
                      </span>
                    </div>
                    <div className="monitor-job-meta">
                      {job.owner_username || "Получатель удалён"} ·{" "}
                      {formatDate(job.created_at)}
                    </div>
                    {job.last_error && (
                      <p className="connection-error">
                        {errorText(job.last_error)}
                      </p>
                    )}
                    {job.run_id && (
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() => void onOpen(job.run_id!)}
                      >
                        Открыть результат <Icon name="arrow" size={15} />
                      </button>
                    )}
                    {job.status === "failed" && (
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() =>
                          void action(
                            () => api.retryLead(job.id),
                            "Заявка возвращена в очередь.",
                          )
                        }
                      >
                        Повторить
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
