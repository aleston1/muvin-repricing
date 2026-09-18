# Facturación electrónica AFIP/ARCA — puesta en marcha

Este módulo (Fase 3) emite comprobantes electrónicos y obtiene el CAE contra
los web services de AFIP/ARCA. La **lógica fiscal** (tipo de comprobante,
cálculo de neto/IVA/total, numeración) está implementada y testeada. Falta un
único paso, que depende del contribuyente: **tramitar el certificado digital**.

## Qué está hecho

- `erp/afip/comprobantes.py` — lógica fiscal, con tests
  (`python -m erp.afip.test_comprobantes`).
- `erp/afip/wsfev1.py` — cliente WSAA (autenticación) + WSFEv1 (FEDummy,
  FECompUltimoAutorizado, FECAESolicitar).
- API: `/api/erp/facturacion/estado`, `/preview`, `/emitir`,
  `/api/erp/puntos-venta`, `/api/erp/comprobantes`.
- Se puede **previsualizar** un comprobante (tipo y totales) sin tocar AFIP.
  La **emisión real** requiere el certificado configurado.

## Tarea del lado de Muvin: certificado digital

1. Entrar a AFIP con **Clave Fiscal** y habilitar el servicio
   **"Administración de Certificados Digitales"** (WSASS).
2. Generar la **clave privada** y el **CSR** (pedido de certificado):
   ```bash
   openssl genrsa -out muvin.key 2048
   openssl req -new -key muvin.key -subj "/C=AR/O=Muvin/CN=muvin/serialNumber=CUIT <tu-cuit>" -out muvin.csr
   ```
3. Subir el `.csr` en el servicio de AFIP y **descargar el certificado** (`.pem`/`.crt`).
4. **Autorizar** ese certificado a usar el web service **`wsfe`** (Facturación
   Electrónica), en "Administración de Relaciones".
5. Empezar SIEMPRE en **homologación** (entorno de pruebas de AFIP). Los pasos
   1–4 se repiten para el entorno de producción cuando ya funcione en homologación.

> El certificado y la clave son secretos. **No** se commitean al repo: se
> cargan como archivos/variables de entorno en el servidor.

## Configuración (variables de entorno)

| Variable                | Descripción                                     |
|-------------------------|-------------------------------------------------|
| `AFIP_MODO`             | `homologacion` (default) o `produccion`         |
| `AFIP_CUIT`             | CUIT del emisor, sin guiones                     |
| `AFIP_CERT_PATH`        | Ruta al certificado `.pem`/`.crt`               |
| `AFIP_KEY_PATH`         | Ruta a la clave privada `.key`                  |
| `AFIP_EMISOR_CONDICION` | Condición IVA del emisor (`RI` default)         |
| `AFIP_TA_DIR`           | Carpeta para cachear el Ticket de Acceso        |

## Prueba en homologación (cuando esté el certificado)

```bash
# 1) ¿WSFEv1 está operativo?
python -c "from erp.afip import wsfev1; print(wsfev1.dummy())"

# 2) ¿Autentica el certificado? (obtiene token/sign)
python -c "from erp.afip import wsfev1; print(bool(wsfev1.autenticar()))"

# 3) Emitir una factura de prueba desde la API (/api/erp/facturacion/emitir)
```

## Detalles fiscales contemplados

- **Clase de comprobante** según condición del emisor y receptor:
  RI→RI = Factura A; RI→otros = Factura B; emisor Monotributo/Exento = Factura C.
- **Numeración por punto de venta y tipo** (se consulta el último autorizado a
  AFIP y se emite el siguiente).
- **CAE** y su vencimiento se guardan; el comprobante autorizado es inmutable
  (se anula con nota de crédito, no se edita ni borra).
- Multi-alícuota de IVA (21%, 10.5%, 27%, etc.) en el array `Iva` de WSFEv1.

## Pendientes para producción (siguientes pasos)

- Generar el **PDF del comprobante** con QR (RG 4291) para enviar al cliente.
- Manejo de **CAEA** (autorización anticipada) si el volumen lo justifica.
- Notas de crédito/débito asociadas a un comprobante origen.
- Validar con el **contador** antes de pasar a producción.
