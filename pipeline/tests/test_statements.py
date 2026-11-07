from decimal import Decimal

import pytest

from bankcheck.statements.camt053 import parse_camt053
from bankcheck.statements.detect import detect_format
from bankcheck.statements.mt940 import parse_mt940
from bankcheck.statements.n43 import parse_n43

def n43_file(lines: list[str]) -> bytes:
    return "\r\n".join(lines).encode("latin-1")


class TestN43:
    def lines(self) -> list[str]:
        return [
            "11" + "9403" + "5039" + "9731221318" + "240624" + "240731" + "2" + "00000002842835" + "978" + "3" + "VALTERMIA IBERICA, S.A.U.    ",
            "22    5039240624240625040112000000001164663697755853            ABONO RINV000302",
            "2301TRANSFERENCIA DE MOTORES EUROPA S.COOP.                                     ",
            "2302CONCEPTO: ABONO RINV0003027                                                 ",
            "22    5039240626240626998241000000029593019063729235            CF010024072509  ",
            "2301PAGO CONFIRMING                       REMESA CF010024072509 4 PROVEEDORES   ",
            "33" + "9403" + "5039" + "9731221318" + "00001" + "00000002959301" + "00001" + "00000000116466" + "2" + "00000000000000" + "978",
            "88999999999999999999000006",
        ]

    def test_reads_credit_and_debit_movements(self) -> None:
        parsed = parse_n43(n43_file(self.lines()))

        assert [line.amount_cents for line in parsed.lines] == [116466, -2959301]
        first = parsed.lines[0]
        assert first.booking_date == "2024-06-24"
        assert first.value_date == "2024-06-25"
        assert first.bank_code == "04"
        assert first.bank_subcode == "011"
        assert first.ref1 == ""
        assert first.ref2 == "ABONO RINV000302"
        assert first.src_line == 2

    def test_joins_38_character_fields_with_a_pipe_cutting_words_at_the_boundary(self) -> None:
        parsed = parse_n43(n43_file(self.lines()))

        assert parsed.lines[0].text == "TRANSFERENCIA DE MOTORES EUROPA S.COOP | . | CONCEPTO: ABONO RINV0003027"
        assert parsed.lines[1].text == "PAGO CONFIRMING | REMESA CF010024072509 4 PROVEEDORES"

    def test_keeps_the_leading_space_of_a_concept_field(self) -> None:
        lines = self.lines()
        lines[2] = "2301" + "A".ljust(38) + " 0504752".ljust(38)

        parsed = parse_n43(n43_file(lines))

        assert parsed.lines[0].text.startswith("A |  0504752")

    def test_trailer_is_read_as_a_control(self) -> None:
        parsed = parse_n43(n43_file(self.lines()))

        assert parsed.control.declared_debits == 1
        assert parsed.control.declared_credits == 1
        assert parsed.control.declared_debit_cents == 2959301
        assert parsed.control.declared_credit_cents == 116466
        assert parsed.control.problems() == []

    def test_a_missing_movement_breaks_the_control(self) -> None:
        lines = self.lines()
        del lines[4:6]

        parsed = parse_n43(n43_file(lines))

        assert any("debe" in problem or "cargos" in problem for problem in parsed.control.problems())

    def test_reads_the_original_amount_record(self) -> None:
        lines = self.lines()
        lines.insert(2, "2401" + "840" + "00000000123456" + " " * 59)

        parsed = parse_n43(n43_file(lines))

        assert parsed.lines[0].orig_currency == "USD"
        assert parsed.lines[0].orig_amount == Decimal("1234.56")

    def test_a_movement_cut_short_is_reported_not_swallowed(self) -> None:
        lines = self.lines()
        lines[1] = "22    5039240624"

        parsed = parse_n43(n43_file(lines))

        assert any("línea 2" in problem for problem in parsed.problems)


