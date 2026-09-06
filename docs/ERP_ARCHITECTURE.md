# ERP Muvin — Arquitectura y hoja de ruta

Objetivo: reemplazar **Hansa** por un ERP propio, de forma **incremental** y
conviviendo con Hansa durante la transición. Este documento describe la
decisión de alcance, la arquitectura y las fases.

## Decisión de alcance

Se construye el **ERP operativo** (stock, clientes, precios, facturación
electrónica, compras). La **contabilidad e impuestos** (libro IVA, plan de
cuentas, retenciones/percepciones, balances) **queda fuera del alcance
inicial**: se sigue exportando a la herramienta contable. Motivo: es la parte
más regulada y de mayor riesgo, y donde un error tiene costo fiscal.

Esto no cierra la puerta a incorporar contabilidad más adelante, pero primero
se estabiliza lo operativo.

## Por qué una base de datos de verdad

Hoy la operación usa Hansa espejado en un Google Sheet, y la app guarda datos
en archivos `.json` que **Render pierde al reiniciar** (`costos.json`,
`stock_cache.json`). Un ERP es el *sistema de registro*: necesita una base de
datos persistente con backups. Por eso la Fase 1 introduce **PostgreSQL**
(SQLite solo para desarrollo local).

## Arquitectura

```
app.py  (Flask)
 ├── sync_bp        /api/sync   → repricing ML + sincronización Tiendanube (existente)
 └── erp            /api/erp    → ERP (nuevo)
      ├── db.py       SQLAlchemy + PostgreSQL/SQLite
      ├── models.py   Producto, Variante, Deposito, StockDeposito,
      │               MovimientoStock, Cliente, ListaPrecios, PrecioLista
      ├── services.py lógica de stock (ledger)
      ├── importar.py puentes desde costos.json / stock_cache.json
      └── api/        productos, stock, clientes, precios
UI: static/erp.html  → /erp
```

Principios de diseño:

- **SKU raíz (6 caracteres)** sigue siendo la clave de negocio, igual que en el
  repricing y la sincronización de canales. Así el ERP se integra con lo que ya
  existe sin remapear nada.
- **Stock por movimientos (ledger).** La existencia por depósito es la suma de
  sus movimientos. Nunca se pisa un número a mano sin dejar el movimiento que lo
  justifica → auditable, requisito de cualquier ERP serio.
- **Módulos desacoplados** para que Facturación y Compras se enganchen sin
  reescribir el núcleo.

## Configuración

| Variable        | Descripción                                              |
|-----------------|----------------------------------------------------------|
| `DATABASE_URL`  | PostgreSQL en producción. Sin ella, usa SQLite (`erp.db`). |

En Render: agregar un PostgreSQL administrado y setear `DATABASE_URL`. La app
crea las tablas sola al arrancar (`db.create_all()`).

## Importar datos existentes

```bash
python -m erp.importar deposito-base   # crea depósito CENTRAL
python -m erp.importar costos          # costos.json  -> Producto.costo
python -m erp.importar stock           # stock_cache.json -> stock inicial
```

Son idempotentes (por SKU): se pueden correr las veces que haga falta durante
la convivencia con Hansa.

## API (Fase 1)

Base: `/api/erp`

| Método | Ruta                                   | Descripción                    |
|--------|----------------------------------------|--------------------------------|
| GET    | `/productos?q=&limit=&offset=`         | Listar/buscar productos        |
| POST   | `/productos`                           | Crear producto                 |
| GET    | `/productos/<id>`                      | Detalle con variantes          |
| PATCH  | `/productos/<id>`                      | Editar producto                |
| POST   | `/productos/<id>/variantes`            | Crear variante                 |
| GET    | `/depositos`                           | Listar depósitos               |
| POST   | `/depositos`                           | Crear depósito                 |
| GET    | `/stock?sku=&deposito_id=`             | Existencias                    |
| POST   | `/stock/movimientos`                   | Registrar ingreso/egreso/ajuste|
| GET    | `/stock/movimientos?sku=`              | Ledger de movimientos          |
| GET    | `/clientes?q=`                         | Listar/buscar clientes         |
| POST   | `/clientes`                            | Crear cliente (datos fiscales) |
| PATCH  | `/clientes/<id>`                       | Editar cliente                 |
| GET    | `/listas-precios`                      | Listar listas de precios       |
| POST   | `/listas-precios`                      | Crear lista                    |
| POST   | `/listas-precios/<id>/precios`         | Fijar precio de variante (upsert) |

## Hoja de ruta

### Fase 1 — Núcleo operativo ✅ (este cambio)
Productos, variantes, depósitos, stock por movimientos, clientes con datos
fiscales, listas de precios, UI e importadores.

### Fase 2 — Precios
Reglas de precio sobre `ListaPrecios`: markup por marca/categoría, redondeo,
precio por canal (ML/TN), y conexión con el repricing existente. Recalcular
listas desde el costo.

### Fase 3 — Facturación electrónica AFIP/ARCA 🚧 (en progreso)
Lógica fiscal + cliente WSAA/WSFEv1 implementados y testeados; falta el
certificado digital para probar en homologación. Ver `docs/AFIP_FACTURACION.md`.
El módulo más regulado. Punto crítico del proyecto.
- `WSAA` (autenticación con **certificado digital**) + `WSFEv1` (comprobantes).
- Entorno de **homologación** primero, luego **producción**.
- Librería recomendada: PyAfipWs o cliente SOAP con `zeep`. No escribir el SOAP
  a mano.
- Contemplar desde el diseño: numeración por **punto de venta**, tipo de
  comprobante según condición IVA del cliente, **CAE/CAEA**, y guardado
  inmutable del comprobante fiscal.
- Modelo nuevo: `Comprobante` (tipo, punto de venta, número, cliente, ítems,
  neto/IVA/total, CAE, vencimiento_CAE, estado).

### Fase 4 — Compras y cuenta corriente
Órdenes de compra que generan ingresos de stock; cuenta corriente de clientes
y proveedores.

### Fase 5 — Migración y salida de Hansa
Migrar histórico y saldos desde Hansa, correr en paralelo, y recién entonces
dar de baja Hansa.

## Riesgos y responsabilidades (no técnicos)

- **Backups y confiabilidad**: al ser sistema de registro, la base necesita
  backups automáticos y un plan de restauración probado.
- **Migración desde Hansa**: suele ser la parte más tediosa (clientes, saldos,
  stock inicial, histórico).
- **Cumplimiento fiscal**: la facturación electrónica debe validarse contra
  homologación con un contador antes de emitir comprobantes reales.
