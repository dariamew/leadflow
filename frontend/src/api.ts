import type {
  AppConfig,
  Attribute,
  AuthSession,
  Qualification,
  User,
  ManagedUser,
  UserPage,
  InvitationIssued,
  InvitationInfo,
  MonitorConfig,
  MonitorStatus,
  BitrixSourceConfig,
  BitrixTextField,
  HistoryFilters,
} from "./types";

let csrfToken = "";
const unauthorizedListeners = new Set<() => void>();

export function onUnauthorized(listener: () => void) {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public outcomeUnknown = false,
  ) {
    super(message);
  }
}

const messages: Record<string, string> = {
  "Choose a single text field from Bitrix24": "Выберите поле с текстом заявки.",
  "Bitrix source settings are not initialized":
    "Настройка источника заявок недоступна. Обратитесь к администратору.",
  INVALID_RESPONSE: "Не удалось прочитать ответ Битрикс24. Повторите запрос.",
  insufficient_scope:
    "Недостаточно прав для работы с Битрикс24. Обратитесь к администратору.",
  "Lead monitoring is not initialized":
    "Автообработка недоступна. Обратитесь к администратору.",
  "Bitrix webhook is not configured":
    "Битрикс24 не подключён. Обратитесь к администратору.",
  "Lead monitoring worker is disabled":
    "Автообработка недоступна. Обратитесь к администратору.",
  "Choose a lead recipient": "Выберите получателя результатов.",
  "Choose an active lead recipient":
    "Выберите включённого сотрудника с установленным паролем.",
  "Monitoring settings changed; refresh":
    "Настройки изменились. Обновите страницу.",
  "Enable lead monitoring first": "Сначала включите отслеживание.",
  "Only failed lead jobs can be retried":
    "Повторить можно только задачу с ошибкой. Обновите список.",
  "Qualification not found":
    "Квалификация не найдена или недоступна вашему аккаунту.",
  "Cannot reset your own password through administration":
    "Для смены своего пароля обратитесь к другому администратору.",
  "Password reset requires an active user with a password":
    "Для восстановления нужен включённый аккаунт с установленным паролем.",
  "Administrator required": "Действие доступно только администратору.",
  "Username already exists": "Этот логин уже занят.",
  "Cannot demote or disable yourself":
    "Нельзя отключить свой аккаунт или изменить собственную роль.",
  "Cannot remove the last administrator":
    "Нельзя отключить последнего администратора.",
  "Invitation unavailable":
    "Ссылка недоступна: срок истёк, пароль уже установлен или аккаунт отключён. Запросите новую ссылку у администратора.",
  "Invitation requires an active pending user":
    "Ссылку можно создать только для включённого аккаунта без пароля.",
  "Invitation origin not configured":
    "Не удалось создать ссылку. Обратитесь к администратору.",
  "User not found": "Пользователь не найден. Обновите список.",
  NOT_CONFIGURED: "Битрикс24 не подключён. Обратитесь к администратору.",
  ACCESS_DENIED:
    "Нет доступа к этому лиду в Битрикс24. Обратитесь к администратору.",
  INVALID_CREDENTIALS:
    "Подключение к Битрикс24 недействительно. Обратитесь к администратору.",
  TRANSPORT_ERROR: "Не удалось получить ответ от Битрикс24.",
  QUERY_LIMIT_EXCEEDED:
    "Битрикс24 временно ограничил запросы. Попробуйте позже.",
  "Configured lead text field is empty or is not text":
    "В настроенном поле лида нет текста заявки.",
  "Lead text exceeds 20000 characters": "Текст лида превышает 20 000 символов.",
  "CRM source or selected fields changed; qualify again":
    "Заявка или выбранные поля изменились в CRM. Загрузите лид и выполните анализ заново.",
  "Selected fields are not empty; explicit overwrite is required":
    "В выбранных полях уже есть данные. Для замены отметьте разрешение перезаписи.",
  "Run is already applied, being applied, or requires reconciliation":
    "Запись уже выполняется или требует сверки с CRM. Обновите состояние результата.",
  "CRM field mapping changed; qualify the lead again":
    "Настройки полей изменились. Выполните квалификацию лида заново.",
  "CRM portal changed; qualify the lead again":
    "Портал изменился. Выполните квалификацию лида заново.",
  "Database operation failed": "Сервис временно недоступен. Попробуйте позже.",
  LLM_RESPONSE_INVALID_OR_UNAVAILABLE:
    "Не удалось завершить анализ. Попробуйте ещё раз.",
  EXTRACTION_EVIDENCE_INVALID:
    "Не удалось подтвердить данные заявки. Повторите анализ.",
  LLM_INCOMPLETE_OR_REFUSED: "Не удалось завершить анализ. Попробуйте ещё раз.",
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120_000);
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "leadflow",
        ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      },
      credentials: "same-origin",
      signal: controller.signal,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Сервер не отвечает. Проверьте соединение.");
  } finally {
    clearTimeout(timeout);
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : "";
    const code = typeof data?.code === "string" ? data.code : "";
    if (response.status === 401 && path !== "/auth/login") {
      csrfToken = "";
      unauthorizedListeners.forEach((listener) => listener());
    }
    const message =
      response.status === 401
        ? path === "/auth/login"
          ? "Неверный логин или пароль."
          : "Сессия завершена. Войдите снова."
        : response.status === 429
          ? "Слишком много попыток входа. Попробуйте через 10 минут."
          : detail === "Invalid CSRF token"
            ? "Обновите страницу и повторите действие."
            : messages[code] ||
              messages[detail] ||
              (response.status === 422
                ? "Проверьте введённые данные и выбранные поля."
                : "Не удалось выполнить запрос. Обновите состояние и попробуйте снова.");
    throw new ApiError(
      response.status,
      message,
      Boolean(data?.outcome_unknown),
    );
  }
  if (data === null)
    throw new ApiError(502, "Не удалось загрузить данные. Попробуйте ещё раз.");
  return data as T;
}

