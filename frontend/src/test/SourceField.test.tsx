import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourceFieldView } from "../SourceFieldView";

const fields = [
  { code: "comments", title: "Комментарий" },
  { code: "UF_CRM_REQUEST_TEXT", title: "Текст заявки" },
];
function mockSource(current = "comments", failFirst = false) {
  let text_field = current;
  let failure = failFirst;
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/fields")) {
      if (failure) {
        failure = false;
        return new Response(JSON.stringify({ code: "ACCESS_DENIED" }), {
          status: 502,
        });
      }
      return new Response(JSON.stringify(fields));
    }
    if (options?.method === "PUT")
      text_field = JSON.parse(options.body as string).text_field;
    return new Response(
      JSON.stringify({
        text_field,
        saved: false,
        portal_changed: false,
        bitrix_configured: true,
      }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => vi.unstubAllGlobals());

describe("CRM source selection", () => {
  it("shows field names and saves a custom source independently of monitoring", async () => {
    const fetch = mockSource();
    const saved = vi.fn();
    render(<SourceFieldView onSaved={saved} />);
    await waitFor(() =>
      expect(screen.getByLabelText("Поле лида в Битрикс24")).toBeEnabled(),
    );
    expect(
      screen.getByRole("option", { name: "Комментарий" }),
    ).toBeInTheDocument();
    const user = userEvent.setup();
    await user.selectOptions(
      screen.getByLabelText("Поле лида в Битрикс24"),
      "UF_CRM_REQUEST_TEXT",
    );
    await user.click(screen.getByRole("button", { name: "Сохранить поле" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Поле сохранено",
    );
    expect(saved).toHaveBeenCalledWith("UF_CRM_REQUEST_TEXT");
    const request = fetch.mock.calls.find(
      ([, options]) => options?.method === "PUT",
    );
    expect(request![0]).toBe("/api/v1/bitrix/source");
    expect(JSON.parse(request![1]!.body as string)).toEqual({
      text_field: "UF_CRM_REQUEST_TEXT",
    });
    expect(fetch.mock.calls.some(([url]) => url.includes("/monitor"))).toBe(
      false,
    );
  });

  it("keeps a missing saved field visible and requires choosing an available one", async () => {
    mockSource("UF_CRM_REMOVED");
    render(<SourceFieldView />);
    await screen.findByText(
      "Текущее поле недоступно. Выберите поле из списка.",
    );
    expect(screen.getByLabelText("Поле лида в Битрикс24")).toHaveValue(
      "UF_CRM_REMOVED",
    );
    expect(
      screen.getByRole("button", { name: "Сохранить поле" }),
    ).toBeDisabled();
    await userEvent.selectOptions(
      screen.getByLabelText("Поле лида в Битрикс24"),
      "comments",
    );
    expect(
      screen.getByRole("button", { name: "Сохранить поле" }),
    ).toBeEnabled();
  });

  it("recovers after a catalogue request fails", async () => {
    mockSource("comments", true);
    render(<SourceFieldView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Нет доступа к этому лиду в Битрикс24",
    );
    expect(
      screen.getByRole("button", { name: "Сохранить поле" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Обновить список полей" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Сохранить поле" }),
      ).toBeEnabled(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
