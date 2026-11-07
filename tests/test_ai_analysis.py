"""Análisis de fallos con modelo: estadística local fiable y prompt acotado; el modelo solo propone."""
from bankrec import ai_analysis, llm


def case(rule="ref", good=3, ours=1, text="PAGO FRA FV25-1"):
    return {"txn": "T1", "text": text, "amount": "100.00 EUR", "rule": rule, "good_n": good, "ours_n": ours,
            "good": "A 1.00 ; B 2.00", "ours": "A 1.00"}


def test_resumen_cuenta_reglas_y_tamanos():
    cases = [case("ref", 3), case("ref", 20), case("fallback_receipt", 50), case("fallback_receipt", 400)]
    stats = ai_analysis.summarise(cases)
    assert stats["by_rule"] == {"ref": 2, "fallback_receipt": 2}
    assert stats["by_size"] == {"1-10": 1, "11-30": 1, "31-100": 1, ">100": 1}
    assert stats["missing_items"] == (3 - 1) + (20 - 1) + (50 - 1) + (400 - 1)


def test_resumen_sin_fallos():
    assert ai_analysis.summarise([]) == {"by_rule": {}, "by_size": {}, "missing_items": 0}


def test_prompt_incluye_restricciones_del_proyecto_y_limita_casos():
    cases = [case(text=f"T{i}") for i in range(40)]
    prompt = ai_analysis.build_prompt("ar_x", cases, ai_analysis.summarise(cases), max_cases=5)
    assert "ar_x" in prompt and prompt.count("TEXT:") == 5
    assert "no ids" in prompt.lower() and "exact" in prompt.lower()


def test_sin_modelo_se_imprime_solo_la_estadistica(monkeypatch, capsys):
    monkeypatch.delenv("BANKREC_LLM", raising=False)
    monkeypatch.setattr(ai_analysis, "failures", lambda phase, segment, limit: [case()])
    ai_analysis.main(["--phase", "sample", "--segment", "ar_x"])
    out = capsys.readouterr().out
    assert "by_rule" in out and "BANKREC_LLM=1" in out


def test_con_modelo_se_imprime_su_respuesta(monkeypatch, capsys):
    monkeypatch.setattr(ai_analysis, "failures", lambda phase, segment, limit: [case()])
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "ask", lambda prompt, system="", max_tokens=400: "HIPOTESIS: refs truncadas")
    ai_analysis.main(["--phase", "sample", "--segment", "ar_x"])
    assert "HIPOTESIS: refs truncadas" in capsys.readouterr().out
