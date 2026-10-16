import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { configuration, leadResult, result } from "./fixtures";
import type { Qualification } from "../types";

type ApplyBehavior = "ok" | "stale" | "unknown";
function mockServer(
  lead = false,
  behavior: ApplyBehavior = "ok",
  history: Qualification[] = [],
  signedIn = false,
  role: "admin" | "manager" = "manager",
) {
  let current = structuredClone(lead ? leadResult : result);
  const writes: RequestInit[] = [];
  const auth = {
    user: { id: "user-1", username: "manager", role },
    csrf_token: "session-csrf-token",
  };
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), { status });
    if (url.endsWith("/auth/me"))
      return signedIn
        ? json(auth)
        : json({ detail: "Authentication required" }, 401);
    if (url.endsWith("/auth/login")) {
      signedIn = true;
      return json(auth);
    }
    if (url.endsWith("/auth/logout")) {
      signedIn = false;
      return new Response(null, { status: 204 });
    }
    if (url.endsWith("/config")) return json(configuration);
    if (url.endsWith("/bitrix/text-fields"))
      return json([
        { code: "comments", title: "Комментарий" },
        { code: "UF_CRM_REQUEST_TEXT", title: "Текст заявки" },
      ]);
    if (url.includes("/users?")) return json({ items: [], total: 0 });
    if (url.includes("?limit=")) {
      const query = new URL(url, "http://test").searchParams;
      const filtered = history.filter(
        (item) =>
          (!query.has("lead_id") ||
            item.lead_id === Number(query.get("lead_id"))) &&
          (!query.has("status") || item.status === query.get("status")) &&
          (!query.has("priority") ||
            item.result.priority === query.get("priority")),
      );
      const offset = Number(query.get("offset") || 0);
      return json(
        filtered.slice(offset, offset + Number(query.get("limit") || 20)),
      );
    }
    if (url.endsWith("/qualify")) {
      const field = options?.body
        ? JSON.parse(options.body as string).text_field
        : null;
      current = {
        ...current,
        source_text_field: field || configuration.bitrix_text_field,
        source_field_explicit: Boolean(field),
      };
    }
    if (url.endsWith("/apply")) {
      writes.push(options || {});
      if (behavior === "stale")
        return json(
          { detail: "CRM source or selected fields changed; qualify again" },
          409,
        );
      if (behavior === "unknown") {
        current.status = "apply_unknown";
        return json({ code: "TRANSPORT_ERROR", outcome_unknown: true }, 502);
      }
      current = {
        ...current,
        status: "applied",
        applied_attributes: ["users"],
      };
    }
    return json(current);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    writes,
    fetchMock,
    switchAccount: () => {
      auth.user = { id: "user-2", username: "other-manager", role: "manager" };
      history = [];
    },
  };
}

async function connect() {
  const user = userEvent.setup();
  await screen.findByRole("heading", { name: "Вход в сервис" });
  await user.type(screen.getByLabelText("Логин", { exact: true }), "manager");
  await user.type(
    screen.getByLabelText("Пароль", { exact: true }),
    "test-password-123",
  );
  await user.click(screen.getByRole("button", { name: "Войти" }));
  await screen.findByRole("heading", { name: "История квалификаций" });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Загрузить лид" })).toBeEnabled(),
  );
  return user;
}

