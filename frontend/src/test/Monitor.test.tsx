import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MonitorView } from "../MonitorView";
import type { MonitorStatus, User } from "../types";

const admin: User = { id: "admin-id", username: "admin", role: "admin" };
const initial: MonitorStatus = {
  enabled: false,
  owner_id: null,
  interval_seconds: 60,
  cursor: null,
  last_poll_at: null,
  last_error: null,
  worker_available: true,
  worker_recent: false,
  bitrix_configured: true,
  recipients: [
    { id: admin.id, username: "admin" },
    { id: "manager-id", username: "manager" },
  ],
  counts: {},
  jobs: [],
};
function mockMonitor(overrides: Partial<MonitorStatus> = {}) {
  let data = structuredClone({ ...initial, ...overrides });
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/source/fields"))
      return new Response(
        JSON.stringify([{ code: "comments", title: "Комментарий" }]),
      );
    if (url.endsWith("/source"))
      return new Response(
        JSON.stringify({
          text_field: "comments",
          saved: false,
          portal_changed: false,
          bitrix_configured: data.bitrix_configured,
        }),
      );
    if (options?.method === "PUT")
      data = { ...data, ...JSON.parse(options.body as string) };
    if (url.endsWith("/retry"))
      data.jobs = data.jobs.map((job) => ({
        ...job,
        status: "queued",
        last_error: null,
      }));
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => vi.unstubAllGlobals());

describe("automatic qualification", () => {
  it("enables polling with the selected recipient and interval", async () => {
    const fetch = mockMonitor();
    render(<MonitorView user={admin} onOpen={vi.fn()} />);
    await screen.findByLabelText("Получатель результатов");
    const user = userEvent.setup();
    await user.selectOptions(
      screen.getByLabelText("Получатель результатов"),
      "manager-id",
    );
    await user.selectOptions(screen.getByLabelText("Проверять каждые"), "300");
    await user.click(
      screen.getByRole("button", { name: "Включить отслеживание" }),
    );
    await screen.findByText("Включено", { exact: true });
    const request = fetch.mock.calls.find(
      ([, options]) => options?.method === "PUT",
    );
    expect(JSON.parse(request![1]!.body as string)).toEqual({
      enabled: true,
      owner_id: "manager-id",
      interval_seconds: 300,
    });
    expect(
      screen.getByText("Запись в CRM — после подтверждения."),
    ).toBeVisible();
  });

  it("schedules an immediate check and pauses without losing settings", async () => {
    const fetch = mockMonitor({ enabled: true, owner_id: "manager-id" });
    render(<MonitorView user={admin} onOpen={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Проверить сейчас" }),
    );
    expect(
      fetch.mock.calls.some(
        ([path, options]) =>
          path.endsWith("/check") && options?.method === "POST",
      ),
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "Приостановить" }));
    await screen.findByText("Выключено", { exact: true });
    expect(screen.getByLabelText("Получатель результатов")).toHaveValue(
      "manager-id",
    );
  });

  it("opens qualified results and retries failed jobs", async () => {
    const fetch = mockMonitor({
      enabled: true,
      counts: { done: 1, failed: 1 },
      jobs: [
        {
          id: "job-1",
          lead_id: 11,
          owner_username: "manager",
          status: "done",
          attempts: 1,
          last_error: null,
          run_id: "run-1",
          created_at: "2026-10-16T10:00:00Z",
          available_at: "2026-10-16T10:00:00Z",
        },
        {
          id: "job-2",
          lead_id: 12,
          owner_username: "manager",
          status: "failed",
          attempts: 5,
          last_error: "LEAD_TEXT_UNAVAILABLE",
          run_id: null,
          created_at: "2026-10-16T10:00:00Z",
          available_at: "2026-10-16T10:00:00Z",
        },
      ],
    });
    const open = vi.fn();
    render(<MonitorView user={admin} onOpen={open} />);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: /Открыть результат/ }),
    );
    expect(open).toHaveBeenCalledWith("run-1");
    expect(screen.getByText(/В поле заявки нет текста/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Повторить" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      fetch.mock.calls.some(([path]) => path.endsWith("/jobs/job-2/retry")),
    ).toBe(true);
  });

  it("prevents enabling when the server webhook is absent", async () => {
    mockMonitor({ bitrix_configured: false });
    render(<MonitorView user={admin} onOpen={vi.fn()} />);
    expect(
      await screen.findByRole("button", { name: "Включить отслеживание" }),
    ).toBeDisabled();
    expect(screen.getByText(/Битрикс24 не подключён/)).toBeVisible();
  });
});
