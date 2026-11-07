"""Un cobro de un cliente que repite el importe de una factura ya liquidada es un pago duplicado (POLICIES §3)."""
from dataclasses import dataclass

from bankrec.duplicados import find_duplicate


@dataclass(frozen=True)
class Factura:
    id: str
    value: int


def valor(factura: Factura) -> int:
    return factura.value


def test_un_cobro_igual_a_una_factura_ya_cerrada_es_duplicado():
    cerrada = Factura("A", 1000)

    assert find_duplicate([cerrada], 1000, valor) == cerrada


def test_admite_dos_centimos_de_diferencia():
    cerrada = Factura("A", 1000)

    assert find_duplicate([cerrada], 1002, valor) == cerrada
    assert find_duplicate([cerrada], 1003, valor) is None


def test_elige_la_cerrada_mas_reciente():
    vieja, nueva = Factura("VIEJA", 1000), Factura("NUEVA", 1000)

    assert find_duplicate([vieja, nueva], 1000, valor) == nueva


def test_sin_cerradas_no_hay_duplicado():
    assert find_duplicate([], 1000, valor) is None
