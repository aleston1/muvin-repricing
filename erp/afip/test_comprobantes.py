"""Tests de la lógica fiscal (no requieren red ni AFIP).

Correr:  python -m erp.afip.test_comprobantes
"""
from .comprobantes import (
    ItemComprobante, calcular_totales, clase_comprobante, doc_tipo_receptor,
    iva_array_wsfev1, tipo_comprobante,
)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_clase_y_tipo():
    # RI -> RI = Factura A (1)
    _assert(clase_comprobante("RI", "RI") == "A", "RI/RI debe ser A")
    _assert(tipo_comprobante("RI", "RI") == 1, "RI/RI factura = 1")
    # RI -> Consumidor Final = Factura B (6)
    _assert(clase_comprobante("RI", "CF") == "B", "RI/CF debe ser B")
    _assert(tipo_comprobante("RI", "CF") == 6, "RI/CF factura = 6")
    # RI -> Monotributo = Factura B
    _assert(tipo_comprobante("RI", "MONOTRIBUTO") == 6, "RI/Mono factura = 6")
    # Monotributo emisor -> siempre C
    _assert(tipo_comprobante("MONOTRIBUTO", "RI") == 11, "Mono emisor = C(11)")
    # Notas de crédito
    _assert(tipo_comprobante("RI", "RI", "nota_credito") == 3, "NC A = 3")
    _assert(tipo_comprobante("RI", "CF", "nota_credito") == 8, "NC B = 8")


def test_doc_tipo():
    _assert(doc_tipo_receptor("CUIT") == 80, "CUIT=80")
    _assert(doc_tipo_receptor("DNI") == 96, "DNI=96")
    _assert(doc_tipo_receptor(None, "CF") == 99, "CF sin doc = 99")


def test_totales_factura_a_sin_iva_incluido():
    # Precio sin IVA: 1000 x 2 = 2000 neto, IVA 21% = 420, total 2420
    items = [ItemComprobante("Zapatilla", 2, 1000, 21.0)]
    t = calcular_totales(items, clase="A", incluye_iva=False)
    _assert(t.neto == 2000.0, f"neto {t.neto}")
    _assert(t.iva == 420.0, f"iva {t.iva}")
    _assert(t.total == 2420.0, f"total {t.total}")


def test_totales_factura_b_precio_con_iva():
    # Precio final con IVA incluido: 1210 -> neto 1000, iva 210
    items = [ItemComprobante("Remera", 1, 1210, 21.0)]
    t = calcular_totales(items, clase="B", incluye_iva=True)
    _assert(t.neto == 1000.0, f"neto {t.neto}")
    _assert(t.iva == 210.0, f"iva {t.iva}")
    _assert(t.total == 1210.0, f"total {t.total}")


def test_totales_factura_c_sin_iva():
    # Monotributo: no discrimina IVA. total = suma bruta, iva = 0
    items = [ItemComprobante("Servicio", 1, 5000, 21.0)]
    t = calcular_totales(items, clase="C", incluye_iva=True)
    _assert(t.iva == 0.0, f"iva C debe ser 0, es {t.iva}")
    _assert(t.total == 5000.0, f"total C {t.total}")
    _assert(iva_array_wsfev1(t, "C") == [], "C no lleva array IVA")


def test_multi_alicuota():
    items = [
        ItemComprobante("A", 1, 1000, 21.0),   # neto 1000 iva 210
        ItemComprobante("B", 1, 1000, 10.5),   # neto 1000 iva 105
    ]
    t = calcular_totales(items, clase="A", incluye_iva=False)
    _assert(t.neto == 2000.0, f"neto {t.neto}")
    _assert(t.iva == 315.0, f"iva {t.iva}")
    arr = iva_array_wsfev1(t, "A")
    ids = sorted(x["Id"] for x in arr)
    _assert(ids == [4, 5], f"debe haber alícuotas 10.5(4) y 21(5): {ids}")


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"\n{len(tests)} tests OK")


if __name__ == "__main__":
    main()
