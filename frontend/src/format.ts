import type { BudgetFact, Category } from "./types";

export const categoryLabels: Record<Category, string> = {
  crm_implementation: "Внедрение CRM",
  integration: "Интеграции и автоматизация",
  support: "Сопровождение CRM",
  other: "Другое направление",
  unknown: "Нужно уточнить",
};
export const attributeLabels = {
  category: "Направление",
  users: "Пользователи",
  budget_min: "Бюджет, от",
  budget_max: "Бюджет, до",
  currency: "Валюта",
  deadline_text: "Желаемый срок",
};
const number = new Intl.NumberFormat("ru-RU");
export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "")
    return "Не заполнено";
  if (typeof value === "number") return number.format(value);
  if (typeof value === "string")
    return categoryLabels[value as Category] || value;
  return "Значение заполнено";
}
export function formatBudget(budget: BudgetFact | null): string {
  if (!budget) return "Нужно уточнить";
  const { minimum, maximum, currency } = budget;
  const unit = currency
    ? { RUB: "₽", USD: "$", EUR: "€" }[currency]
    : "(валюта не указана)";
  if (minimum !== null && maximum !== null) {
    return `${minimum === maximum ? number.format(minimum) : `${number.format(minimum)}–${number.format(maximum)}`} ${unit}`;
  }
  return `${minimum === null ? "До" : "От"} ${number.format((minimum ?? maximum)!)} ${unit}`;
}
export const formatDate = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
