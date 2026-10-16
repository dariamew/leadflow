import pytest
from fastapi import HTTPException

from app.integrations.crm_text import crm_plain_text
from app.service import lead_text


@pytest.mark.parametrize(
    ("rich", "plain"),
    [
        ("[p]\nтестик\n[/p]", "тестик"),
        ("[P]Нужна [B]CRM[/B][/P][p]На 15 человек[/p]", "Нужна CRM\n\nНа 15 человек"),
        (
            "[p]Срок: [color=#ff0000]завтра[/color][br]Бюджет 100&nbsp;000 ₽[/p]",
            "Срок: завтра\nБюджет 100 000 ₽",
        ),
        ("[url=https://example.test]Наш сайт[/url]", "Наш сайт"),
        (
            "<p>Нужна <strong>CRM</strong></p><p>15 человек<br/>До 100&nbsp;000 ₽</p>",
            "Нужна CRM\n\n15 человек\nДо 100 000 ₽",
        ),
        ("<p>Заявка</p><script>hidden()</script><style>hidden {}</style>", "Заявка"),
        (
            "Бюджет < 100000, команда [10–15] человек [срочно]",
            "Бюджет < 100000, команда [10–15] человек [срочно]",
        ),
        ("Система <CRM> и &lt;ERP&gt;", "Система <CRM> и <ERP>"),
        ("[list][*]Сайт[*]Телефония[/list]", "Сайт\nТелефония"),
        ("[p] \r\n&nbsp;[/p]", ""),
    ],
)
def test_rich_text_becomes_readable_without_dropping_ordinary_notation(rich, plain):
    assert crm_plain_text(rich) == plain


def test_markup_only_lead_is_empty(settings):
    with pytest.raises(HTTPException) as error:
        lead_text({"comments": "[p]&nbsp;[/p]"}, settings)
    assert error.value.status_code == 422
