"""Lógica fiscal de comprobantes electrónicos (AR) — sin dependencia de red.

Todo lo que se puede validar sin AFIP vive acá y está cubierto por tests:
qué tipo de comprobante corresponde según la condición del emisor y del
receptor, el mapeo de tipos de documento, y el cálculo de neto/IVA/total.

Referencias WSFEv1 (códigos oficiales de AFIP/ARCA):
  CbteTipo:   1=Factura A  2=ND A  3=NC A
              6=Factura B  7=ND B  8=NC B
              11=Factura C 12=ND C 13=NC C
  DocTipo:    80=CUIT  86=CUIL  96=DNI  99=Consumidor Final (sin doc)
  Concepto:   1=Productos  2=Servicios  3=Productos y Servicios
  Iva Id:     3=0%  4=10.5%  5=21%  6=27%  8=5%  9=2.5%
"""
from dataclasses import dataclass, field

# --- Tipos de comprobante (CbteTipo) por clase (A/B/C) ---
CBTE = {
    "A": {"factura": 1, "nota_debito": 2, "nota_credito": 3},
    "B": {"factura": 6, "nota_debito": 7, "nota_credito": 8},
    "C": {"factura": 11, "nota_debito": 12, "nota_credito": 13},
}

# --- Tipo de documento del receptor (DocTipo) ---
DOC_TIPO = {"CUIT": 80, "CUIL": 86, "DNI": 96, "CDI": 87, "CF": 99}

# --- Alícuotas de IVA: Id AFIP -> porcentaje ---
IVA_ID = {3: 0.0, 4: 10.5, 5: 21.0, 6: 27.0, 8: 5.0, 9: 2.5}
IVA_ID_POR_PCT = {v: k for k, v in IVA_ID.items()}


def clase_comprobante(emisor_condicion, receptor_condicion):
    """Determina la CLASE (A/B/C) del comprobante.

    - Emisor Monotributo o Exento  -> siempre C.
    - Emisor Responsable Inscripto -> A si el receptor es RI; B en caso
      contrario (Monotributo, Exento, Consumidor Final).
    """
    emisor = (emisor_condicion or "").upper()
    receptor = (receptor_condicion or "").upper()
    if emisor in ("MONOTRIBUTO", "EXENTO"):
        return "C"
    if emisor == "RI":
        return "A" if receptor == "RI" else "B"
    raise ValueError(f"Condición de emisor no soportada: {emisor_condicion}")


def tipo_comprobante(emisor_condicion, receptor_condicion, documento="factura"):
    """Devuelve el CbteTipo numérico (p. ej. Factura B = 6)."""
    clase = clase_comprobante(emisor_condicion, receptor_condicion)
    if documento not in CBTE[clase]:
        raise ValueError(f"Documento inválido: {documento}")
    return CBTE[clase][documento]


def doc_tipo_receptor(tipo_doc, condicion_iva=None):
    """DocTipo AFIP del receptor. Consumidor Final sin documento -> 99."""
    if (condicion_iva or "").upper() == "CF" and not tipo_doc:
        return DOC_TIPO["CF"]
    return DOC_TIPO.get((tipo_doc or "CUIT").upper(), DOC_TIPO["CUIT"])


def _r2(x):
    return round(x + 1e-9, 2)


@dataclass
class ItemComprobante:
    """Un renglón. `precio_unitario` puede venir con o sin IVA según
    `incluye_iva`. `alicuota_pct` es el % de IVA del renglón (default 21)."""
    descripcion: str
    cantidad: float
    precio_unitario: float
    alicuota_pct: float = 21.0

    def neto_y_iva(self, incluye_iva):
        bruto = self.cantidad * self.precio_unitario
        if incluye_iva:
            neto = bruto / (1 + self.alicuota_pct / 100.0)
            iva = bruto - neto
        else:
            neto = bruto
            iva = neto * self.alicuota_pct / 100.0
        return _r2(neto), _r2(iva)


@dataclass
class TotalesComprobante:
    neto: float = 0.0
    iva: float = 0.0
    total: float = 0.0
    # Detalle por alícuota para el array 'Iva' de WSFEv1.
    iva_por_alicuota: dict = field(default_factory=dict)  # pct -> {"base","importe"}


def calcular_totales(items, clase="A", incluye_iva=False):
    """Suma neto/IVA/total y arma el detalle por alícuota.

    En comprobantes clase C (Monotributo/Exento) no se discrimina IVA: el
    total es la suma de los brutos y el importe de IVA es 0.
    """
    tot = TotalesComprobante()
    for it in items:
        if clase == "C":
            neto = _r2(it.cantidad * it.precio_unitario)
            iva = 0.0
            pct = 0.0
        else:
            neto, iva = it.neto_y_iva(incluye_iva)
            pct = it.alicuota_pct
        tot.neto = _r2(tot.neto + neto)
        tot.iva = _r2(tot.iva + iva)
        slot = tot.iva_por_alicuota.setdefault(pct, {"base": 0.0, "importe": 0.0})
        slot["base"] = _r2(slot["base"] + neto)
        slot["importe"] = _r2(slot["importe"] + iva)
    tot.total = _r2(tot.neto + tot.iva)
    return tot


def iva_array_wsfev1(totales, clase="A"):
    """Convierte el detalle por alícuota al array 'Iva' que espera WSFEv1.
    Clase C no lleva IVA."""
    if clase == "C":
        return []
    arr = []
    for pct, v in sorted(totales.iva_por_alicuota.items()):
        arr.append({
            "Id": IVA_ID_POR_PCT.get(pct, 5),  # default 21%
            "BaseImp": v["base"],
            "Importe": v["importe"],
        })
    return arr
