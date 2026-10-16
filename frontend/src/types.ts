export type Category =
  "crm_implementation" | "integration" | "support" | "other" | "unknown";
export type Attribute =
  | "category"
  | "users"
  | "budget_min"
  | "budget_max"
  | "currency"
  | "deadline_text";
export interface TextFact {
  value: string;
  evidence: string;
}
export interface NumberFact {
  value: number;
  evidence: string;
}
export interface BudgetFact {
  minimum: number | null;
  maximum: number | null;
  currency: "RUB" | "USD" | "EUR" | null;
  evidence: string;
}
export interface Facts {
  category: Category;
  category_evidence: string | null;
  users: NumberFact | null;
  budget: BudgetFact | null;
  deadline: TextFact | null;
  integrations: TextFact[];
  warnings: string[];
}
export interface WriteOption {
  attribute: Attribute;
  suggested_value: string | number;
  current_value: unknown;
}
export interface Qualification {
  id: string;
  created_by_user_id: string | null;
  created_by_username: string | null;
  trigger: "manual" | "automatic";
  lead_id: number | null;
  source_text: string;
  source_text_field: string | null;
  source_field_explicit: boolean;
  extractor: "rules" | "llm";
  pipeline_version: string;
  status: "pending_review" | "applying" | "applied" | "apply_unknown";
  result: {
    facts: Facts;
    priority: "normal" | "elevated";
    priority_score: number;
    priority_reasons: string[];
    completeness: number;
    missing_fields: string[];
    questions: string[];
  };
  created_at: string;
  duration_ms: number;
  token_usage: Record<string, number>;
  applied_attributes: Attribute[] | null;
  write_options: WriteOption[];
}
export interface AppConfig {
  extractor: "rules" | "llm";
  bitrix_configured: boolean;
  writable_attributes: Attribute[];
  bitrix_text_field: string;
}

export interface BitrixSourceConfig {
  text_field: string;
  saved: boolean;
  portal_changed: boolean;
  bitrix_configured: boolean;
}

export interface BitrixTextField {
  code: string;
  title: string;
}

export interface User {
  id: string;
  username: string;
  role: "admin" | "manager";
}

export interface ManagedUser extends User {
  is_active: boolean;
  password_set: boolean;
  created_at: string;
  invitation_expires_at: string | null;
}

export interface UserPage {
  items: ManagedUser[];
  total: number;
}

export interface InvitationIssued {
  user: ManagedUser;
  invitation_url: string;
  expires_at: string;
}

export interface InvitationInfo {
  username: string;
  expires_at: string;
}

export interface MonitorConfig {
  enabled: boolean;
  owner_id: string | null;
  interval_seconds: number;
}

export interface LeadJob {
  id: string;
  lead_id: number;
  owner_username: string | null;
  status: "queued" | "processing" | "retry" | "done" | "failed";
  attempts: number;
  last_error: string | null;
  run_id: string | null;
  created_at: string;
  available_at: string;
}

export interface MonitorStatus extends MonitorConfig {
  cursor: number | null;
  last_poll_at: string | null;
  last_error: string | null;
  worker_available: boolean;
  worker_recent: boolean;
  bitrix_configured: boolean;
  recipients: { id: string; username: string }[];
  counts: Partial<Record<LeadJob["status"], number>>;
  jobs: LeadJob[];
}

export interface AuthSession {
  user: User;
  csrf_token: string;
}
export type HistoryFilters = {
  lead_id?: number;
  status?: Qualification["status"];
  priority?: Qualification["result"]["priority"];
};
