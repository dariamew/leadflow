from app.qualification.extractors import Extractor, validate_evidence
from app.schemas import Category, QualificationResult

PIPELINE_VERSION = "0.1.0"


class QualificationPipeline:
    def __init__(self, extractor: Extractor, budget_threshold: int = 100000):
        self.extractor = extractor
        self.budget_threshold = budget_threshold

    async def qualify(self, text: str) -> tuple[QualificationResult, dict[str, int]]:
        extraction = await self.extractor.extract(text)
        facts = extraction.facts
        validate_evidence(facts, text)
        missing, questions, reasons = [], [], []
        score = 0
        if facts.category in {Category.IMPLEMENTATION, Category.INTEGRATION, Category.SUPPORT}:
            score += 2
            reasons.append("Потребность соответствует одному из направлений услуг: +2.")
        if facts.category in {Category.UNKNOWN, Category.OTHER}:
            missing.append("service_fit")
            questions.append("Какую задачу вы хотите решить с помощью CRM или автоматизации?")
        if facts.users is None:
            missing.append("users")
            questions.append("Сколько сотрудников будут пользоваться системой?")
        if facts.budget is None:
            missing.append("budget")
            questions.append("Какой диапазон бюджета и в какой валюте вы рассматриваете?")
        elif facts.budget.currency is None:
            missing.append("budget_currency")
            questions.append("В какой валюте указан бюджет?")
        else:
            # A guaranteed lower bound, not an optimistic upper bound.
            if (
                facts.budget.currency == "RUB"
                and facts.budget.minimum is not None
                and facts.budget.minimum >= self.budget_threshold
            ):
                score += 1
                reasons.append("Нижняя граница бюджета соответствует порогу услуги: +1.")
        if facts.deadline is None:
            missing.append("deadline")
            questions.append("К какому сроку нужен результат?")
        if facts.users or facts.integrations:
            score += 1
            reasons.append("Есть конкретные требования по пользователям или интеграциям: +1.")
        if facts.integrations and len(questions) < 3:
            questions.append("Какие продукты и версии используются у указанных систем?")
        # Completeness has four dimensions: fit, users, budget+currency, deadline.
        result = QualificationResult(
            facts=facts,
            priority="elevated" if score >= 4 else "normal",
            priority_score=score,
            priority_reasons=reasons,
            completeness=round((4 - len(missing)) / 4, 2),
            missing_fields=missing,
            questions=questions[:3],
        )
        return result, extraction.token_usage