export const api = {
  sourceConfig: () => request<BitrixSourceConfig>("/bitrix/source"),
  sourceFields: () => request<BitrixTextField[]>("/bitrix/source/fields"),
  leadTextFields: () => request<BitrixTextField[]>("/bitrix/text-fields"),
  setSourceField: (text_field: string) =>
    request<BitrixSourceConfig>("/bitrix/source", {
      method: "PUT",
      body: JSON.stringify({ text_field }),
    }),
  monitor: () => request<MonitorStatus>("/bitrix/monitor"),
  configureMonitor: (config: MonitorConfig) =>
    request<MonitorStatus>("/bitrix/monitor", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  checkLeads: () =>
    request<MonitorStatus>("/bitrix/monitor/check", { method: "POST" }),
  retryLead: (id: string) =>
    request<MonitorStatus>(`/bitrix/monitor/jobs/${id}/retry`, {
      method: "POST",
    }),
  users: (offset = 0) => request<UserPage>(`/users?offset=${offset}&limit=20`),
  createUser: (username: string, role: User["role"]) =>
    request<InvitationIssued>("/users", {
      method: "POST",
      body: JSON.stringify({ username, role }),
    }),
  updateUser: (
    id: string,
    changes: { role?: User["role"]; is_active?: boolean },
  ) =>
    request<ManagedUser>(`/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    }),
  inviteUser: (id: string) =>
    request<InvitationIssued>(`/users/${id}/invitation`, { method: "POST" }),
  resetUserPassword: (id: string) =>
    request<InvitationIssued>(`/users/${id}/password-reset`, {
      method: "POST",
    }),
  checkInvitation: (token: string) =>
    request<InvitationInfo>("/auth/invitations/check", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  acceptInvitation: (token: string, password: string) =>
    request<void>("/auth/invitations/accept", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    }),
  login: async (username: string, password: string) => {
    const session = await request<AuthSession>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    csrfToken = session.csrf_token;
    return session;
  },
  me: async () => {
    const session = await request<AuthSession>("/auth/me");
    csrfToken = session.csrf_token;
    return session;
  },
  logout: async () => {
    await request<void>("/auth/logout", { method: "POST" });
    csrfToken = "";
  },
  config: () => request<AppConfig>("/config"),
  history: (filters: HistoryFilters = {}, offset = 0) => {
    // One extra record determines whether the next page exists.
    const query = new URLSearchParams({ limit: "31", offset: String(offset) });
    if (filters.lead_id !== undefined)
      query.set("lead_id", String(filters.lead_id));
    if (filters.status) query.set("status", filters.status);
    if (filters.priority) query.set("priority", filters.priority);
    return request<Qualification[]>(`/qualifications?${query}`);
  },
  get: (id: string) => request<Qualification>(`/qualifications/${id}`),
  qualifyLead: (id: number, textField?: string) =>
    request<Qualification>(`/bitrix/leads/${id}/qualify`, {
      method: "POST",
      ...(textField ? { body: JSON.stringify({ text_field: textField }) } : {}),
    }),
  apply: (id: string, attributes: Attribute[], allowOverwrite: boolean) =>
    request<Qualification>(`/qualifications/${id}/apply`, {
      method: "POST",
      body: JSON.stringify({ attributes, allow_overwrite: allowOverwrite }),
    }),
};
