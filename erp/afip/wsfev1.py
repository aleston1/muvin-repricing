"""Cliente de los web services de facturación electrónica de AFIP/ARCA.

  - WSAA  (LoginCms): autenticación con certificado digital -> Ticket de Acceso.
  - WSFEv1 (service.asmx): FEDummy (salud), FECompUltimoAutorizado (próximo
    número) y FECAESolicitar (emitir y obtener el CAE).

IMPORTANTE: este cliente todavía NO fue probado contra AFIP porque requiere el
certificado digital (ver docs/AFIP_FACTURACION.md). La lógica fiscal previa
(erp/afip/comprobantes.py) sí está testeada. Acá el diseño está listo para
correr en HOMOLOGACIÓN apenas exista el certificado.

Configuración por variables de entorno:
    AFIP_CUIT        CUIT del emisor (sin guiones)
    AFIP_CERT_PATH   ruta al certificado .pem (o .crt) emitido por AFIP
    AFIP_KEY_PATH    ruta a la clave privada .key
    AFIP_MODO        'homologacion' (default) o 'produccion'
    AFIP_TA_DIR      dónde cachear el Ticket de Acceso (default: raíz del repo)
"""
import base64
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

URLS = {
    "homologacion": {
        "wsaa": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms?wsdl",
        "wsfev1": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL",
    },
    "produccion": {
        "wsaa": "https://wsaa.afip.gov.ar/ws/services/LoginCms?wsdl",
        "wsfev1": "https://servicios1.afip.gov.ar/wsfev1/service.asmx?WSDL",
    },
}


class AfipConfigError(RuntimeError):
    """Falta configuración (certificado, clave, CUIT) para operar con AFIP."""


class AfipError(RuntimeError):
    """AFIP devolvió un error (Observaciones/Errors) o falló la comunicación."""


def cargar_config():
    modo = os.environ.get("AFIP_MODO", "homologacion").lower()
    if modo not in URLS:
        raise AfipConfigError(f"AFIP_MODO inválido: {modo}")
    cfg = {
        "modo": modo,
        "cuit": os.environ.get("AFIP_CUIT", ""),
        "cert": os.environ.get("AFIP_CERT_PATH", ""),
        "key": os.environ.get("AFIP_KEY_PATH", ""),
        "ta_dir": os.environ.get("AFIP_TA_DIR",
                                 os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        **URLS[modo],
    }
    return cfg


def _falta_config(cfg):
    faltan = [k for k in ("cuit", "cert", "key") if not cfg.get(k)]
    if faltan:
        return f"Falta configuración de AFIP: {', '.join(faltan)}"
    for k in ("cert", "key"):
        if not os.path.exists(cfg[k]):
            return f"No existe el archivo {k}: {cfg[k]}"
    return None


# --------------------------------------------------------------- WSAA

def _tra_xml(servicio="wsfe", ttl_horas=12):
    ahora = datetime.now(timezone.utc)
    unique = int(ahora.timestamp())
    gen = (ahora - timedelta(minutes=10)).isoformat()
    exp = (ahora + timedelta(hours=ttl_horas)).isoformat()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0">'
        f'<header><uniqueId>{unique}</uniqueId>'
        f'<generationTime>{gen}</generationTime>'
        f'<expirationTime>{exp}</expirationTime></header>'
        f'<service>{servicio}</service></loginTicketRequest>'
    )


def _firmar_cms(tra_xml, cert_path, key_path):
    """Firma el TRA como CMS/PKCS#7 (DER, base64) usando openssl.
    Es el método clásico de PyAfipWs: portable y sin dependencias Python extra."""
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        f.write(tra_xml)
        tra_file = f.name
    try:
        out = subprocess.run(
            ["openssl", "cms", "-sign", "-in", tra_file, "-signer", cert_path,
             "-inkey", key_path, "-nodetach", "-outform", "DER"],
            capture_output=True, check=True)
        return base64.b64encode(out.stdout).decode()
    except FileNotFoundError:
        raise AfipConfigError("openssl no está disponible para firmar el TRA")
    except subprocess.CalledProcessError as e:
        raise AfipConfigError(f"Error firmando TRA: {e.stderr.decode()[:300]}")
    finally:
        os.unlink(tra_file)


def _ta_cache_path(cfg):
    return os.path.join(cfg["ta_dir"], f"afip_ta_{cfg['modo']}.xml")


def _ta_vigente(cfg):
    """Devuelve (token, sign) si hay un Ticket de Acceso cacheado y no vencido."""
    path = _ta_cache_path(cfg)
    if not os.path.exists(path):
        return None
    try:
        root = ET.parse(path).getroot()
        exp = root.findtext(".//expirationTime")
        if exp and datetime.fromisoformat(exp) > datetime.now(timezone.utc) + timedelta(minutes=10):
            return root.findtext(".//token"), root.findtext(".//sign")
    except Exception:
        return None
    return None


