from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from bankcheck.remittance.common import Advice, AdviceLine, check_control, parse_amount, parse_date
from bankcheck.remittance.csv_fmt import parse_csv_advice
from bankcheck.remittance.txt_fmt import parse_txt_advice
from bankcheck.remittance.xlsx_fmt import parse_xlsx_advice
from bankcheck.remittance.pdf_fmt import parse_pdf_text


class TestParseAmount:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1,234.56", "1234.56"),
            ("1.234,56", "1234.56"),
            ("54.018,81", "54018.81"),
            ("347,544.66", "347544.66"),
            ("-997.68", "-997.68"),
            ("-1.900,39", "-1900.39"),
            ("191,57", "191.57"),
            ("919.53", "919.53"),
            ("2176", "2176"),
        ],
    )
    def test_reads_both_locales(self, text: str, expected: str) -> None:
        assert parse_amount(text) == Decimal(expected)

    def test_empty_is_none(self) -> None:
        assert parse_amount("") is None
        assert parse_amount(None) is None

    def test_garbage_is_none(self) -> None:
        assert parse_amount("abc") is None


class TestParseDate:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("27/05/2025", "2025-05-27"),
            ("16/12/24", "2024-12-16"),
            ("2025-08-25", "2025-08-25"),
        ],
    )
    def test_reads_day_first_dates(self, text: str, expected: str) -> None:
        assert parse_date(text) == expected

    def test_month_first_when_asked(self) -> None:
        assert parse_date("09/29/2025", month_first=True) == "2025-09-29"
        assert parse_date("11/09/2025", month_first=True) == "2025-11-09"

    def test_impossible_date_falls_back_to_the_other_order(self) -> None:
        assert parse_date("29/09/2025", month_first=True) == "2025-09-29"

    def test_garbage_is_none(self) -> None:
        assert parse_date("nope") is None


CSV_COMMA = """payment_ref,9926070236,payment_date,2025-07-02,currency,USD
document,doc_date,gross,discount_or_deduction,reason,paid,your_order
1102501701,31/01/2025,1299.32,,,1299.32,4556996756
1102503275,24/02/2025,8873.30,-177.47,DISCOUNT,8695.83,
TOTAL,,,,,9995.15,
"""

CSV_SEMICOLON_WITH_REPEATED_LINE = """payment_ref;250825798384;payment_date;2025-08-25;currency;EUR
document;doc_date;gross;discount_or_deduction;reason;paid;your_order
FV25-0510414;27/05/2025;2176.04;;;2176.04;
FV25-0513458;02/07/2025;611.90;;;611.90;PED-27094
FV25-0513458;02/07/2025;611.90;;;611.90;PED-27094
TOTAL;;;;;2787.94;
"""


class TestCsvAdvice:
    def test_reads_header_lines_and_total(self) -> None:
        advice = parse_csv_advice(CSV_COMMA)

        assert advice.payment_ref == "9926070236"
        assert advice.payment_date == "2025-07-02"
        assert advice.currency == "USD"
        assert advice.control_amount == Decimal("9995.15")
        assert [line.document for line in advice.lines] == ["1102501701", "1102503275"]

    def test_reads_deduction_reason_and_order(self) -> None:
        first, second = parse_csv_advice(CSV_COMMA).lines

        assert first.your_order == "4556996756"
        assert second.deduction == Decimal("-177.47")
        assert second.reason == "DISCOUNT"
        assert second.paid == Decimal("8695.83")
        assert second.doc_date == "2025-02-24"

    def test_reads_the_semicolon_variant(self) -> None:
        advice = parse_csv_advice(CSV_SEMICOLON_WITH_REPEATED_LINE)

        assert advice.currency == "EUR"
        assert len(advice.lines) == 3

    def test_unknown_header_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            parse_csv_advice("payment_ref,1\nfoo,bar\n")


TXT_EN = """From: cuentas.pagar@seewilpoolte.example
To: cobros@valtermia.example
Date: Tue, 07 Oct 2025 09:16:00 +0100
Subject: Payment advice REM/2025/0259

Hello,

Please be advised that we have sent a payment of 6,983.27 CHF on 09/29/2025 covering:

  RE250104555        30/06/25        2,690.94
  RE250104302        18/06/25        6,363.86  -190.91 LOGISTIC FEE
  GS250100187        16/09/25       -997.68

Kind regards,
Seewil Pooltechnik GmbH
"""

