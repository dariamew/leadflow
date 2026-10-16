import pytest

from app.qualification.extractors import (
    ExtractionError,
    RulesExtractor,
    validate_evidence,
)
from app.qualification.pipeline import QualificationPipeline
from app.schemas import Category, ExtractedFacts


@pytest.mark.parametrize(
    "text, minimum, maximum, currency",
    [
        ("Бюджет до 250 тысяч рублей", None, 250000, "RUB"),
        ("Бюджет от 100 до 200 тыс. рублей", 100000, 200000, "RUB"),
        ("Бюджет от 1,5 млн рублей", 1500000, None, "RUB"),
        ("Бюджет 150 000 RUB", 150000, 150000, "RUB"),
        ("Бюджет до 3000 USD", None, 3000, "USD"),
        ("Бюджет 100 тысяч", 100000, 100000, None),
        ("Бюджет 0 рублей", 0, 0, "RUB"),
    ],
)
async def test_budget_bounds(text, minimum, maximum, currency):
    facts = (await RulesExtractor().extract(text)).facts
    assert facts.budget is not None
    assert (facts.budget.minimum, facts.budget.maximum, facts.budget.currency) == (
        minimum,
        maximum,
        currency,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Бюджет пока не согласован",
        "Бюджет 100 тыс. Бюджет 200 тыс.",
        "Если увеличим команду, бюджет 200 тысяч рублей",
        "Бюджет от 300 до 100 тысяч рублей",
        "Бюджет 200 тысяч рублей, пока не согласован",
        "Бюджет " + "9" * 400 + " рублей",
    ],
)
async def test_ambiguous_budget_is_not_invented(text):
    assert (await RulesExtractor().extract(text)).facts.budget is None


async def test_full_request_has_verbatim_evidence_and_questions():
    text = (
        "Нужна CRM на 15 человек. Хотим подключить сайт и телефонию. "
        "Бюджет до 250 тысяч рублей. Желательно запуститься в следующем месяце."
    )
    result, usage = await QualificationPipeline(RulesExtractor()).qualify(text)
    assert result.facts.category == Category.IMPLEMENTATION
    assert result.facts.users.value == 15
    assert result.facts.budget.maximum == 250000
    assert [item.value for item in result.facts.integrations] == ["Сайт", "Телефония"]
    assert result.facts.deadline is not None
    assert result.completeness == 1
    assert result.priority == "normal"  # upper bound is not a guaranteed budget
    assert result.priority_score == 3
    validate_evidence(result.facts, text)
    assert usage == {}


async def test_empty_information_preserves_unknowns():
    result, _ = await QualificationPipeline(RulesExtractor()).qualify("Здравствуйте!")
    assert result.facts.category == Category.UNKNOWN
    assert result.facts.users is None and result.facts.budget is None
    assert result.completeness == 0
    assert len(result.questions) == 3
    assert result.priority == "normal"


@pytest.mark.parametrize(
    "text",
    ["Около 15 человек", "От 15 пользователей", "15 человек и 20 пользователей", "10–15 человек"],
)
async def test_users_ambiguity(text):
    assert (await RulesExtractor().extract(text)).facts.users is None


async def test_negated_integrations_are_not_extracted():
    facts = (await RulesExtractor().extract("Не нужно подключать сайт и телефонию.")).facts
    assert facts.integrations == []


def test_fabricated_evidence_is_rejected():
    facts = ExtractedFacts(
        category="support",
        category_evidence="нужна поддержка",
        users=None,
        budget=None,
        deadline=None,
        integrations=[],
        warnings=[],
    )
    with pytest.raises(ExtractionError):
        validate_evidence(facts, "Нужна другая услуга")


async def test_priority_is_explained_by_rules():
    result, _ = await QualificationPipeline(RulesExtractor()).qualify(
        "Нужна CRM на 10 пользователей. Бюджет от 200 тысяч рублей."
    )
    assert result.priority_score == 4
    assert result.priority == "elevated"
    assert len(result.priority_reasons) == 3