def autenticar(cfg=None):
    """Obtiene (token, sign) del WSAA, usando cache si sigue vigente."""
    cfg = cfg or cargar_config()
    err = _falta_config(cfg)
    if err:
        raise AfipConfigError(err)

    cacheado = _ta_vigente(cfg)
    if cacheado:
        return cacheado

    import zeep  # import diferido: solo se necesita al hablar con AFIP

    tra = _tra_xml("wsfe")
    cms = _firmar_cms(tra, cfg["cert"], cfg["key"])
    cliente = zeep.Client(cfg["wsaa"])
    respuesta = cliente.service.loginCms(cms)  # XML string
    root = ET.fromstring(respuesta)
    token = root.findtext(".//token")
    sign = root.findtext(".//sign")
    exp = root.findtext(".//expirationTime")
    # Guardar cache
    with open(_ta_cache_path(cfg), "w") as f:
        f.write(f"<ta><token>{token}</token><sign>{sign}</sign>"
                f"<expirationTime>{exp}</expirationTime></ta>")
    return token, sign


# --------------------------------------------------------------- WSFEv1

def _auth(cfg, token, sign):
    return {"Token": token, "Sign": sign, "Cuit": int(cfg["cuit"])}


def dummy(cfg=None):
    """FEDummy: chequea que WSFEv1 esté operativo (no requiere autenticación)."""
    cfg = cfg or cargar_config()
    import zeep
    cliente = zeep.Client(cfg["wsfev1"])
    r = cliente.service.FEDummy()
    return {"AppServer": r.AppServer, "DbServer": r.DbServer, "AuthServer": r.AuthServer}


def ultimo_autorizado(punto_venta, cbte_tipo, cfg=None):
    """FECompUltimoAutorizado: último número emitido para (PV, tipo).
    El próximo comprobante es este + 1."""
    cfg = cfg or cargar_config()
    token, sign = autenticar(cfg)
    import zeep
    cliente = zeep.Client(cfg["wsfev1"])
    r = cliente.service.FECompUltimoAutorizado(
        _auth(cfg, token, sign), int(punto_venta), int(cbte_tipo))
    return int(r.CbteNro)


def _revisar_errores(resp):
    errs = getattr(resp, "Errors", None)
    if errs and getattr(errs, "Err", None):
        detalle = "; ".join(f"[{e.Code}] {e.Msg}" for e in errs.Err)
        raise AfipError(f"AFIP rechazó la solicitud: {detalle}")


def emitir(punto_venta, cbte_tipo, doc_tipo, doc_nro, totales, iva_array,
           concepto=1, cfg=None):
    """FECAESolicitar: emite un comprobante y devuelve el CAE.

    `totales` es un TotalesComprobante (erp.afip.comprobantes) e `iva_array`
    el resultado de iva_array_wsfev1(). Devuelve dict con cae, vencimiento,
    número y resultado ('A'probado / 'R'echazado).
    """
    cfg = cfg or cargar_config()
    token, sign = autenticar(cfg)
    import zeep
    cliente = zeep.Client(cfg["wsfev1"])

    proximo = ultimo_autorizado(punto_venta, cbte_tipo, cfg) + 1
    hoy = datetime.now().strftime("%Y%m%d")
    detalle = {
        "Concepto": concepto,
        "DocTipo": doc_tipo,
        "DocNro": int(doc_nro or 0),
        "CbteDesde": proximo,
        "CbteHasta": proximo,
        "CbteFch": hoy,
        "ImpTotal": totales.total,
        "ImpTotConc": 0,
        "ImpNeto": totales.neto,
        "ImpOpEx": 0,
        "ImpIVA": totales.iva,
        "ImpTrib": 0,
        "MonId": "PES",
        "MonCotiz": 1,
    }
    if iva_array:
        detalle["Iva"] = {"AlicIva": iva_array}

    req = {
        "FeCabReq": {"CantReg": 1, "PtoVta": int(punto_venta), "CbteTipo": int(cbte_tipo)},
        "FeDetReq": {"FECAEDetRequest": [detalle]},
    }
    resp = cliente.service.FECAESolicitar(_auth(cfg, token, sign), req)
    _revisar_errores(resp)

    det = resp.FeDetResp.FECAEDetResponse[0]
    if det.Resultado != "A":
        obs = getattr(det, "Observaciones", None)
        msg = ""
        if obs and getattr(obs, "Obs", None):
            msg = "; ".join(f"[{o.Code}] {o.Msg}" for o in obs.Obs)
        raise AfipError(f"Comprobante rechazado: {msg or 'sin detalle'}")

    return {
        "resultado": det.Resultado,
        "cae": det.CAE,
        "cae_vencimiento": det.CAEFchVto,
        "numero": proximo,
        "punto_venta": int(punto_venta),
        "cbte_tipo": int(cbte_tipo),
    }


def estado_configuracion():
    """Diagnóstico rápido para la UI/API: ¿está lista la config de AFIP?"""
    cfg = cargar_config()
    err = _falta_config(cfg)
    return {
        "modo": cfg["modo"],
        "cuit": cfg["cuit"] or None,
        "configurado": err is None,
        "falta": err,
    }