TXT_ES = """From: cuentas.pagar@wellnessloma.example
Subject: Aviso de pago 8826091193

Les informamos de que hemos ordenado una transferencia por importe de 15.692,74 EUR con fecha 11/09/2025 que liquida los siguientes documentos:

  FV24-0224983       16/12/24       12.520,50
  FV25-0210437       09/05/25        3.172,24

Saludos cordiales,
Wellness Lomaseca S.A.
"""


class TestTxtAdvice:
    def test_reads_english_template(self) -> None:
        advice = parse_txt_advice(TXT_EN)

        assert advice.payment_ref == "REM/2025/0259"
        assert advice.currency == "CHF"
        assert advice.control_amount == Decimal("6983.27")
        assert advice.payment_date == "2025-09-29"
        assert len(advice.lines) == 3

    def test_paid_is_gross_plus_deduction(self) -> None:
        line = parse_txt_advice(TXT_EN).lines[1]

        assert line.gross == Decimal("6363.86")
        assert line.deduction == Decimal("-190.91")
        assert line.reason == "LOGISTIC FEE"
        assert line.paid == Decimal("6172.95")

    def test_reads_negative_credit_notes(self) -> None:
        assert parse_txt_advice(TXT_EN).lines[2].paid == Decimal("-997.68")

    def test_reads_spanish_template_with_day_first_date(self) -> None:
        advice = parse_txt_advice(TXT_ES)

        assert advice.payment_ref == "8826091193"
        assert advice.payment_date == "2025-09-11"
        assert advice.control_amount == Decimal("15692.74")
        assert advice.lines[0].gross == Decimal("12520.50")


PDF_TEXT_EN = """Hydro Roberts Pty Ltd REMITTANCE ADVICE
Payment ref. PAY-2025-001781
Date 04/07/2025
Payee Valtermia Australia Pty Ltd
Amount 7,614.71 AUD
Document Date Gross Deduction Paid
INV25-0310549 28/04/2025 2,191.35 2,191.35
INV25-0112228 30/05/2025 4,379.39 -875.87 PRICE DIFF 3,503.52
AB25-0500284 30/06/2025 -1,900.39 -1,900.39
INV25-0312834 30/05/2025 3,820.23 3,820.23
Documento sintético de prueba — sin validez fiscal / Synthetic test document
"""

PDF_TEXT_ES = """RY Rodríguez y Cortés Suministros S.A. AVISO DE PAGO
Ref. PAY-2025-001171
Fecha 30/06/2025
Beneficiario Valtermia Ibérica, S.A.U.
Importe 2.624,63 EUR
Documento Fecha Bruto Dto./ded. Pagado
FV24-0116657 13/09/2024 2.433,06 2.433,06
FV25-0100122 02/01/2025 191,57 191,57
"""


class TestPdfAdvice:
    def test_reads_english_template(self) -> None:
        advice = parse_pdf_text(PDF_TEXT_EN)

        assert advice.payment_ref == "PAY-2025-001781"
        assert advice.payment_date == "2025-07-04"
        assert advice.currency == "AUD"
        assert advice.control_amount == Decimal("7614.71")
        assert len(advice.lines) == 4

    def test_reads_deduction_with_reason(self) -> None:
        line = parse_pdf_text(PDF_TEXT_EN).lines[1]

        assert line.deduction == Decimal("-875.87")
        assert line.reason == "PRICE DIFF"
        assert line.paid == Decimal("3503.52")

    def test_reads_spanish_template(self) -> None:
        advice = parse_pdf_text(PDF_TEXT_ES)

        assert advice.payment_ref == "PAY-2025-001171"
        assert advice.currency == "EUR"
        assert advice.lines[1].paid == Decimal("191.57")

    def test_reads_a_deduction_glued_to_the_gross_amount(self) -> None:
        text = PDF_TEXT_ES.replace("FV25-0100122 02/01/2025 191,57 191,57", "FV25-0107006 10/04/2025 26.425,09-2.642,50 PENAL. RETRASO 23.782,59")

        line = parse_pdf_text(text).lines[1]

        assert line.gross == Decimal("26425.09")
        assert line.deduction == Decimal("-2642.50")
        assert line.reason == "PENAL. RETRASO"
        assert line.paid == Decimal("23782.59")

    def test_text_without_table_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            parse_pdf_text("Una factura cualquiera")


