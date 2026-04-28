"""Hand-crafted prompts covering all 8 PII categories advertised by Privacy Filter.

Categories: account_number, private_address, private_email, private_person,
private_phone, private_url, private_date, secret.
"""

from __future__ import annotations

SAMPLES: dict[str, str] = {
    "person_address_date": (
        "Alice Johnson was born on 1990-01-02 and currently lives at "
        "221B Baker Street, London, NW1 6XE."
    ),
    "email_phone": (
        "Please contact Bob Smith at bob.smith@example.com or by phone "
        "+1 (415) 555-0142 between 9am and 5pm PT."
    ),
    "url_account": (
        "Customer profile is available at https://crm.acme.io/users/42. "
        "His IBAN is DE89 3704 0044 0532 0130 00 and credit card "
        "4111 1111 1111 1111 expires 09/29."
    ),
    "secrets": (
        "Internal notes: prod database password is 'hunter2-blue!' and the "
        "deploy script reads OPENAI_API_KEY=sk-proj-AbCdEfGhIjKlMnOpQrStUv."
    ),
    "mixed_email_chain": (
        "From: Carol Diaz <carol.diaz@globex.com>\n"
        "To: legal@globex.com\n"
        "Date: 2025-11-14\n"
        "Subject: NDA follow-up\n\n"
        "Hi team, the signed NDA from Daniel Müller (DOB 1978-04-30, "
        "phone +49 30 1234567) is on our SharePoint: "
        "https://globex.sharepoint.com/sites/legal/NDA-Mueller.pdf."
    ),
    "russian_text": (
        "Иванов Иван Иванович, паспорт 4509 123456, проживает по адресу "
        "г. Москва, ул. Тверская, д. 7, кв. 12. Телефон: +7 (495) 123-45-67, "
        "email: ivan.ivanov@mail.ru."
    ),
    "russian_full": (
        "Заявитель: Петрова Мария Сергеевна, паспорт 4514 654321, выдан "
        "ОВД района Хамовники 12 января 2015 г.\n"
        "СНИЛС 123-456-789 01, ИНН 770123456789.\n"
        "Адрес: 119121, г. Москва, ул. Плющиха, д. 12, кв. 3.\n"
        "Телефоны: 8 (916) 555-12-34, +7 495 123-45-67.\n"
        "Email: maria.petrova@mail.ru, сайт: пример.рф/profile.\n"
        "Реквизиты карты: 4111 1111 1111 1111, ОГРН 1027700132195."
    ),
    "code_with_secret": (
        "def connect():\n"
        "    return psycopg2.connect(\n"
        "        host='db.internal.corp',\n"
        "        user='admin',\n"
        "        password='S3cr3t-PaS$word!',\n"
        "    )\n"
    ),
    "clean_no_pii": (
        "The quarterly report shows that revenue grew 12% year over year, "
        "driven mainly by international markets."
    ),
}