async function qualifyLead() {
  const user = await connect();
  await user.click(screen.getByRole("button", { name: "Загрузить лид" }));
  await user.type(screen.getByLabelText("ID лида в Битрикс24"), "42");
  await user.click(screen.getByRole("button", { name: "Анализировать лид" }));
  await screen.findByText(/Лид №42/);
  return user;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("qualification interface", () => {
  it("combines filters across the whole history, preserves them on return and resets them", async () => {
    const records = Array.from({ length: 35 }, (_, index) => ({
      ...leadResult,
      id: `record-${index}`,
      lead_id: 100 + index,
    }));
    const target: Qualification = {
      ...leadResult,
      id: "target",
      lead_id: 42,
      result: { ...leadResult.result, priority: "elevated" },
    };
    const { fetchMock } = mockServer(true, "ok", [...records, target], true);
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) =>
      url.endsWith("/target")
        ? new Response(JSON.stringify(target))
        : original(url, options),
    );
    render(<App />);
    const user = userEvent.setup();
    await screen.findByText("Лид №100", {}, { timeout: 5000 });
    expect(
      screen.queryByRole("button", { name: /Лид №42/ }),
    ).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("ID лида", { exact: true }), "42");
    await user.selectOptions(
      screen.getByLabelText("Статус", { exact: true }),
      "pending_review",
    );
    await user.selectOptions(
      screen.getByLabelText("Приоритет", { exact: true }),
      "elevated",
    );
    await user.click(screen.getByRole("button", { name: "Найти" }));
    const row = await screen.findByRole("button", {
      name: /Лид №42.*Повышенный/,
    });
    expect(
      screen.queryByRole("button", { name: /Лид №100/ }),
    ).not.toBeInTheDocument();
    await user.click(row);
    await user.click(await screen.findByRole("button", { name: "К истории" }));
    expect(screen.getByLabelText("ID лида", { exact: true })).toHaveValue("42");
    expect(screen.getByLabelText("Статус", { exact: true })).toHaveValue(
      "pending_review",
    );
    await screen.findByRole("button", { name: /Лид №42.*Повышенный/ });
    await user.click(screen.getByRole("button", { name: "Сбросить" }));
    await screen.findByText("Лид №100", {}, { timeout: 5000 });
    expect(screen.getByLabelText("Приоритет", { exact: true })).toHaveValue("");
  }, 15000);

  it("paginates the history and resets the page when filters change", async () => {
    mockServer(
      true,
      "ok",
      Array.from({ length: 31 }, (_, index) => ({
        ...leadResult,
        id: `page-${index}`,
        lead_id: index + 1,
      })),
      true,
    );
    render(<App />);
    const user = userEvent.setup();
    await screen.findByText("Лид №1", {}, { timeout: 5000 });
    expect(
      screen.queryByRole("button", { name: /Лид №31\D/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await screen.findByRole("button", { name: /Лид №31\D/ });
    expect(screen.getByText("Страница 2")).toBeVisible();
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
    await user.type(screen.getByLabelText("ID лида", { exact: true }), "1");
    await user.click(screen.getByRole("button", { name: "Найти" }));
    await screen.findByRole("button", { name: /Лид №1\D/ });
    expect(screen.queryByText("Страница 2")).not.toBeInTheDocument();
  }, 15000);

  it("shows an empty filtered state and rejects invalid lead IDs before requesting", async () => {
    const { fetchMock } = mockServer(true, "ok", [leadResult], true);
    render(<App />);
    await screen.findByRole("button", { name: /Лид №42/ });
    const user = userEvent.setup();
    const input = screen.getByLabelText("ID лида", { exact: true });
    await user.type(input, "-5");
    const before = fetchMock.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Найти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "положительный числовой ID",
    );
    expect(fetchMock.mock.calls).toHaveLength(before);
    await user.clear(input);
    await user.type(input, "999");
    await user.click(screen.getByRole("button", { name: "Найти" }));
    expect(await screen.findByText("Ничего не найдено")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Сбросить" }));
    await screen.findByRole("button", { name: /Лид №42/ });
  });

  it("ignores responses from an older filter request", async () => {
    const { fetchMock } = mockServer(true, "ok", [leadResult], true);
    const original = fetchMock.getMockImplementation()!;
    let release!: (response: Response) => void;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) =>
      url.includes("lead_id=999")
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : original(url, options),
    );
    render(<App />);
    await screen.findByRole("button", { name: /Лид №42/ });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("ID лида", { exact: true }), "999");
    await user.click(screen.getByRole("button", { name: "Найти" }));
    await waitFor(() => expect(release).toBeTypeOf("function"));
    await user.click(screen.getByRole("button", { name: "Сбросить" }));
    await screen.findByRole("button", { name: /Лид №42/ });
    await act(async () => {
      release(new Response(JSON.stringify([])));
    });
    expect(screen.getByRole("button", { name: /Лид №42/ })).toBeVisible();
  });

  it("keeps filters during automatic refresh", async () => {
    const interval = vi.spyOn(window, "setInterval");
    const { fetchMock } = mockServer(true, "ok", [leadResult], true);
    render(<App />);
    await screen.findByRole("button", { name: /Лид №42/ });
    const user = userEvent.setup();
    await user.selectOptions(
      screen.getByLabelText("Статус", { exact: true }),
      "pending_review",
    );
    await user.click(screen.getByRole("button", { name: "Найти" }));
    await screen.findByRole("button", { name: /Лид №42/ });
    const tick = interval.mock.calls
      .filter(([, delay]) => delay === 15000)
      .at(-1)![0] as () => void;
    const before = fetchMock.mock.calls.length;
    await act(async () => {
      tick();
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.length).toBeGreaterThan(before),
    );
    const path = fetchMock.mock.calls.at(-1)![0];
    expect(new URL(path, "http://test").searchParams.get("status")).toBe(
      "pending_review",
    );
    expect(screen.getByLabelText("Статус", { exact: true })).toHaveValue(
      "pending_review",
    );
  });

  it("starts with saved results and returns home from the ID form", async () => {
    mockServer(true, "ok", [leadResult], true);
    render(<App />);
    const heading = await screen.findByRole("heading", {
      name: "История квалификаций",
    });
    expect(heading).toBeVisible();
    expect(
      await screen.findByRole("button", { name: /Лид №42.*На проверке/ }),
    ).toBeVisible();
    expect(
      screen.queryByLabelText("ID лида в Битрикс24"),
    ).not.toBeInTheDocument();
    expect(document.querySelector("textarea")).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Загрузить лид" }));
    expect(screen.getByRole("heading", { name: "Анализ по ID" })).toBeVisible();
    expect(screen.getByLabelText("ID лида в Битрикс24")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "К истории" }));
    expect(
      screen.getByRole("heading", { name: "История квалификаций" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Лид №42.*На проверке/ }),
    ).toBeVisible();
  });

  it("offers retry when history loading fails instead of showing an empty history", async () => {
    const { fetchMock } = mockServer(true, "ok", [leadResult], true);
    const original = fetchMock.getMockImplementation()!;
    let failed = false;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.includes("?limit=") && !failed) {
        failed = true;
        return new Response(
          JSON.stringify({ detail: "Database operation failed" }),
          { status: 503 },
        );
      }
      return original(url, options);
    });
    render(<App />);
    expect(
      await screen.findByText("Не удалось загрузить результаты."),
    ).toBeVisible();
    expect(screen.queryByText("Результатов пока нет")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(
      await screen.findByRole("button", { name: /Лид №42.*На проверке/ }),
    ).toBeVisible();
  });

  it("keeps history chronological after opening an older saved result", async () => {
    const newer = {
      ...leadResult,
      id: "newer",
      lead_id: 99,
      created_at: "2026-10-16T12:00:00Z",
    };
    mockServer(true, "ok", [newer, leadResult], true);
    render(<App />);
    const older = await screen.findByRole("button", {
      name: /Лид №42.*На проверке/,
    });
    await userEvent.click(older);
    await userEvent.click(
      await screen.findByRole("button", { name: "К истории" }),
    );
    const rows = screen.getAllByRole("button", {
      name: /Лид №(42|99).*На проверке/,
    });
    expect(rows[0]).toHaveAccessibleName(/Лид №99/);
    expect(rows[1]).toHaveAccessibleName(/Лид №42/);
  });

  it("lets a manager choose a CRM field beside the lead ID for one analysis", async () => {
    const { fetchMock } = mockServer(true);
    render(<App />);
    const user = await connect();
    await user.click(screen.getByRole("button", { name: "Загрузить лид" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Поле с текстом заявки")).toBeEnabled(),
    );
    expect(screen.getByLabelText("Поле с текстом заявки")).toHaveValue("");
    await user.selectOptions(
      screen.getByLabelText("Поле с текстом заявки"),
      "UF_CRM_REQUEST_TEXT",
    );
    await user.type(screen.getByLabelText("ID лида в Битрикс24"), "42");
    await user.click(screen.getByRole("button", { name: "Анализировать лид" }));
    await screen.findByText(/Лид №42/);
    const request = fetchMock.mock.calls.find(([path]) =>
      path.endsWith("/leads/42/qualify"),
    );
    expect(JSON.parse(request![1]!.body as string)).toEqual({
      text_field: "UF_CRM_REQUEST_TEXT",
    });
    expect(
      fetchMock.mock.calls.some(
        ([path, options]) =>
          path.endsWith("/bitrix/source") && options?.method === "PUT",
      ),
    ).toBe(false);
    await user.click(screen.getByText("Исходная заявка", { exact: true }));
    expect(
      screen.queryByText("Поле: UF_CRM_REQUEST_TEXT"),
    ).not.toBeInTheDocument();
  });

  it("restores an explicit source when opening a saved result", async () => {
    const record = {
      ...leadResult,
      source_text_field: "UF_CRM_REQUEST_TEXT",
      source_field_explicit: true,
    };
    const { fetchMock } = mockServer(true, "ok", [record], true);
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) =>
      url.endsWith(`/${record.id}`)
        ? new Response(JSON.stringify(record))
        : original(url, options),
    );
    render(<App />);
    const history = await screen.findByRole("button", { name: /История/ });
    await waitFor(() => expect(history).toBeEnabled());
    await userEvent.click(history);
    await userEvent.click(
      screen.getByRole("button", { name: /Лид №42.*На проверке/ }),
    );
    expect(
      await screen.findByRole("heading", { name: "Результат квалификации" }),
    ).toBeVisible();
    expect(
      screen.queryByLabelText("ID лида в Битрикс24"),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Повторить анализ" }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Поле с текстом заявки")).toHaveValue(
        "UF_CRM_REQUEST_TEXT",
      ),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([path, options]) =>
            path.endsWith("/leads/42/qualify") &&
            JSON.parse(options?.body as string).text_field ===
              "UF_CRM_REQUEST_TEXT",
        ),
      ).toBe(true),
    );
  });
  it("labels manager history as personal without exposing author labels", async () => {
    mockServer(false, "ok", [result], true);
    render(<App />);
    const history = await screen.findByRole("button", { name: /История/ });
    await waitFor(() => expect(history).toBeEnabled());
    await userEvent.click(history);
    expect(screen.getByText("Мои результаты")).toBeVisible();
    expect(screen.queryByText(/Автор:/)).not.toBeInTheDocument();
  });

  it("shows authors and legacy records in administrator history", async () => {
    mockServer(
      false,
      "ok",
      [
        result,
        {
          ...result,
          id: "legacy",
          created_by_user_id: null,
          created_by_username: null,
        },
      ],
      true,
      "admin",
    );
    render(<App />);
    const history = await screen.findByRole("button", { name: /История/ });
    await waitFor(() => expect(history).toBeEnabled());
    await userEvent.click(history);
    expect(screen.getByText("Все сотрудники")).toBeVisible();
    expect(screen.getByText("Автор: manager")).toBeVisible();
    expect(screen.getByText("Автор не указан")).toBeVisible();
  });

  it("clears cached history and admin navigation when the cookie account changes", async () => {
    const { switchAccount } = mockServer(false, "ok", [result], true, "admin");
    render(<App />);
    const history = await screen.findByRole("button", { name: /История/ });
    await waitFor(() => expect(history).toBeEnabled());
    await userEvent.click(history);
    expect(screen.getByText(result.source_text)).toBeVisible();
    switchAccount();
    fireEvent.focus(window);
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Пользователи" }),
      ).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /История/ })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: /История/ }));
    expect(screen.getByText("Результатов пока нет")).toBeVisible();
    expect(screen.queryByText(result.source_text)).not.toBeInTheDocument();
  });

  it("requires login before showing the workspace", async () => {
    mockServer();
    render(<App />);
    await screen.findByRole("heading", { name: "Вход в сервис" });
    expect(
      screen.queryByRole("heading", { name: "История квалификаций" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Ключ доступа к API"),
    ).not.toBeInTheDocument();
    await connect();
    expect(
      screen.queryByRole("button", { name: "Пользователи" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Результатов пока нет")).toBeVisible();
    expect(
      screen.queryByLabelText("Текст заявки", { exact: true }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Вставить пример" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Загрузить лид" })).toBeEnabled();
  });

  it("loads a lead by ID, renders citations and highlights its source", async () => {
    const { fetchMock } = mockServer(true);
    render(<App />);
    const user = await qualifyLead();
    expect(await screen.findByText(/250\s000/)).toHaveTextContent("До");
    expect(screen.getByText("К какому сроку нужен результат?")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Подтвердить поля" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /«15 человек»/ }));
    await waitFor(() =>
      expect(document.querySelector("mark")).toHaveTextContent("15 человек"),
    );
    expect(
      fetchMock.mock.calls.some(([path]) => path.endsWith("/leads/42/qualify")),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(
        ([path, options]) =>
          path.endsWith("/qualifications") && options?.method === "POST",
      ),
    ).toBe(false);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("writes only selected fields after overwrite permission and a separate confirmation", async () => {
    const { writes } = mockServer(true);
    render(<App />);
    const user = await qualifyLead();
    await user.click(screen.getByRole("checkbox", { name: /Пользователи/ }));
    expect(
      screen.getByRole("button", { name: "Подтвердить поля (1)" }),
    ).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", {
        name: "Разрешить замену заполненных полей",
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Подтвердить поля (1)" }),
    );
    expect(writes).toHaveLength(0);
    const dialog = screen.getByRole("dialog", { name: "Подтвердить запись" });
    expect(within(dialog).getByText("10")).toBeVisible();
    expect(within(dialog).getByText("15")).toBeVisible();
    await user.click(
      within(dialog).getByRole("button", { name: "Записать в Битрикс24" }),
    );
    await screen.findByRole("status");
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(JSON.parse(writes[0].body as string)).toEqual({
      attributes: ["users"],
      allow_overwrite: true,
    });
    expect(writes[0].credentials).toBe("same-origin");
    expect(writes[0].headers).toMatchObject({
      "X-CSRF-Token": "session-csrf-token",
    });
    expect(writes[0].headers).not.toHaveProperty("X-API-Key");
    expect(
      screen.getByRole("button", { name: "Подтвердить поля (1)" }),
    ).toBeDisabled();
  });

  it("shows stale data errors instead of reporting success", async () => {
    const { writes } = mockServer(true, "stale");
    render(<App />);
    const user = await qualifyLead();
    await user.click(screen.getByRole("checkbox", { name: /Пользователи/ }));
    await user.click(
      screen.getByRole("checkbox", {
        name: "Разрешить замену заполненных полей",
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Подтвердить поля (1)" }),
    );
    await user.click(
      within(
        screen.getByRole("dialog", { name: "Подтвердить запись" }),
      ).getByRole("button", { name: "Записать в Битрикс24" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "изменились в CRM",
    );
    expect(writes).toHaveLength(1);
  });

  it("blocks repeat writes after an unknown outcome", async () => {
    const { writes } = mockServer(true, "unknown");
    render(<App />);
    const user = await qualifyLead();
    await user.click(screen.getByRole("checkbox", { name: /Пользователи/ }));
    await user.click(
      screen.getByRole("checkbox", {
        name: "Разрешить замену заполненных полей",
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Подтвердить поля (1)" }),
    );
    await user.click(
      within(
        screen.getByRole("dialog", { name: "Подтвердить запись" }),
      ).getByRole("button", { name: "Записать в Битрикс24" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Нет подтверждения записи",
    );
    expect(
      screen.getByRole("button", { name: "Обновить состояние" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /Подтвердить поля/ }),
    ).not.toBeInTheDocument();
    expect(writes).toHaveLength(1);
  });

  it("loads history and opens a saved qualification", async () => {
    mockServer(false, "ok", [result]);
    render(<App />);
    const user = await connect();
    await user.click(screen.getByRole("button", { name: /История/ }));
    await user.click(
      screen.getByRole("button", { name: /Внедрение CRM.*На проверке/ }),
    );
    expect(await screen.findByText("Результат")).toBeVisible();
    expect(
      screen.queryByLabelText("Текст заявки", { exact: true }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Повторить анализ" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByText("Исходная заявка", { exact: true }));
    expect(screen.getByText(result.source_text)).toBeVisible();
  });

  it("handles invalid credentials without exposing raw backend details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "secret upstream message" }), {
            status: 401,
          }),
      ),
    );
    render(<App />);
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Вход в сервис" });
    await user.type(screen.getByLabelText("Логин", { exact: true }), "manager");
    await user.type(
      screen.getByLabelText("Пароль", { exact: true }),
      "incorrect-password",
    );
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Неверный логин или пароль",
    );
    expect(
      screen.queryByText("secret upstream message"),
    ).not.toBeInTheDocument();
  });

  it("restores an existing session without asking for credentials", async () => {
    const { fetchMock } = mockServer(false, "ok", [result], true);
    render(<App />);
    await screen.findByRole("heading", { name: "История квалификаций" });
    expect(
      screen.queryByRole("heading", { name: "Вход в сервис" }),
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([path]) => path.endsWith("/auth/login")),
    ).toBe(false);
  });

  it("logs out through the server and removes the workspace", async () => {
    const { fetchMock } = mockServer();
    render(<App />);
    const user = await connect();
    await user.click(screen.getByRole("button", { name: "Аккаунт" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Аккаунт" })).getByRole(
        "button",
        { name: "Выйти" },
      ),
    );
    await screen.findByRole("heading", { name: "Вход в сервис" });
    expect(
      fetchMock.mock.calls.some(([path]) => path.endsWith("/auth/logout")),
    ).toBe(true);
    expect(
      screen.queryByRole("heading", { name: "История квалификаций" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Пароль", { exact: true })).toHaveValue("");
  });

  it("returns to login when a session expires", async () => {
    const { fetchMock } = mockServer();
    render(<App />);
    const user = await connect();
    await user.click(screen.getByRole("button", { name: "Загрузить лид" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Поле с текстом заявки")).toBeEnabled(),
    );
    await user.type(screen.getByLabelText("ID лида в Битрикс24"), "42");
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Authentication required" }), {
        status: 401,
      }),
    );
    await user.click(screen.getByRole("button", { name: "Анализировать лид" }));
    await screen.findByRole("heading", { name: "Вход в сервис" });
    expect(screen.getByRole("alert")).toHaveTextContent("Сессия завершена");
    expect(screen.queryByLabelText("Текст заявки")).not.toBeInTheDocument();
  });

  it("shows user administration navigation only for admins", async () => {
    mockServer(false, "ok", [], true, "admin");
    render(<App />);
    const button = await screen.findByRole("button", { name: "Пользователи" });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
    expect(
      await screen.findByRole("heading", { name: "Пользователи" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Добавить пользователя" }),
    ).toBeVisible();
  });
});
