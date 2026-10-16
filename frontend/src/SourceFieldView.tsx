import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import type { BitrixSourceConfig, BitrixTextField } from "./types";

export function SourceFieldView({
  onSaved,
}: {
  onSaved?: (field: string) => void;
}) {
  const [config, setConfig] = useState<BitrixSourceConfig | null>(null);
  const [fields, setFields] = useState<BitrixTextField[]>([]);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function load() {
    setLoading(true);
    setError("");
    setNotice("");
    try {
      const current = await api.sourceConfig();
      setConfig(current);
      setSelected(current.text_field);
      setFields(current.bitrix_configured ? await api.sourceFields() : []);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Не удалось загрузить поля Битрикс24.",
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.setSourceField(selected);
      setConfig(saved);
      setSelected(saved.text_field);
      onSaved?.(saved.text_field);
      setNotice(
        "Поле сохранено. Ручная загрузка и автообработка используют новый источник.",
      );
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Не удалось сохранить поле.",
      );
    } finally {
      setSaving(false);
    }
  }
  const known = fields.some((field) => field.code === selected);
  return (
    <section className="card" aria-labelledby="source-field-heading">
      <div className="card-heading">
        <h2 id="source-field-heading">Поле с текстом заявки</h2>
      </div>
      <div className="monitor-body">
        <p>Источник текста для загрузки по ID и автообработки.</p>
        <form onSubmit={save}>
          <label className="field-label" htmlFor="bitrix-source-field">
            Поле лида в Битрикс24
          </label>
          <select
            id="bitrix-source-field"
            className="source-field-select"
            value={selected}
            onChange={(event) => {
              setSelected(event.target.value);
              setNotice("");
            }}
            disabled={loading || saving || !fields.length}
            required
          >
            {!known && (
              <option value={selected}>
                {loading ? "Загружаем поля…" : "Выберите поле"}
              </option>
            )}
            {fields.map((field) => (
              <option key={field.code} value={field.code}>
                {field.title}
              </option>
            ))}
          </select>
          <p className="monitor-hint">
            Для анализа используется текст из выбранного поля.
          </p>
          {config?.portal_changed && (
            <p className="connection-error">
              Портал изменился. Выберите и сохраните поле для нового портала.
            </p>
          )}
          {config && !config.bitrix_configured && (
            <p className="connection-error">
              Подключите Битрикс24, чтобы загрузить список полей.
            </p>
          )}
          {config?.bitrix_configured && !loading && !error && !known && (
            <p className="connection-error">
              Текущее поле недоступно. Выберите поле из списка.
            </p>
          )}
          {error && (
            <p className="connection-error" role="alert">
              {error}
            </p>
          )}
          {notice && (
            <p className="source-saved" role="status">
              {notice}
            </p>
          )}
          <div className="monitor-actions">
            <button
              className="button primary"
              type="submit"
              disabled={
                loading || saving || !known || !config?.bitrix_configured
              }
            >
              {saving ? "Сохраняем…" : "Сохранить поле"}
            </button>
            <button
              className="text-button"
              type="button"
              disabled={loading || saving}
              onClick={() => void load()}
            >
              {loading ? "Загружаем поля…" : "Обновить список полей"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