MT940_SAMPLE = "\r\n".join(
    [
        "{1:F01NORTGB2LAXXX0000000000}{2:O940}{4:",
        ":20:STMT240731037",
        ":25:GB32ZZES13180362025040",
        ":28C:00037/1",
        ":60F:C240701GBP1000,00",
        ":61:2407030701C21595,04NTRFNONREF//NO241834155327",
        ":86:FP BACKYARD REDCLIFFE INV# INV24-0105 01",
        "SEGUNDA LINEA",
        ":61:2407010701D100,00NCHG20240701618640//NO241835575883",
        ":86:COMISION",
        ":62F:C240731GBP22495,04",
        ":64:C240731GBP22495,04",
        "-}",
    ]
).encode("utf-8")


class TestMt940:
    def test_booking_date_is_the_entry_date_and_value_date_the_first_one(self) -> None:
        parsed = parse_mt940(MT940_SAMPLE)

        first = parsed.lines[0]
        assert first.booking_date == "2024-07-01"
        assert first.value_date == "2024-07-03"

    def test_reads_sign_type_and_references(self) -> None:
        parsed = parse_mt940(MT940_SAMPLE)

        assert [line.amount_cents for line in parsed.lines] == [2159504, -10000]
        assert parsed.lines[0].bank_code == "NTRF"
        assert parsed.lines[0].ref1 == "NONREF"
        assert parsed.lines[0].ref2 == "NO241834155327"
        assert parsed.lines[0].currency == "GBP"

    def test_joins_the_lines_of_the_text_field(self) -> None:
        parsed = parse_mt940(MT940_SAMPLE)

        assert parsed.lines[0].text == "FP BACKYARD REDCLIFFE INV# INV24-0105 01SEGUNDA LINEA"

    def test_reversal_marks_take_their_sign_from_the_last_letter(self) -> None:
        credit = parse_mt940(MT940_SAMPLE.replace(b"0701D100,00NCHG", b"0701RC100,00NMSC"))
        debit = parse_mt940(MT940_SAMPLE.replace(b"0701D100,00NCHG", b"0701RD100,00NMSC"))

        assert credit.lines[1].amount_cents == 10000
        assert debit.lines[1].amount_cents == -10000

    def test_a_continuation_line_starting_with_a_dash_is_text(self) -> None:
        data = MT940_SAMPLE.replace(b"SEGUNDA LINEA", b"-651266")

        parsed = parse_mt940(data)

        assert parsed.lines[0].text == "FP BACKYARD REDCLIFFE INV# INV24-0105 01-651266"

    def test_reads_the_original_amount_from_the_text(self) -> None:
        data = MT940_SAMPLE.replace(b"SEGUNDA LINEA", b"/OCMT/EUR34762,34/EXCH/10,585024")

        parsed = parse_mt940(data)

        assert parsed.lines[0].orig_currency == "EUR"
        assert parsed.lines[0].orig_amount == Decimal("34762.34")

    def test_entry_date_of_december_with_value_in_january_is_last_year(self) -> None:
        data = MT940_SAMPLE.replace(b":61:2407030701C", b":61:2501021231C")

        parsed = parse_mt940(data)

        assert parsed.lines[0].booking_date == "2024-12-31"
        assert parsed.lines[0].value_date == "2025-01-02"

    def test_balances_are_checked(self) -> None:
        parsed = parse_mt940(MT940_SAMPLE)
        assert parsed.control.problems() == []

        broken = parse_mt940(MT940_SAMPLE.replace(b":62F:C240731GBP22495,04", b":62F:C240731GBP1,00"))
        assert broken.control.problems()

    def test_an_unreadable_movement_is_reported(self) -> None:
        data = MT940_SAMPLE.replace(b":61:2407010701D100,00NCHG", b":61:GARBAGE")

        parsed = parse_mt940(data)

        assert any("línea 9" in problem for problem in parsed.problems)


