import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { UsersView } from "../UsersView";
import { App } from "../App";
import type { ManagedUser, User } from "../types";

const admin: User = { id: "admin-1", username: "admin", role: "admin" };
const account: ManagedUser = {
  ...admin,
  is_active: true,
  password_set: true,
  created_at: "2026-01-01T00:00:00Z",
  invitation_expires_at: null,
};
const colleague: ManagedUser = {
  ...account,
  id: "manager-1",
  username: "sales",
  role: "manager",
  password_set: false,
};
const token = "A".repeat(43);
const issued = {
  user: colleague,
  invitation_url: `http://localhost/#set-password=${token}`,
  expires_at: "2026-10-20T12:00:00Z",
};

function mockUsers(duplicate = false, passwordSet = false) {
  let users = [account, { ...colleague, password_set: passwordSet }];
  const changes: { path: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options?: RequestInit) => {
      const json = (data: unknown, status = 200) =>
        new Response(JSON.stringify(data), { status });
      if (path.includes("/users?"))
        return json({ items: users, total: users.length });
      if (options?.method === "POST" && path.endsWith("/users")) {
        const body = JSON.parse(options.body as string);
        changes.push({ path, body });
        return duplicate
          ? json({ detail: "Username already exists" }, 409)
          : json(issued, 201);
      }
      if (options?.method === "PATCH") {
        const body = JSON.parse(options.body as string);
        changes.push({ path, body });
        users = users.map((user) =>
          user.id === colleague.id ? { ...user, ...body } : user,
        );
        return json(users[1]);
      }
      if (path.endsWith("/invitation") || path.endsWith("/password-reset")) {
        changes.push({ path, body: {} });
        return json(issued);
      }
      throw new Error(`Unexpected request ${path}`);
    }),
  );
  return changes;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("creates a manager and shows a one-time link only after creation", async () => {
  const changes = mockUsers();
  render(<UsersView currentUser={admin} />);
  const user = userEvent.setup();
  await screen.findByText("sales");
  expect(
    screen.queryByRole("button", { name: "Отключить admin" }),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "Добавить пользователя" }),
  );
  const dialog = screen.getByRole("dialog", { name: "Новый пользователь" });
  expect(within(dialog).getByLabelText("Роль")).toHaveValue("manager");
  expect(screen.queryByLabelText("Одноразовая ссылка")).not.toBeInTheDocument();
  await user.type(within(dialog).getByLabelText("Логин"), "sales");
  await user.click(
    within(dialog).getByRole("button", { name: "Создать пользователя" }),
  );
  expect(await screen.findByLabelText("Одноразовая ссылка")).toHaveValue(
    issued.invitation_url,
  );
  expect(changes[0].body).toEqual({ username: "sales", role: "manager" });
  await user.click(screen.getByRole("button", { name: "Готово" }));
  expect(screen.queryByLabelText("Одноразовая ссылка")).not.toBeInTheDocument();
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});

it("shows duplicate login errors without issuing a link", async () => {
  mockUsers(true);
  render(<UsersView currentUser={admin} />);
  const user = userEvent.setup();
  await screen.findByText("sales");
  await user.click(
    screen.getByRole("button", { name: "Добавить пользователя" }),
  );
  await user.type(screen.getByLabelText("Логин"), "sales");
  await user.click(
    screen.getByRole("button", { name: "Создать пользователя" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("логин уже занят");
  expect(screen.queryByLabelText("Одноразовая ссылка")).not.toBeInTheDocument();
});

it("confirms role changes and disabling before sending them", async () => {
  const changes = mockUsers();
  render(<UsersView currentUser={admin} />);
  const user = userEvent.setup();
  await screen.findByText("sales");
  await user.click(screen.getByRole("button", { name: "Изменить роль sales" }));
  await user.selectOptions(screen.getByLabelText("Роль"), "admin");
  expect(changes).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Сохранить роль" }));
  await waitFor(() => expect(changes).toHaveLength(1));
  expect(changes[0].body).toEqual({ role: "admin" });
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  await user.click(screen.getByRole("button", { name: "Отключить sales" }));
  expect(changes).toHaveLength(1);
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Отключить",
    }),
  );
  expect(await screen.findByText("Отключён")).toBeVisible();
  expect(changes[1].body).toEqual({ is_active: false });
  expect(screen.getByRole("button", { name: "Включить sales" })).toBeEnabled();
});

