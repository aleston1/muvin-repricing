# muvin-repricing

## Asesor de compra (`/asesor`)

Página pública que funciona como un vendedor: hace preguntas (uso, distancia,
terreno, eléctrica, dónde se guarda, si queda en la calle, horario, altura,
presupuesto y qué accesorios ya tiene) y devuelve hasta 3 bicicletas con stock
**en el talle del cliente**, cada una con combos armados (Esencial: casco +
candado + luces; Completo: + inflador, portapaquetes/canasto, caramañola según
el uso) ajustados al presupuesto. Al final hay un chat con IA anclado en esos
productos para resolver dudas, y un botón de WhatsApp con todo precargado.

Las respuestas quedan en la URL (`#r=...`), así un resultado se puede
compartir o retomar.

Variables de entorno:

| Variable | Uso |
|---|---|
| `TN_STORE_ID`, `TN_TOKEN` | Lectura del catálogo de Tiendanube (obligatorias) |
| `ANTHROPIC_API_KEY` | Activa el chat "¿Te queda alguna duda?" (opcional) |
| `ASESOR_MODEL` | Modelo del chat (por defecto `claude-sonnet-5`) |
| `ASESOR_WHATSAPP` | Número para el botón de WhatsApp, ej. `5491122334455` (opcional) |
| `ASESOR_STORE_URL` | URL de la tienda (por defecto `https://www.muvin.com.ar`) |
| `ASESOR_CACHE_SEG` | Segundos de caché del catálogo (por defecto 900) |
| `ASESOR_SHEET_ID` | ID del Google Sheet de criterios (compartido "cualquiera con el enlace: lector"). Sin esto se usa `docs/asesor_criterios.xlsx` |
| `ASESOR_SHEET_CACHE_SEG` | Segundos de caché de la planilla (por defecto 300) |

**Regla de datos obligatorios:** un vehículo al que le falta UN dato obligatorio de
la planilla (ver solapa "Datos obligatorios"), o que no tiene foto, precio o
stock en Tiendanube, no se ofrece. `https://<este-servidor>/asesor/control`
muestra qué se ofrece, qué no y qué falta cargar; la planilla calcula lo mismo
en la columna "Qué falta" y en la solapa "Control". Las reglas están en
`asesor_datos.faltantes()`.

Los IDs de categorías de Tiendanube que usa están en `asesor.py`
(`CAT_BICIS`, `CAT_ACCESORIOS`). `POST /api/asesor/refrescar` fuerza a releer
el catálogo tras cambiar precios o stock.

Para mostrarlo dentro de la tienda: un botón/banner que lleve a
`https://<este-servidor>/asesor`, o embeberlo en una página de Tiendanube con
`<iframe src="https://<este-servidor>/asesor" style="width:100%;height:100vh;border:0"></iframe>`.
