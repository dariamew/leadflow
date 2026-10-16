import { useState } from "react";
import { Dialog } from "./Dialog";
import { Icon } from "./Icons";
import {
  attributeLabels,
  categoryLabels,
  formatBudget,
  formatValue,
} from "./format";
import type { Attribute, Qualification } from "./types";

function Evidence({
  quote,
  onReveal,
}: {
  quote: string | null;
  onReveal: (quote: string) => void;
}) {
  return quote ? (
    <button
      type="button"
      className="evidence"
      onClick={() => onReveal(quote)}
      title="Показать цитату в исходной заявке"
    >
      <Icon name="quote" size={14} />
      <span>«{quote}»</span>
    </button>
  ) : null;
}

function Source({ text, quote }: { text: string; quote: string | null }) {
  const start = quote ? text.indexOf(quote) : -1;
  return (
    <p className="source-text">
      {start >= 0 && quote ? (
        <>
          {text.slice(0, start)}
          <mark>{text.slice(start, start + quote.length)}</mark>
          {text.slice(start + quote.length)}
        </>
      ) : (
        text
      )}
    </p>
  );
}

function WritePanel({
  run,
  busy,
  uncertain,
  onApply,
  onRefresh,
}: {
  run: Qualification;
  busy: boolean;
  uncertain: boolean;
  onApply: (attributes: Attribute[], overwrite: boolean) => Promise<void>;
  onRefresh: () => void;
}) {
  const [selected, setSelected] = useState<Attribute[]>(
    run.applied_attributes || [],
  );
  const [overwrite, setOverwrite] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const options = run.write_options || [];
  const selectedOptions = options.filter((option) =>
    selected.includes(option.attribute),
  );
  const hasExisting = selectedOptions.some(
    (option) =>
      option.current_value !== null &&
      option.current_value !== undefined &&
      option.current_value !== "",
  );
  const blocked = run.status !== "pending_review" || uncertain;
  const canWrite = run.lead_id !== null && options.length > 0 && !blocked;
  const hint =
    run.lead_id === null
      ? "Загрузите лид, чтобы записать данные в CRM."
      : run.status === "applied"
        ? "Выбранные поля записаны в Битрикс24."
        : run.status === "applying" ||
            run.status === "apply_unknown" ||
            uncertain
          ? "Проверьте запись в CRM и обновите состояние."
          : options.length === 0
            ? "Нет доступных полей. Проверьте настройки и данные заявки."
            : "Выберите поля для записи.";
  return (
    <section className="write-panel" aria-labelledby="write-heading">
      <div className="section-heading">
        <span className="section-icon blue">
          <Icon name="link" size={18} />
        </span>
        <div>
          <h3 id="write-heading">Запись в Битрикс24</h3>
          <p>{hint}</p>
        </div>
        {run.status === "applied" && (
          <span className="success-icon">
            <Icon name="check" />
          </span>
        )}
      </div>
      {run.lead_id !== null && options.length > 0 && (
        <fieldset disabled={!canWrite || busy} className="write-options">
          <legend className="sr-only">Поля для записи</legend>
          {options.map((option) => (
            <label
              key={option.attribute}
              className={`write-option ${selected.includes(option.attribute) ? "selected" : ""}`}
            >
              <input
                type="checkbox"
                checked={selected.includes(option.attribute)}
                onChange={(event) => {
                  setSelected((old) =>
                    event.target.checked
                      ? [...old, option.attribute]
                      : old.filter((value) => value !== option.attribute),
                  );
                  setOverwrite(false);
                }}
              />
              <span className="write-option-label">
                {attributeLabels[option.attribute]}
                <small>
                  В CRM при анализе: {formatValue(option.current_value)}
                </small>
              </span>
              <span className="write-value">
                {formatValue(option.suggested_value)}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      {canWrite && hasExisting && (
        <label className="overwrite-check">
          <input
            type="checkbox"
            checked={overwrite}
            disabled={busy}
            onChange={(event) => setOverwrite(event.target.checked)}
          />
          Разрешить замену заполненных полей
        </label>
      )}
      <div className="write-footer">
        {blocked && run.status !== "applied" ? (
          <button
            className="button secondary"
            onClick={onRefresh}
            disabled={busy}
          >
            <Icon name="refresh" size={16} />
            Обновить состояние
          </button>
        ) : (
          <button
            className="button primary"
            disabled={
              !canWrite ||
              !selected.length ||
              busy ||
              (hasExisting && !overwrite)
            }
            onClick={() => setConfirm(true)}
          >
            <Icon name="check" size={16} />
            Подтвердить поля{selected.length > 0 ? ` (${selected.length})` : ""}
          </button>
        )}
      </div>
      <Dialog
        title="Подтвердить запись"
        open={confirm}
        onClose={() => setConfirm(false)}
        busy={busy}
      >
        <p className="dialog-description">
          Запись выбранных полей в лид №{run.lead_id}.
        </p>
        <dl className="confirmation-list">
          {selectedOptions.map((option) => (
            <div key={option.attribute}>
              <dt>{attributeLabels[option.attribute]}</dt>
              <dd>
                <span>{formatValue(option.current_value)}</span>
                <Icon name="arrow" size={14} />
                <strong>{formatValue(option.suggested_value)}</strong>
              </dd>
            </div>
          ))}
        </dl>
        {hasExisting && (
          <p className="callout warning">
            Заполненные значения будут заменены.
          </p>
        )}
        <div className="dialog-actions">
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => setConfirm(false)}
          >
            Назад
          </button>
          <button
            className="button primary"
            disabled={busy}
            onClick={async () => {
              await onApply(selected, overwrite);
              setConfirm(false);
            }}
          >
            {busy ? (
              <>
                <span className="spinner" />
                Записываем…
              </>
            ) : (
              <>
                <Icon name="check" size={17} />
                Записать в Битрикс24
              </>
            )}
          </button>
        </div>
      </Dialog>
    </section>
  );
}

export function ResultView({
  run,
  busy,
  uncertain,
  onApply,
  onRefresh,
}: {
  run: Qualification;
  busy: boolean;
  uncertain: boolean;
  onApply: (attributes: Attribute[], overwrite: boolean) => Promise<void>;
  onRefresh: () => void;
}) {
  const [sourceOpen, setSourceOpen] = useState(false);
  const [activeQuote, setActiveQuote] = useState<string | null>(null);
  const { facts } = run.result;
  const warnings = facts.warnings.filter(
    (warning) => !warning.startsWith("Режим rules:"),
  );
  const reveal = (quote: string) => {
    setActiveQuote(quote);
    setSourceOpen(true);
  };
  const rows = [
    {
      label: "Направление",
      value: categoryLabels[facts.category],
      quote: facts.category_evidence,
      missing: facts.category === "unknown",
    },
    {
      label: "Пользователи",
      value: facts.users ? `${facts.users.value} чел.` : "Нужно уточнить",
      quote: facts.users?.evidence || null,
      missing: !facts.users,
    },
    {
      label: "Бюджет",
      value: formatBudget(facts.budget),
      quote: facts.budget?.evidence || null,
      missing: !facts.budget,
    },
    {
      label: "Желаемый срок",
      value: facts.deadline?.value || "Нужно уточнить",
      quote: facts.deadline?.evidence || null,
      missing: !facts.deadline,
    },
  ];
  return (
    <>
      <div className="result-summary">
        <div>
          <span
            className={`badge ${run.result.priority === "elevated" ? "purple" : "neutral"}`}
          >
            {run.result.priority === "elevated"
              ? "Повышенный приоритет"
              : "Обычный приоритет"}
          </span>
          <p>
            <span>
              {run.lead_id !== null
                ? `Лид №${run.lead_id}`
                : "Текстовая заявка"}
            </span>
          </p>
        </div>
        <div className="completeness">
          <strong>
            {Math.round(run.result.completeness * 100)}
            <span>%</span>
          </strong>
          <span>полнота данных</span>
        </div>
      </div>
      <div className="progress-track">
        <div style={{ width: `${run.result.completeness * 100}%` }} />
      </div>
      <div className="fact-list">
        {rows.map((row) => (
          <div className="fact-row" key={row.label}>
            <span className="fact-label">{row.label}</span>
            <div className="fact-content">
              <strong className={row.missing ? "muted-value" : ""}>
                {row.value}
              </strong>
              <Evidence quote={row.quote} onReveal={reveal} />
            </div>
          </div>
        ))}
        <div className="fact-row">
          <span className="fact-label">Интеграции</span>
          <div className="fact-content">
            {facts.integrations.length ? (
              facts.integrations.map((item) => (
                <div
                  className="integration-item"
                  key={`${item.value}-${item.evidence}`}
                >
                  <span className="tag">{item.value}</span>
                  <Evidence quote={item.evidence} onReveal={reveal} />
                </div>
              ))
            ) : (
              <>
                <strong className="muted-value">Не указаны</strong>
              </>
            )}
          </div>
        </div>
      </div>
      <div className="result-details">
        {run.result.questions.length > 0 && (
          <section className="questions">
            <h3>
              <Icon name="info" size={17} />
              Уточнить у клиента
            </h3>
            <ol>
              {run.result.questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ol>
          </section>
        )}
        <details className="detail-panel">
          <summary>
            Почему такой приоритет<span>{run.result.priority_score} балл.</span>
          </summary>
          {run.result.priority_reasons.length ? (
            <ul>
              {run.result.priority_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <p>Пока недостаточно данных для повышения приоритета.</p>
          )}
        </details>
        {warnings.length > 0 && (
          <details className="detail-panel">
            <summary>
              Требует внимания<span>{warnings.length}</span>
            </summary>
            <ul>
              {warnings.map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          </details>
        )}
        <details
          className="detail-panel"
          open={sourceOpen}
          onToggle={(event) => setSourceOpen(event.currentTarget.open)}
        >
          <summary>Исходная заявка</summary>
          <Source text={run.source_text} quote={activeQuote} />
        </details>
      </div>
      <WritePanel
        run={run}
        busy={busy}
        uncertain={uncertain}
        onApply={onApply}
        onRefresh={onRefresh}
      />
    </>
  );
}
