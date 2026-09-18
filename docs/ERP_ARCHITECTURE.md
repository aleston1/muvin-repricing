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

## Modo seguro (candado de conexiones externas)

Mientras el sistema está en construcción y validación, el ERP **no se conecta a
ninguna plataforma externa** (Tiendanube, MercadoLibre, AFIP) por defecto.
Cualquier operación que intente hacerlo se rechaza con HTTP 403 sin tocar nada
afuera. Esto está garantizado por código (`erp/seguridad.py`), no depende de
recordar no apretar un botón.

- Por defecto (sin configurar nada): **modo seguro** → cero conexiones externas.
- Para habilitar conexiones (recién cuando esté todo validado):
  `ERP_PERMITIR_CONEXIONES=1`.
- Estado visible en `GET /api/erp/modo-seguro` y en el badge del encabezado de
  la UI.

El candado protege: importación de Tiendanube, sincronización de ventas
(TN/ML) y emisión de facturas (AFIP). Todo lo demás (cargar productos, stock,
clientes, previsualizar comprobantes) es local y siempre funciona.

## Configuración

| Variable                  | Descripción                                          |
|---------------------------|------------------------------------------------------|
| `DATABASE_URL`            | PostgreSQL en producción. Sin ella, usa SQLite (`erp.db`). |
| `ERP_PERMITIR_CONEXIONES` | `1` para habilitar conexiones externas. Default: modo seguro (off). |

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

## Visión: ERP omnicanal

El ERP es el **centro** (fuente de verdad de stock, precios y clientes) y los
canales (Tiendanube, MercadoLibre) son satélites que se sincronizan en ambos
sentidos:

```
        ┌────────────┐        ┌──────────────┐
        │ Tiendanube │        │ MercadoLibre │
        └─────┬──────┘        └──────┬───────┘
     ventas/stock/precio      ventas/stock/precio
              └──────────┬───────────┘
                    ┌────▼─────┐
                    │   ERP    │  stock, precios, clientes, ventas,
                    │ (verdad) │  facturación, entregas
                    └────┬─────┘
                         │ export
                    ┌────▼─────┐
                    │ Contable │
                    └──────────┘
```

Flujos objetivo:
- **Ventas**: cada venta en TN/ML entra al ERP como pedido → descuenta stock →
  se factura (AFIP) → se prepara la entrega.
- **Stock**: un alta/baja de stock en el ERP se replica a TN y ML; una venta en
  un canal descuenta y actualiza el stock publicado en el otro.
- **Precios**: las listas del ERP publican precio a cada canal (con su markup),
  conectado al repricing existente.

## Hoja de ruta

### Fase 1 — Núcleo operativo ✅
Productos, variantes, depósitos, stock por movimientos, clientes con datos
fiscales, listas de precios, UI e importadores.

### Fase 2 — Población de datos maestros
La fuente de verdad es **Hansa**, no los canales. Por eso los datos maestros se
migran desde Hansa:
- **Migrador desde Hansa** ✅ (`erp/importar_hansa.py`): clientes, productos,
  variantes y stock desde export de texto, con mapeo de columnas configurable.
  Idempotente. Ver `docs/MIGRACION_HANSA.md`.
- **Importar de Tiendanube** ✅ (`erp/importar_tn.py`): sólo un **atajo opcional
  de arranque** para poder ver el sistema con datos reales antes de tener el
  export de Hansa. NO es la fuente de verdad.
- **Precios**: reglas sobre `ListaPrecios` (markup por marca/canal, redondeo).

### Fase 3 — Sincronización de ventas y stock (bidireccional) 🚧 (en curso)
- **Traer ventas** de TN y ML al ERP ✅ (`erp/ventas.py`, modelo `Pedido`):
  descuenta stock automáticamente, idempotente por (canal, id). Botón en la UI.
- Pendiente: **publicar el stock** actualizado del ERP hacia ambos canales
  (webhooks de TN/ML o polling), para el sentido inverso.

### Fase 4 — Entregas 🚧 (base lista)
Estado de cada pedido y marca de **entregado** ✅. Pendiente: remito y
integración con logística si aplica.

### Fase 5 — Facturación electrónica AFIP/ARCA 🚧 (implementada, en pausa)
Lógica fiscal + cliente WSAA/WSFEv1 **implementados y testeados**; falta el
certificado digital para probar en homologación. Ver `docs/AFIP_FACTURACION.md`.
Se retoma al final, cuando el sistema operativo ya esté andando. Facturará las
ventas que entren por los canales.

### Fase 6 — Compras y cuenta corriente
Órdenes de compra que generan ingresos de stock; cuenta corriente de clientes
y proveedores.

### Fase 7 — Migración y salida de Hansa
Migrar histórico y saldos desde Hansa (ver "Datos desde Hansa"), correr en
paralelo, y recién entonces dar de baja Hansa.

## Datos desde Hansa

Hansa es software propietario: sus archivos de base de datos están en un
**formato binario cerrado** que no se puede leer ni copiar como código. Lo que
sí se usa —y es muy valioso— es **exportar los datos** desde Hansa (Hansa
exporta registros a texto tabulado / Excel) para:
1. Replicar la estructura de campos que la operación realmente usa.
2. Migrar los datos reales al ERP (clientes, productos, stock, precios,
   histórico).

Con una muestra de export de Hansa se construye un importador específico
(similar a `erp/importar_tn.py`).

## Riesgos y responsabilidades (no técnicos)

- **Backups y confiabilidad**: al ser sistema de registro, la base necesita
  backups automáticos y un plan de restauración probado.
- **Migración desde Hansa**: suele ser la parte más tediosa (clientes, saldos,
  stock inicial, histórico).
- **Cumplimiento fiscal**: la facturación electrónica debe validarse contra
  homologación con un contador antes de emitir comprobantes reales.
