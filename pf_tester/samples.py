"""Hand-crafted prompts covering all 8 PII categories advertised by Privacy Filter.

Categories: account_number, private_address, private_email, private_person,
private_phone, private_url, private_date, secret.
"""

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