CAMT_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
 <BkToCstmrStmt>
  <Stmt><Id>DE01-RHEI-EUR-20240701</Id>
   <Acct><Id><IBAN>DE17942741585670999198</IBAN></Id><Ccy>EUR</Ccy></Acct>
   <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">100.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
   <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">1026.55</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
   <Ntry><NtryRef>RHEI2407017480912</NtryRef><Amt Ccy="EUR">976.55</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts>
    <BookgDt><Dt>2024-07-01</Dt></BookgDt><ValDt><Dt>2024-07-02</Dt></ValDt><AcctSvcrRef>RHEI2407017480912</AcctSvcrRef>
    <BkTxCd><Domn><Cd>PMNT</Cd><Fmly><Cd>RCDT</Cd><SubFmlyCd>ESCT</SubFmlyCd></Fmly></Domn><Prtry><Cd>011</Cd></Prtry></BkTxCd>
    <NtryDtls><TxDtls><Refs><EndToEndId>PAY-1</EndToEndId></Refs><AmtDtls><InstdAmt><Amt Ccy="USD">1050.00</Amt></InstdAmt></AmtDtls>
    <RltdPties><Dbtr><Nm>Gartenbau GmbH</Nm></Dbtr><DbtrAcct><Id><IBAN>DE17945013815772355298</IBAN></Id></DbtrAcct></RltdPties>
    <RmtInf><Ustrd>RECHNUNG RE24011</Ustrd></RmtInf></TxDtls></NtryDtls><AddtlNtryInf>SEPA EREF+PAY-1 SVWZ+RECHNUNG RE24011</AddtlNtryInf></Ntry>
   <Ntry><NtryRef>RHEI2</NtryRef><Amt Ccy="EUR">50.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts>
    <BookgDt><Dt>2024-07-03</Dt></BookgDt><ValDt><Dt>2024-07-03</Dt></ValDt>
    <BkTxCd><Domn><Cd>PMNT</Cd><Fmly><Cd>ICDT</Cd><SubFmlyCd>ESCT</SubFmlyCd></Fmly></Domn></BkTxCd>
    <NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Proveedor SA</Nm></Cdtr><CdtrAcct><Id><IBAN>ES11</IBAN></Id></CdtrAcct></RltdPties></TxDtls></NtryDtls>
    <AddtlNtryInf>PAGO</AddtlNtryInf></Ntry>
  </Stmt>
 </BkToCstmrStmt>
</Document>
"""


class TestCamt:
    def test_reads_entries_with_sign(self) -> None:
        parsed = parse_camt053(CAMT_SAMPLE)

        assert [line.amount_cents for line in parsed.lines] == [97655, -5000]
        assert parsed.lines[0].booking_date == "2024-07-01"
        assert parsed.lines[0].value_date == "2024-07-02"
        assert parsed.lines[0].currency == "EUR"

    def test_reads_codes_parties_and_references(self) -> None:
        first, second = parse_camt053(CAMT_SAMPLE).lines

        assert first.bank_code == "PMNT/RCDT/ESCT"
        assert first.bank_subcode == "011"
        assert first.ref1 == "RHEI2407017480912"
        assert first.counterparty_name == "Gartenbau GmbH"
        assert first.counterparty_account == "DE17945013815772355298"
        assert first.end_to_end_id == "PAY-1"
        assert first.remittance_info == "RECHNUNG RE24011"
        assert first.text == "SEPA EREF+PAY-1 SVWZ+RECHNUNG RE24011"
        assert second.counterparty_name == "Proveedor SA"

    def test_keeps_free_text_byte_for_byte(self) -> None:
        data = CAMT_SAMPLE.replace(b"<Ustrd>RECHNUNG RE24011</Ustrd>", b"<Ustrd>REST </Ustrd>")

        assert parse_camt053(data).lines[0].remittance_info == "REST "

    def test_reads_the_instructed_amount(self) -> None:
        first = parse_camt053(CAMT_SAMPLE).lines[0]

        assert first.orig_amount == Decimal("1050.00")
        assert first.orig_currency == "USD"

    def test_balances_are_checked(self) -> None:
        assert parse_camt053(CAMT_SAMPLE).control.problems() == []

        broken = parse_camt053(CAMT_SAMPLE.replace(b">1026.55<", b">1.00<"))
        assert broken.control.problems()


class TestDetectFormat:
    @pytest.mark.parametrize(
        ("head", "expected"),
        [
            (b'<?xml version="1.0"?><Document', "camt053"),
            (b"{1:F01NORT", "mt940"),
            (b":20:STMT", "mt940"),
            (b"1194035039", "n43"),
        ],
    )
    def test_detects_by_content(self, head: bytes, expected: str) -> None:
        assert detect_format(head) == expected

    def test_unknown_content_returns_none(self) -> None:
        assert detect_format(b"hola") is None
