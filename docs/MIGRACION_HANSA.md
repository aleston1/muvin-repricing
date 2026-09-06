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

## Qué necesito de Muvin

Una **muestra de export** de cada entidad (clientes, artículos, stock, precios).
Con eso fijo el mapeo exacto de columnas y la migración queda lista para correr
con los archivos completos.