def write_xlsx(path: Path, rows: list[tuple]) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


class TestXlsxAdvice:
    def test_reads_document_layout(self, tmp_path: Path) -> None:
        path = write_xlsx(
            tmp_path / "a.xlsx",
            [
                ("Landscapes LLC", None, None, None, None, None),
                ("Payment advice", "REM/2025/1047", "2025-08-12", None, None, None),
                ("Document", "Date", "Gross", "Deduction", "Reason", "Paid"),
                ("1122509099", "2025-05-09", 774.61, 0, None, 774.61),
                ("1122508214", "2025-05-01", 2670.27, -26.70, "DISCOUNT", 2643.57),
                ("TOTAL", None, None, None, None, 3418.18),
            ],
        )

        advice = parse_xlsx_advice(path)

        assert advice.layout == "xlsx_document"
        assert advice.payment_ref == "REM/2025/1047"
        assert advice.payment_date == "2025-08-12"
        assert advice.control_amount == Decimal("3418.18")
        assert advice.lines[1].reason == "DISCOUNT"
        assert advice.lines[1].paid == Decimal("2643.57")
        assert advice.lines[0].deduction is None

    def test_reads_voucher_layout(self, tmp_path: Path) -> None:
        path = write_xlsx(
            tmp_path / "b.xlsx",
            [
                ("Schwimmbadbau GmbH", None, None, None, None, None, None),
                ("Payment advice", "2551800801", "2025-12-09", None, None, None, None),
                ("Voucher", "Invoice", "PO", "Inv. date", "Amount", "Deduction", "Net paid"),
                ("VCHR76444398", "107618", "4551146260", "2025-11-26", 2211, -22.11, 2188.89),
                ("VCHR90438632", "100239", None, "2025-12-03", -45.08, 0, -45.08),
                ("TOTAL", None, None, None, None, 2143.81, None),
            ],
        )

        advice = parse_xlsx_advice(path)

        assert advice.layout == "xlsx_voucher"
        first = advice.lines[0]
        assert first.document == "107618"
        assert first.voucher == "VCHR76444398"
        assert first.your_order == "4551146260"
        assert first.gross == Decimal("2211")
        assert first.deduction == Decimal("-22.11")
        assert first.paid == Decimal("2188.89")
        assert advice.control_amount == Decimal("2143.81")

    def test_unknown_layout_is_an_error(self, tmp_path: Path) -> None:
        path = write_xlsx(tmp_path / "c.xlsx", [("x",), ("y", "z", "w"), ("Foo", "Bar")])

        with pytest.raises(ValueError):
            parse_xlsx_advice(path)


def line(document: str, paid: str) -> AdviceLine:
    return AdviceLine(document=document, doc_date=None, gross=Decimal(paid), deduction=None, reason=None, paid=Decimal(paid), your_order=None, voucher=None)


def advice(lines: list[AdviceLine], control: str | None) -> Advice:
    amount = Decimal(control) if control else None
    return Advice(payment_ref="R", payment_date=None, currency="EUR", control_amount=amount, layout="t", lines=tuple(lines))


class TestCheckControl:
    def test_ok_when_the_sum_matches(self) -> None:
        result = check_control(advice([line("A", "10.00"), line("B", "5.50")], "15.50"))

        assert result.status == "ok"
        assert result.repeated == frozenset()

    def test_repeated_line_is_flagged_when_only_the_unique_sum_matches(self) -> None:
        result = check_control(advice([line("A", "10.00"), line("A", "10.00"), line("B", "5.50")], "15.50"))

        assert result.status == "ok_without_repeats"
        assert result.repeated == frozenset({1})

    def test_repeated_line_stands_when_the_full_sum_matches(self) -> None:
        result = check_control(advice([line("A", "10.00"), line("A", "10.00")], "20.00"))

        assert result.status == "ok"
        assert result.repeated == frozenset()

    def test_mismatch_is_reported(self) -> None:
        assert check_control(advice([line("A", "10.00")], "99.00")).status == "sum_mismatch"

    def test_no_control_amount(self) -> None:
        assert check_control(advice([line("A", "10.00")], None)).status == "no_control"

    def test_no_lines(self) -> None:
        assert check_control(advice([], "10.00")).status == "no_lines"