it("requires confirmation before replacing an invitation", async () => {
  const changes = mockUsers();
  render(<UsersView currentUser={admin} />);
  const user = userEvent.setup();
  await screen.findByText("sales");
  await user.click(
    screen.getByRole("button", { name: "Создать ссылку для sales" }),
  );
  expect(
    screen.getByText("Предыдущая ссылка перестанет работать."),
  ).toBeVisible();
  expect(changes).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Создать ссылку" }));
  expect(await screen.findByLabelText("Одноразовая ссылка")).toBeVisible();
  expect(changes[0].path).toBe("/api/v1/users/manager-1/invitation");
});

it("checks an invitation, matches passwords and returns to login after setting one", async () => {
  window.history.replaceState(null, "", `/#set-password=${token}`);
  const accepts: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith("/check"))
        return new Response(
          JSON.stringify({ username: "sales", expires_at: issued.expires_at }),
          { status: 200 },
        );
      if (path.endsWith("/accept")) {
        accepts.push(JSON.parse(options?.body as string));
        return new Response(null, { status: 204 });
      }
      return new Response(
        JSON.stringify({ detail: "Authentication required" }),
        { status: 401 },
      );
    }),
  );
  render(<App />);
  const user = userEvent.setup();
  const password = await screen.findByLabelText("Новый пароль");
  expect(accepts).toHaveLength(0);
  await user.type(password, "a-strong-password");
  await user.type(
    screen.getByLabelText("Повторите пароль"),
    "different-password",
  );
  await user.click(screen.getByRole("button", { name: "Установить пароль" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Пароли не совпадают");
  expect(accepts).toHaveLength(0);
  await user.clear(screen.getByLabelText("Повторите пароль"));
  await user.type(
    screen.getByLabelText("Повторите пароль"),
    "a-strong-password",
  );
  await user.click(screen.getByRole("button", { name: "Установить пароль" }));
  await screen.findByRole("heading", { name: "Пароль установлен" });
  expect(accepts).toEqual([{ token, password: "a-strong-password" }]);
  expect(window.location.hash).toBe("");
  await user.click(screen.getByRole("button", { name: "Перейти ко входу" }));
  expect(await screen.findByLabelText("Логин", { exact: true })).toHaveValue(
    "sales",
  );
  expect(screen.getByLabelText("Пароль", { exact: true })).toHaveValue("");
});

it("does not offer a password form for an expired or consumed invitation", async () => {
  window.history.replaceState(null, "", `/#set-password=${token}`);
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (path: string) =>
        new Response(
          JSON.stringify({
            detail: path.endsWith("/me")
              ? "Authentication required"
              : "Invitation unavailable",
          }),
          { status: path.endsWith("/me") ? 401 : 400 },
        ),
    ),
  );
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Ссылка недоступна",
  );
  expect(screen.queryByLabelText("Новый пароль")).not.toBeInTheDocument();
});

it("issues a password recovery link only after admin confirmation", async () => {
  const changes = mockUsers(false, true);
  render(<UsersView currentUser={admin} />);
  const user = userEvent.setup();
  await screen.findByText("sales");
  await user.click(
    screen.getByRole("button", { name: "Сбросить пароль sales" }),
  );
  expect(
    screen.getByText(/Старый пароль действует до установки нового/),
  ).toBeVisible();
  expect(changes).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Создать ссылку" }));
  expect(await screen.findByLabelText("Одноразовая ссылка")).toHaveValue(
    issued.invitation_url,
  );
  expect(changes[0].path).toBe("/api/v1/users/manager-1/password-reset");
});
