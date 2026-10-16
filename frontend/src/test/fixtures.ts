import type { AppConfig, Qualification } from "../types";

export const configuration: AppConfig = {
  extractor: "rules",
  bitrix_configured: true,
  writable_attributes: ["users", "category"],
  bitrix_text_field: "comments",
};
export const result: Qualification = {
  id: "a92d3852-7539-4e0e-a0e1-3d64b87aa564",
  created_by_user_id: "user-1",
  created_by_username: "manager",
  trigger: "manual",
  lead_id: null,
  source_text: "Нужна CRM на 15 человек. Бюджет до 250 тысяч рублей.",
  source_text_field: null,
  source_field_explicit: false,
  extractor: "rules",
  pipeline_version: "0.1.0",
  status: "pending_review",
  created_at: "2026-10-15T10:00:00Z",
  duration_ms: 5,
  token_usage: {},
  applied_attributes: null,
  write_options: [],
  result: {
    facts: {
      category: "crm_implementation",
      category_evidence: "Нужна CRM на 15 человек",
      users: { value: 15, evidence: "15 человек" },
      budget: {
        minimum: null,
        maximum: 250000,
        currency: "RUB",
        evidence: "Бюджет до 250 тысяч рублей",
      },
      deadline: null,
      integrations: [],
      warnings: ["Результат требует проверки."],
    },
    priority: "normal",
    priority_score: 3,
    priority_reasons: ["Есть конкретные требования: +1."],
    completeness: 0.75,
    missing_fields: ["deadline"],
    questions: ["К какому сроку нужен результат?"],
  },
};
export const leadResult: Qualification = {
  ...result,
  lead_id: 42,
  source_text_field: "comments",
  write_options: [
    { attribute: "users", suggested_value: 15, current_value: 10 },
  ],
};
