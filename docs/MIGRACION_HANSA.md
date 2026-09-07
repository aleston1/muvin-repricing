# Migración de datos desde Hansa

El ERP reemplaza a Hansa como **fuente de verdad**. Los datos maestros (ítems,
clientes, stock, precios) se **migran una vez** desde Hansa; luego el ERP es el
master y los canales (Tiendanube, MercadoLibre) reflejan lo que el ERP tiene.

> **Importante sobre "ver los archivos de Hansa":** la base de datos de Hansa
> está en un formato binario propietario que no se puede leer como código. Lo
> que sí se usa es la función de **exportar** de Hansa, que genera archivos de
> **texto delimitado** (tabulado). Con esos export migramos todo.

## Paso 1 — Exportar desde Hansa

En cada módulo de Hansa (Clientes, Artículos, Stock, Precios) usar la rutina de
exportación para generar un `.txt`. Idealmente **con encabezado** (primera fila
con los nombres de columna). Con **una muestra de cada tipo** ya se puede armar
y validar el mapeo; después se corre con el archivo completo.

## Paso 2 — Mapeo de columnas

El importador (`erp/importar_hansa.py`) es **genérico**: no asume el formato de
Hansa, se le pasa un *mapeo* que dice qué columna del archivo corresponde a cada
campo del ERP. El valor del mapeo puede ser el **nombre de la columna** (si hay
encabezado) o el **índice** numérico (si no lo hay).

Campos por entidad:

- **Clientes**: `razon_social` (requerido), `nombre_fantasia`, `tipo_doc`,
  `nro_doc`, `condicion_iva`, `email`, `telefono`, `direccion`, `localidad`,
  `provincia`, `codigo_postal`.
- **Productos**: `sku` (requerido), `nombre`, `marca`, `categoria`, `costo`,
  `descripcion`, `color`, `talle`. Se agrupan por SKU raíz (6 caracteres).
- **Stock**: `sku` (requerido), `cantidad`, `deposito` (opcional).

La condición de IVA de Hansa se normaliza a `RI` / `MONOTRIBUTO` / `EXENTO` /
`CF` (mapa ajustable en el código).

## Paso 3 — Correr la migración

```python
from app import app
from erp import importar_hansa as h

with app.app_context():
    # Clientes
    filas = h.leer_filas("export_clientes.txt")
    print(h.importar_clientes(filas, {
        "razon_social": "Nombre", "nro_doc": "CUIT",
        "condicion_iva": "Condicion", "email": "Email", "localidad": "Ciudad",
    }))

    # Productos
    filas = h.leer_filas("export_articulos.txt")
    print(h.importar_productos(filas, {
        "sku": "Codigo", "nombre": "Descripcion", "marca": "Marca",
        "costo": "Costo", "color": "Color", "talle": "Talle",
    }))

    # Stock
    filas = h.leer_filas("export_stock.txt")
    print(h.importar_stock(filas, {"sku": "Codigo", "cantidad": "Cantidad"}))
```

Cada función devuelve estadísticas (creados / actualizados / omitidos) y es
**idempotente**: se puede correr de nuevo sin duplicar (el stock se concilia por
diferencia). Así se puede migrar en paralelo mientras Hansa sigue operativo.

## Detalles contemplados

- Números con **coma decimal** (`15000,50`) se parsean bien.
- Filas vacías o sin dato clave se omiten (van al contador `omitidos`).
- Codificación `utf-8-sig` (soporta el BOM que a veces agregan los export).
- Separador autodetectado (tab / `;` / `,`).

## Mapeos confirmados con los export reales de Muvin (6/sep)

### Artículos (`Items6sept.TXT`, tabulado, 23 columnas, ~10.360 ítems)
```python
mapeo_items = {
    "sku": "Cod",                 # A00001 (código único del ítem)
    "nombre": "Descripcion",
    "costo": "Precio Costo",      # ARS, formato 3.575,71
    "codigo_barras": "Cod Barra",
    "categoria": "Grupo",         # EMALT, AMEDI, ...
}
```
Nota: cada `Cod` es un ítem único (el color va en la descripción, p. ej.
"... - Brown"). Se agrupan por SKU raíz (6 caracteres). Migrado OK: 10.320
productos / 10.358 variantes.

### Stock (`Listastock6sept.TXT`, tabulado, 10 columnas, ~9.790 filas)
```python
mapeo_stock = {"sku": "Item", "cantidad": "En Stock"}
```
Migrado OK: 2.608 ítems con existencias (resto en 0). Ej.: A00001 → 39, A00023 → 1.

### Contactos (`Contactos.TXT`, tabulado, ~90 columnas)
Mapeo propuesto (a confirmar la columna de condición IVA con el archivo real):
```python
mapeo_clientes = {
    "codigo_externo": "Code",     # 0002, C00001 — clave de deduplicación
    "razon_social": "Name",
    "nombre_fantasia": "Person",
    "nro_doc": "VATNr",           # CUIT (ojo: consumidores con 11111111 ficticio)
    "email": "eMail",
    "telefono": "Phone",
    "direccion": "InvAddr0",
    "localidad": "InvAddr1",
    "codigo_postal": "InvAddr2",
    # "condicion_iva": "<columna con Resp. Insc. / Consum. Final>",
}
```
Pendiente: el archivo completo (20 MB) supera el límite de descarga de la
herramienta de Drive (10 MB). Se necesita dividirlo en partes <10 MB o
re-exportarlo con menos columnas para migrarlo y confirmar la columna de IVA.
