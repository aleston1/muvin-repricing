from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.comments import Comment
import re

wb = Workbook()
H = Font(bold=True, color="FFFFFF"); HF = PatternFill("solid", fgColor="15181C")
SUB = PatternFill("solid", fgColor="EFECE4"); EST = PatternFill("solid", fgColor="FDF3D8"); FICHA = PatternFill("solid", fgColor="E5F4EA")
WR = Alignment(wrap_text=True, vertical="top")
def header(ws, cols, widths):
    ws.append(cols)
    for i, c in enumerate(ws[1], 1):
        c.font = H; c.fill = HF; c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[c.column_letter].width = widths[i-1]
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = "B2" if len(cols) > 8 else "A2"

# ------------------------------------------------------------ 1. Cómo usar
ws = wb.active; ws.title = "Cómo usar"
ws.column_dimensions["A"].width = 120
txt = [
 ("Mapa de criterios del Asesor Muvin", True),
 ("", False),
 ("Esta planilla define CÓMO recomienda el asesor. El código la lee cada pocos minutos: lo que cambies acá se refleja en la web sin programar.", False),
 ("Precio y stock NO se cargan acá: salen en vivo de Tiendanube. Un producto solo se recomienda si tiene stock en la variante (talle/color) que le sirve al cliente.", False),
 ("", False),
 ("Pestañas", True),
 ("• Etapas: las preguntas que hace el asesor, en orden, con sus opciones y qué criterio alimenta cada una.", False),
 ("• Criterios: el diccionario de cada criterio (qué significa, si es filtro o suma puntaje, valores posibles).", False),
 ("• Productos: una fila por producto de Tiendanube (vehículos). Acá se marca bajo qué criterios se recomienda cada uno.", False),
 ("• Accesorios: qué va en el combo (Esencial / Completo) según el vehículo y las respuestas.", False),
 ("• Sugerencias: TODAS las categorías de la tienda y cuándo mostrarlas después del combo ('Completá tu equipo', 'También te puede interesar', 'Para más adelante').", False),
 ("• Altura por modelo: una fila por bici y por talle con su rango de altura (varía por marca y por modelo). Verde = cargado, amarillo = estimado, rojo = completar.", False),
 ("• Talles genéricos: respaldo cuando un modelo no tiene guía cargada, y altura por rodado para infantiles.", False),
 ("• Listas: valores válidos para los desplegables.", False),
 ("", False),
 ("Colores en la pestaña Productos", True),
 ("Verde = dato tomado de la ficha del producto en Tiendanube.   Amarillo = estimado por tipo de producto: REVISAR.   Sin color = a completar.", False),
 ("", False),
 ("Reglas de oro", True),
 ("1. Si un producto no está en la pestaña Productos o tiene 'Activo en asesor' = No, el asesor nunca lo recomienda.", False),
 ("2. Productos nuevos: se agregan con su ID de Tiendanube (lo ves en la URL del admin). El asesor avisa en un log los productos con stock que no están mapeados.", False),
 ("3. 'Prioridad' (0 a 3) desempata: sirve para empujar lo que conviene vender (margen, sobrestock, lanzamientos).", False),
 ("4. Categorías nuevas (ej. patinetas, accesorios de camping): se agrega el tipo en Listas, sus preguntas en Etapas y sus productos en Productos.", False),
]
for t, b in txt:
    ws.append([t]); ws.cell(ws.max_row, 1).font = Font(bold=b, size=14 if b and ws.max_row == 1 else 11); ws.cell(ws.max_row, 1).alignment = WR

# ------------------------------------------------------------ 2. Etapas
ws = wb.create_sheet("Etapas")
header(ws, ["#", "Etapa", "Pregunta al cliente", "Opciones (lo que ve el cliente)", "Criterio que alimenta", "Cómo decide", "Se pregunta si…"],
       [4, 16, 34, 48, 22, 58, 30])
etapas = [
 (1, "Necesidad", "¿Qué querés resolver?",
  "(OPCIÓN MÚLTIPLE)\nMoverme todos los días (trabajo, estudio, trámites)\nPasear y disfrutar\nHacer deporte o salir a la naturaleza\nLlevar a los chicos\nLlevar carga (compras, trabajo, mascota)\nEs para un chico o chica",
  "Usos / Acepta silla de niños", "Se pueden marcar varias. FILTRO: el producto tiene que cumplir al menos una; cuantas más cumple, más puntaje. 'Llevar a los chicos' también lo cumple una bici con 'Acepta silla de niños' = Sí: en ese caso se suma la silla al combo. 'Llevar carga' suma portapaquetes, canasto o alforjas. Todavía no se habla de bici, monopatín ni moto.", "Siempre"),
 (2, "Distancia", "¿Cuántos km hacés por trayecto? (solo ida)",
  "Menos de 5 km\nEntre 5 y 15 km\nEntre 15 y 30 km\nMás de 30 km",
  "Autonomía (km) / Propulsión", "Eléctricos: FILTRO autonomía ≥ ida y vuelta × 1,5 (margen por frío, subidas, peso). Sin motor: más de 15 km suma puntaje a los eléctricos y a bicis de ruta/gravel.", "Necesidad ≠ chico"),
 (3, "Terreno", "¿Cómo es el recorrido?",
  "Llano, asfalto o bicisenda\nCon subidas o puentes\nTierra, ripio o senderos\nUn poco de todo",
  "Terreno apto / Subida máx (%)", "FILTRO: el terreno tiene que estar entre los aptos. Con subidas suma a eléctricos y a monopatines con subida máx ≥ 20%.", "Siempre"),
 (4, "Esfuerzo", "¿Cuánto querés pedalear?",
  "Quiero pedalear, también es ejercicio\nPedalear, pero con ayuda de un motor\nNada, quiero que me lleve",
  "Propulsión", "FILTRO: Pedal → bicis sin motor. Ayuda → bicis eléctricas (pedaleo asistido). Nada → monopatines, motos y e-bikes con acelerador. Es la pregunta que separa las categorías.", "Necesidad ≠ chico"),
 (5, "Velocidad y papeles", "¿Por dónde vas a circular?",
  "Bicisendas y calles tranquilas (hasta 25 km/h)\nCalles y avenidas (hasta 45 km/h)\nAvenidas rápidas y accesos (más de 45 km/h)",
  "Velocidad máx / Requiere licencia", "FILTRO por velocidad máx. Si elige más de 45 km/h se aclara que requiere licencia y patente, y se pregunta si la tiene o la sacaría.", "Esfuerzo = Nada"),
 (6, "Portabilidad", "¿Lo tenés que cargar o combinar con otro transporte?",
  "Lo subo por escaleras\nLo combino con tren, subte o auto\nLo guardo en planta baja, cochera o patio",
  "Plegable / Peso (kg)", "Escaleras: FILTRO peso ≤ 20 kg (suma si ≤ 15). Combinar: FILTRO plegable = Sí. Planta baja: sin restricción.", "Necesidad ≠ chico"),
 (7, "Estacionamiento", "¿Lo vas a dejar estacionado en la calle?",
  "Sí, en la calle\nNo, lo guardo adentro", "Accesorios (candado)", "Define el candado del combo (alta seguridad vs. liviano). No filtra vehículos.", "Siempre"),
 (8, "Noche", "¿Vas a circular de noche?",
  "Casi siempre de día\nSí, también de noche", "Accesorios (luces / reflectivos)", "Define luces y chaleco del combo. No filtra vehículos.", "Siempre"),
 (9, "Altura", "¿Cuánto medís?", "Deslizador 90–210 cm",
  "Altura mín / máx / Talles", "FILTRO: altura dentro del rango del producto. Si tiene talles, se busca una variante CON STOCK en el talle que corresponde según la tabla DE ESE MODELO (pestaña 'Altura por modelo'). Si el modelo no tiene tabla cargada, se usa 'Talles genéricos' y el asesor lo muestra como talle orientativo.", "Siempre (para chicos: altura del chico)"),
 (10, "Peso", "¿Cuánto pesás aproximadamente?", "Menos de 70 kg\n70 a 90 kg\n90 a 110 kg\nMás de 110 kg",
  "Carga máx (kg)", "FILTRO: peso de la persona + 10 kg ≤ carga máxima. Clave en monopatines (algunos soportan solo 90 kg).", "Propulsión ≠ Pedal"),
 (11, "Presupuesto", "¿Cuánto querés invertir en total?", "Rangos + monto libre + 'No tengo tope'",
  "Precio (en vivo de TN)", "Se reserva lo mínimo de los accesorios esenciales y el resto es tope del vehículo. Hasta +10% se muestra avisando cuánto se pasa.", "Siempre"),
 (12, "Ya tengo", "¿Ya tenés alguno de estos?", "Casco / Candado / Luces / Inflador (según tipo de vehículo)",
  "Accesorios", "Lo que ya tiene no entra en el combo.", "Siempre"),
 ("—", "Stock", "(automático)", "—", "Stock TN", "FILTRO final: solo productos publicados con stock en la variante que le sirve. Nada sin stock llega al cliente.", "Siempre"),
 ("—", "Completá tu equipo", "(automático, después del combo)", "Kit de herramientas, cámara de repuesto, sellador, etc.", "Pestaña Sugerencias", "Productos útiles según el vehículo elegido, fuera del combo. Uno por categoría, con stock.", "Siempre"),
 ("—", "También te puede interesar", "(automático, al final)", "Limpieza, lubricación, indumentaria, intercomunicadores (moto), etc.", "Pestaña Sugerencias", "Vidriera de TODO el catálogo relacionado. Rota los productos entre visitas para que cada producto de la web se muestre alguna vez.", "Siempre"),
]
for e in etapas: ws.append(list(e))
for row in ws.iter_rows(min_row=2):
    for c in row: c.alignment = WR

# ------------------------------------------------------------ 3. Criterios
ws = wb.create_sheet("Criterios")
header(ws, ["Criterio (columna en Productos)", "Qué significa", "Tipo", "Valores", "Lo usa la etapa"], [26, 60, 14, 40, 18])
crit = [
 ("Tipo de vehículo", "Familia del producto. Define qué accesorios lleva el combo y cómo se presenta.", "Info", "Ver Listas", "—"),
 ("Propulsión", "Pedal = sin motor. Asistida = motor que ayuda al pedalear. Eléctrica = anda sola (acelerador).", "Filtro", "Pedal / Asistida / Asistida + acelerador / Eléctrica", "4"),
 ("Uso: diario / paseo / deporte / llevar chicos / llevar carga / niños", "Marcar Sí en cada necesidad para la que se recomienda. Un producto puede tener varios usos.", "Filtro", "Sí / No", "1"),
 ("Acepta silla de niños", "Si se le puede montar una silla para chicos (portapaquetes o tubo de asiento compatible). Hace que cuente para 'Llevar a los chicos' y suma la silla al combo.", "Filtro", "Sí / No / Revisar", "1"),
 ("Neumáticos con aire", "Si usa cubiertas con aire (con cámara o tubeless). Define si el combo lleva inflador.", "Accesorios", "Sí / No", "—"),
 ("Terreno apto", "Dónde anda bien.", "Filtro", "Asfalto / Asfalto y mixto / Mixto y tierra / Todo terreno", "3"),
 ("Autonomía (km)", "Autonomía real a considerar (usar el valor de ficha).", "Filtro", "Número", "2"),
 ("Velocidad máx (km/h)", "Según ficha.", "Filtro", "Número", "5"),
 ("Requiere licencia/patente", "Si hace falta licencia de conducir y patentamiento.", "Filtro", "Sí / No / Revisar", "5"),
 ("Plegable", "Si se pliega para guardar o subir al transporte.", "Filtro", "Sí / No / Parcial", "6"),
 ("Peso (kg)", "Peso del vehículo. Clave para escaleras.", "Filtro", "Número", "6"),
 ("Carga máx (kg)", "Peso máximo soportado (persona + carga).", "Filtro", "Número", "10"),
 ("Altura mín / máx (cm)", "Rango de altura del usuario. Si tiene talles, el rango lo da la pestaña Talles.", "Filtro", "Número", "9"),
 ("Talles por variante", "Sí = el talle está en las variantes de TN y se usa la pestaña Talles.", "Filtro", "Sí / No", "9"),
 ("Subida máx (%)", "Pendiente máxima (monopatines y motos).", "Puntaje", "Número", "3"),
 ("Nivel", "Posicionamiento: ayuda a ordenar dentro del presupuesto.", "Puntaje", "Entrada / Medio / Alto", "11"),
 ("Prioridad", "Empujón comercial para desempatar (0 = neutro, 3 = máximo).", "Puntaje", "0 a 3", "—"),
 ("Activo en asesor", "No = nunca se recomienda (usados, repuestos, productos que no queremos empujar).", "Filtro", "Sí / No", "—"),
 ("Argumento de venta", "Frase corta que el asesor muestra en '¿Por qué te lo recomendamos?'. Si está vacía, se arma sola.", "Texto", "Texto libre", "—"),
]
for c in crit: ws.append(list(c))
for row in ws.iter_rows(min_row=2):
    for c in row: c.alignment = WR

# ------------------------------------------------------------ 4. Productos
ws = wb.create_sheet("Productos")
cols = ["ID Tiendanube", "Nombre", "Categoría en TN", "Tipo de vehículo", "Propulsión",
        "Uso: diario", "Uso: paseo", "Uso: deporte / naturaleza", "Uso: llevar chicos", "Uso: llevar carga", "Uso: niños", "Acepta silla de niños", "Neumáticos con aire",
        "Terreno apto", "Autonomía (km)", "Velocidad máx (km/h)", "Requiere licencia/patente",
        "Plegable", "Peso (kg)", "Carga máx (kg)", "Altura mín (cm)", "Altura máx (cm)", "Talles por variante",
        "Subida máx (%)", "Nivel", "Prioridad", "Activo en asesor", "Argumento de venta", "Notas / revisar"]
header(ws, cols, [13, 30, 20, 20, 18, 9, 9, 11, 11, 11, 9, 11, 11, 18, 11, 11, 12, 10, 9, 10, 10, 10, 10, 10, 10, 9, 10, 40, 44])

rows = []
def add(pid, nombre, cat, tipo, prop, usos, terreno, aut=None, vel=None, lic="No", pleg="No", peso=None, carga=None,
        amin=None, amax=None, talles="No", subida=None, nivel=None, activo="Sí", arg="", notas="", ficha=(), est=(),
        silla=None, aire="Sí"):
    if silla is None:
        silla = "Sí" if "chicos" in usos else ("Revisar" if prop == "Pedal" or prop.startswith("Asistida") else "No")
        if silla == "Revisar": est = tuple(est) + ("silla",)
    rows.append(dict(v=[pid, nombre, cat, tipo, prop] + [("Sí" if u in usos else "No") for u in ("diario","paseo","deporte","chicos","carga","ninos")] + [silla, aire] +
                    [terreno, aut, vel, lic, pleg, peso, carga, amin, amax, talles, subida, nivel, 0, activo, arg, notas],
                     ficha=set(ficha), est=set(est)))

# Monopatines (datos de ficha)
F = ("aut","vel","peso","carga","pleg","subida")
add(282264187,"Max G3","Monopatines","Monopatín eléctrico","Eléctrica",{"diario","paseo"},"Asfalto",80,45,"No","Sí",24.6,130,None,None,subida=30,nivel="Alto",ficha=F,arg="80 km de autonomía y doble suspensión hidráulica: el más completo para ir lejos todos los días.",notas="Velocidad máx 45 km/h: revisar normativa local de monopatines.")
add(282311179,"GT3","Monopatines","Monopatín eléctrico","Eléctrica",{"diario","paseo"},"Asfalto",95,50,"No","Sí",39.5,150,subida=30,nivel="Alto",ficha=F,notas="39,5 kg: no apto para subir escaleras. Velocidad 50 km/h: revisar normativa.")
add(297070699,"E2 PLUS E II","Monopatines","Monopatín eléctrico","Eléctrica",{"diario"},"Asfalto",25,25,"No","Sí",15,90,subida=12,aire="No",nivel="Entrada",ficha=F,arg="Liviano (15 kg) y simple: ideal para trayectos cortos y para subirlo a casa.",notas="Carga máx 90 kg y subida 12%: solo llano.")
add(312062393,"X30","Monopatines","Monopatín eléctrico","Eléctrica",{"diario"},"Asfalto",50,25,"No","Sí",20,120,aire="No",nivel="Medio",ficha=("aut","vel","peso","carga"),est=("pleg",),notas="Micro. Confirmar que es plegable.")
add(363243370,"F3 Pro","Monopatines","Monopatín eléctrico","Eléctrica",{"diario"},"Asfalto",70,32,"No","Sí",19.3,120,subida=24,nivel="Medio",ficha=F)
# Motos
M = ("aut","vel","lic","peso","carga")
add(260345249,"TC Max","Motos eléctricas","Moto eléctrica","Eléctrica",{"diario","paseo"},"Asfalto",110,100,"Sí","No",105,150,nivel="Alto",ficha=M,notas="Altura de asiento 77 cm. Envío a cotizar.")
add(260346171,"CUx","Motos eléctricas","Moto eléctrica (scooter)","Eléctrica",{"diario"},"Asfalto",65,60,"Sí","No",64,None,nivel="Medio",ficha=("aut","vel","lic","peso"),notas="Peso sin batería. Envío a cotizar.")
add(260347602,"F01","Motos eléctricas","Moto eléctrica (scooter)","Eléctrica",{"diario"},"Asfalto",80,77,"Sí","No",92,None,nivel="Medio",ficha=("aut","vel","lic","peso"),notas="Peso sin batería (+18 kg batería).")
add(260683064,"CPX","Motos eléctricas","Moto eléctrica (scooter)","Eléctrica",{"diario","carga"},"Asfalto",70,90,"Sí","No",None,None,nivel="Alto",ficha=("aut","vel","lic"),notas="Equivale a 125 cc. Ampliable a 2 baterías.")
add(263947898,"CPX (2 Baterías 74V)","Motos eléctricas","Moto eléctrica (scooter)","Eléctrica",{"diario","carga"},"Asfalto",140,90,"Sí","No",None,None,nivel="Alto",ficha=("aut","vel","lic"))
add(327905251,"CPX Explorer (2 baterías)","Motos eléctricas","Moto eléctrica (scooter)","Eléctrica",{"diario","carga"},"Asfalto",None,105,"Sí","No",None,None,nivel="Alto",ficha=("vel","lic"),notas="Completar autonomía (la ficha no la dice).")
add(327910096,"CITI","Motos eléctricas","Moto eléctrica","Eléctrica",{"diario"},"Asfalto",107,80,"Sí","No",None,None,nivel="Medio",ficha=("aut","vel","lic"))
# E-bikes
E = "Bicicletas eléctricas"
add(260342361,"2Fold 20",E,"Bici eléctrica plegable","Asistida + acelerador",{"diario","paseo"},"Asfalto",40,25,"No","Sí",17.5,115,140,185,ficha=("aut","vel","pleg","peso","carga","amin","amax"),nivel="Medio",arg="La e-bike plegable más liviana: 17,5 kg, se guarda bajo el escritorio.")
add(260343629,"HSD P9",E,"Bici eléctrica de carga","Asistida",{"diario","carga","chicos"},"Asfalto y mixto",120,None,"No","Sí",25.7,170,150,195,ficha=("aut","pleg","peso","carga","amin","amax"),nivel="Alto",arg="Lleva a los chicos o 60 kg atrás y se guarda vertical ocupando poco lugar.",notas="Carga total 170 kg / usuario hasta 120 kg.")
add(260344367,"2Fold 20 Fat",E,"Bici eléctrica plegable","Asistida + acelerador",{"diario","paseo"},"Asfalto y mixto",40,32,"No","Sí",20,115,140,185,ficha=("aut","vel","pleg","peso","carga","amin","amax"),nivel="Medio")
add(260347235,"Brina2 X1000",E,"Bici eléctrica urbana/MTB","Asistida + acelerador",{"diario","paseo","deporte"},"Asfalto y mixto",70,38,"Revisar","No",22,None,ficha=("aut","vel","peso"),nivel="Alto",notas="1000 W y 38 km/h: revisar si requiere patente.")
add(263963220,"Quick Haul P9",E,"Bici eléctrica de carga","Asistida",{"diario","carga","chicos"},"Asfalto",106,None,"No","No",23,150,145,195,ficha=("aut","peso","carga","amin","amax"),nivel="Alto",notas="Autonomía 53–106 km según modo. 145 cm con caño de asiento corto.")
add(264034402,"Nbd S5I",E,"Bici eléctrica urbana","Asistida",{"diario","paseo"},"Asfalto",118,None,"No","Parcial",None,120,ficha=("aut","carga"),nivel="Alto",notas="Autonomía 51–118 km. Confirmar plegado (ficha: 'tiempo de plegado 5 s').")
add(273218901,"Slim",E,"Bici eléctrica plegable","Asistida + acelerador",{"diario"},"Asfalto",40,30,"No","Sí",18,None,ficha=("aut","vel","pleg","peso"),nivel="Medio")
add(273255228,"X350",E,"Bici eléctrica plegable (rodado ancho)","Asistida + acelerador",{"diario","paseo"},"Todo terreno",35,40,"Revisar","Sí",None,None,ficha=("aut","vel","pleg"),nivel="Medio",notas="Doble suspensión, 20x4.0.")
add(273267951,"Enduro Pro X",E,"Bici eléctrica plegable (rodado ancho)","Asistida + acelerador",{"diario","paseo"},"Todo terreno",50,40,"Revisar","Sí",None,None,ficha=("aut","vel","pleg"),nivel="Medio")
add(291838862,"Rider FT01 - 750W",E,"Bici eléctrica tipo moto","Asistida + acelerador",{"diario","paseo"},"Todo terreno",70,45,"Revisar","No",49,120,ficha=("aut","vel","peso","carga"),nivel="Medio",notas="49 kg: no apta para escaleras.")
add(314027238,"Segway Xyber",E,"Bici eléctrica tipo moto","Asistida + acelerador",{"diario","paseo"},"Todo terreno",90,55,"Revisar","No",63,180,ficha=("aut","vel","peso","carga"),nivel="Alto")
add(314180547,"Chopper FT02",E,"Bici eléctrica tipo moto","Asistida + acelerador",{"diario","paseo"},"Todo terreno",75,60,"No","No",None,120,ficha=("aut","vel","carga","lic"),nivel="Medio",arg="Anda como una moto, pero sin patente ni licencia.")
add(321841921,"Vecocraft Foldy-E Einhell",E,"Bici eléctrica plegable","Asistida",{"diario"},"Asfalto",30,25,"No","Sí",None,None,ficha=("aut","vel","pleg"),nivel="Entrada",arg="Usa baterías de herramientas Einhell: repuesto barato y fácil.")
add(338519904,"Rider FT01 - 1000W",E,"Bici eléctrica tipo moto","Asistida + acelerador",{"diario","paseo"},"Todo terreno",70,45,"Revisar","No",49,120,ficha=("aut","vel","peso","carga"),nivel="Medio")
add(339104476,"HTK 1000M",E,"Bici eléctrica urbana","Asistida",{"diario"},"Asfalto",None,None,"No","No",23,None,ficha=("peso",),nivel="Entrada",notas="Ficha incompleta: completar autonomía y velocidad.")
add(347573632,"Ponyboy",E,"Bici eléctrica tipo moto","Asistida",{"diario","paseo"},"Asfalto y mixto",None,None,"Revisar","No",None,None,nivel="Medio",notas="Ficha incompleta: completar autonomía, velocidad, peso.")
add(349261766,"E-Krabi",E,"Bici eléctrica plegable","Asistida",{"diario","paseo"},"Asfalto",100,24,"No","Sí",21.4,120,160,200,ficha=("aut","vel","pleg","peso","carga","amin","amax"),nivel="Medio")
add(349261769,"E-GOA",E,"Bici eléctrica plegable","Asistida",{"diario"},"Asfalto",60,24,"No","Sí",19,100,150,190,ficha=("aut","vel","pleg","peso","carga","amin","amax"),nivel="Medio")
add(349261777,"E-Easy Fat",E,"Bici eléctrica plegable (rodado ancho)","Asistida",{"diario","paseo"},"Todo terreno",100,24,"No","Sí",21.8,120,160,200,ficha=("aut","vel","pleg","peso","carga","amin","amax"),nivel="Medio")
add(350943156,"Apine Trail E Shimano",E,"Bici eléctrica de montaña (eMTB)","Asistida",{"deporte"},"Mixto y tierra",70,25,"No","No",None,None,talles="Sí",ficha=("aut",),est=("vel",),nivel="Alto",notas="Autonomía 25–70 km. Nombre en TN con typo ('Apine').")
add(354117181,"Rift Zone E2 SHIMANO (2024)",E,"Bici eléctrica de montaña (eMTB)","Asistida",{"deporte"},"Mixto y tierra",None,25,"No","No",None,None,talles="Sí",est=("vel",),nivel="Alto",notas="Completar autonomía (batería 630 Wh).")
add(361026582,"City",E,"Bici eléctrica tipo moto","Asistida + acelerador",{"diario"},"Asfalto",65,45,"Revisar","No",None,120,ficha=("aut","vel","carga"),nivel="Medio")

# Bicis convencionales (estimado por tipo)
def conv(ids_nombres, cat, tipo, usos, terreno, pleg="No", talles="Sí", notas="", activo="Sí", amin=None, amax=None, silla=None):
    for pid, n in ids_nombres:
        est = ("usos","terreno") + (("pleg",) if pleg != "No" else ()) + (("amin","amax") if amin else ())
        add(pid, n, cat, tipo, "Pedal", usos, terreno, pleg=pleg, talles=talles, amin=amin, amax=amax, activo=activo, notas=notas, est=est, silla=silla)

conv([(260343619,"Quick 6"),(260345186,"Street 700c"),(260346843,"Space"),(260348567,"Alight 2 DD Disc"),(283613255,"Kentfield 1"),
      (284230738,"Fairfax 1 (2025)"),(284243181,"Presidio 1 (2025)"),(284246048,"Muirwoods (2022)"),(284254494,"Kentfield 2"),
      (297493608,"Stinson 1"),(297519717,"Stinson 2"),(349261735,"Roadkiller Lady Disk"),(349261744,"I Am Single"),
      (349261748,"Flower Bud Fairy"),(349261756,"VHS Player"),(349261782,"Lucky Clover"),(349261785,"Lucky Clover Low"),
      (349261838,"Roadkiller Disk"),(350940557,"Kentfield 3"),(350940977,"Kentfield 1 ST (caño bajo)"),
      (350941779,"Fairfax 1 ST (caño bajo)"),(353200327,"Fairfax 3 (2025)"),(354117226,"Stinson 2 ST")],
     "Bicicletas > Urbanas", "Bici urbana", {"diario","paseo"}, "Asfalto")
add(349261743,"The Tentacle","Bicicletas > Urbanas + De Carga","Bici de carga / urbana","Pedal",{"diario","paseo","carga","chicos"},"Asfalto",talles="No",est=("usos","terreno"))
conv([(260340429,"Link D8"),(260340659,"Node D8"),(260341040,"Link C8"),(260341051,"Node D7i"),(260341563,"Eclipse D16"),
      (260342182,"Link A7"),(349261704,"Hopper Mini"),(349261708,"Hopper XL"),(349261749,"Krabi V-brake"),(349261759,"Krabi Disk"),
      (349261760,"Lentus Mini"),(349261768,"GOA V-brake"),(349261771,"Easy Disk"),(349261778,"Easy Fat"),(349261789,"Easy 8"),
      (349261794,"Speed Disk"),(349261833,"Easy Fat Nexus"),(349261834,"Lentus 8")],
     "Bicicletas > Plegables", "Bici plegable", {"diario","paseo"}, "Asfalto", pleg="Sí", talles="No", amin=150, amax=190,
     notas="Completar peso (clave para escaleras) y rango de altura de ficha.")
conv([(283042882,"Nicasio (2023)"),(283050150,"Nicasio 1 (2025)"),(283064870,"Four Corners 1 Sword (2025)"),(297455735,"DSX 1 (2026)"),
      (297486793,"DSX 2 (2026)"),(345977305,"Nicasio+ Sword (650B)"),(349261732,"Wanderer"),(349261740,"The Lightning"),
      (350939845,"Nicasio 1 ST (caño bajo)"),(350940160,"Four Corners 1 Cues (2026)"),(359825099,"Esker 5.0")],
     "Bicicletas > Gravel", "Bici gravel", {"diario","paseo","deporte"}, "Asfalto y mixto")
add(351124297,"Gestalt (2026)","Bicicletas > Gravel + Ruta","Bici gravel/ruta","Pedal",{"deporte","diario"},"Asfalto y mixto",talles="Sí",est=("usos","terreno"))
add(349261840,"Mom's Favorite","Bicicletas > Gravel + MTB","Bici gravel/MTB","Pedal",{"deporte","paseo"},"Mixto y tierra",talles="Sí",est=("usos","terreno"),notas="Está en Gravel y MTB: revisar.")
conv([(260343873,"Trail 6 MTB 2021"),(260346819,"Cheetah"),(284282046,"Alpine Trail Carbon 1 (2024)"),(284296407,"El Roy (2023)"),
      (297298148,"Bolinas Ridge 1"),(297298255,"Bolinas Ridge 2"),(297318978,"Bobcat Trail 4 (2025)"),(297327810,"San Quentin 1"),
      (349261728,"Boys Don't Cry"),(349261738,"Lone Ranger"),(349261821,"Sunday"),(349261825,"Sunday Low"),(349261827,"Big Time"),
      (352623351,"Rift Zone 1 (2026)"),(353164248,"Rift Zone 2 29 (2025)"),(353164254,"Bobcat Trail 5 (2025)"),
      (354117068,"Pine Mountain 1 - 29"),(354117137,"Team Marin 1")],
     "Bicicletas > MTB", "Bici de montaña (MTB)", {"deporte"}, "Mixto y tierra", silla="No")
conv([(284309508,"Alcatraz 1"),(354117074,"Alcatraz 2"),(354117085,"Alcatraz 24\" (2026)")],
     "Bicicletas > Dirt/Stunt", "Bici dirt/stunt", {"deporte"}, "Todo terreno", silla="No")
# Infantiles
def rango(n):
    m = re.search(r'(\d+(?:[.,]5)?)\s*"?(?: Race)?$', n.replace("\"",""))
    r = {"12":(85,100),"14":(95,110),"16":(105,120),"20":(115,135),"24":(130,150),"26":(145,165),"27.5":(150,175),"27,5":(150,175)}
    return r.get(m.group(1)) if m else None
for pid, n in [(260342484,"Power"),(260342490,"Jumper"),(304920554,"Bayview Trail 24\""),(305009753,"Bayview Trail 20\""),
               (349261711,"Bubble 14 Race"),(349261714,"Bubble 16 Race"),(349261718,"Bubble 27,5 Race"),(349261720,"Chloe 27.5 Race"),
               (349261721,"Bubble 24 Race"),(349261724,"Bubble 26 Race"),(349261795,"Bubble 20 Race"),(349261799,"Chloe 20 Race"),
               (349261804,"Chloe 24 Race"),(349261809,"Chloe 26 Race"),(349261814,"Chloe 16 Race"),(350552513,"Ant"),
               (353164252,"Rift Zone 26"),(354117262,"San Quentin 24\"")]:
    rg = rango(n)
    add(pid, n, "Vehículos para niños", "Bici infantil/juvenil", "Pedal", {"ninos"}, "Asfalto y mixto", silla="No",
        amin=rg[0] if rg else None, amax=rg[1] if rg else None, est=("usos","amin","amax") if rg else ("usos",),
        notas="" if rg else "Completar rodado / rango de altura.")

KEY = {"aut":14,"vel":15,"lic":16,"pleg":17,"peso":18,"carga":19,"amin":20,"amax":21,"subida":23,"usos":(5,6,7,8,9,10),"silla":11,"aire":12,"terreno":13}
for r in rows:
    ws.append(r["v"])
    ri = ws.max_row
    for k in r["ficha"]:
        for ci in (KEY[k] if isinstance(KEY[k], tuple) else (KEY[k],)):
            ws.cell(ri, ci+1).fill = FICHA
    for k in r["est"]:
        for ci in (KEY[k] if isinstance(KEY[k], tuple) else (KEY[k],)):
            ws.cell(ri, ci+1).fill = EST
    ws.cell(ri, 28).alignment = WR; ws.cell(ri, 29).alignment = WR
ws.auto_filter.ref = f"A1:{ws.cell(1, len(cols)).column_letter}{ws.max_row}"
n = ws.max_row
def dv(formula, rng):
    d = DataValidation(type="list", formula1=formula, allow_blank=True); ws.add_data_validation(d); d.add(rng)
dv("=Listas!$A$2:$A$20", f"D2:D{n+200}")
dv("=Listas!$B$2:$B$6", f"E2:E{n+200}")
dv('"Sí,No"', f"F2:K{n+200}")
dv('"Sí,No,Revisar"', f"L2:L{n+200}")
dv('"Sí,No"', f"M2:M{n+200}")
dv("=Listas!$C$2:$C$6", f"N2:N{n+200}")
dv('"Sí,No,Revisar"', f"Q2:Q{n+200}")
dv('"Sí,No,Parcial"', f"R2:R{n+200}")
dv('"Sí,No"', f"W2:W{n+200}")
dv('"Entrada,Medio,Alto"', f"Y2:Y{n+200}")
dv('"0,1,2,3"', f"Z2:Z{n+200}")
dv('"Sí,No"', f"AA2:AA{n+200}")
ws.cell(1,1).comment = Comment("ID del producto en Tiendanube (número en la URL del admin). Es la llave con la que el asesor cruza stock y precio en vivo.", "Asesor")

# ------------------------------------------------------------ 5. Accesorios (combos)
ws = wb.create_sheet("Accesorios")
header(ws, ["Tipo de vehículo", "Rol en el combo", "Categoría TN (ID)", "Combo", "Condición", "Nivel a elegir", "Por qué (texto al cliente)"],
       [26, 20, 34, 12, 34, 16, 70])
acc = [
 ("Bicis (todas)", "Casco", "Cascos > Para ciclismo (30169776)", "Esencial", "Si no tiene casco", "Medio", "Lo primero: protege tu cabeza en cada salida."),
 ("Bicis (todas)", "Candado", "Candados y accesorios (30169763), sin 'Específicos de moto'", "Esencial", "Estaciona en la calle", "Alto, sin cables", "En la calle, un U-lock, cadena o plegable de alta seguridad es lo que frena un robo."),
 ("Bicis (todas)", "Candado", "Candados y accesorios (30169763)", "Esencial", "Guarda adentro", "Económico", "Para paradas cortas alcanza con uno liviano."),
 ("Bicis sin motor", "Luces", "Luces > Para bicicleta (30169757)", "Esencial", "Siempre (mejor nivel si anda de noche)", "Medio / Alto de noche", "Para ver y, sobre todo, que te vean."),
 ("Bicis eléctricas", "Luces", "Luces > Para bicicleta (30169757)", "Completo", "Solo si la ficha no trae luces", "Medio", "Luz extra para ser más visible."),
 ("Todo con 'Neumáticos con aire' = Sí (bicis, monopatines)", "Inflador", "Infladores > De mano (38434548) / De pie (38567338)", "Esencial", "Si no tiene inflador", "Medio", "Con la presión justa rueda mejor, gastás menos batería o piernas y evitás pinchaduras."),
 ("Moto eléctrica", "Inflador", "Infladores > Eléctricos (38938743)", "Completo", "Si no tiene inflador", "Medio", "Para controlar la presión sin ir a la gomería."),
 ("Bicis con 'Acepta silla de niños' = Sí", "Silla para niños", "Sillas para niños (30169830)", "Esencial", "Marcó 'Llevar a los chicos'", "Medio", "Para llevar a los chicos seguros desde el primer día."),
 ("Bicis (todas)", "Portapaquetes, canasto o alforjas", "Portapaquetes (30169786) / Bolsos y canastos (33821153) / Mochilas, alforjas y morrales (33844521)", "Esencial", "Marcó 'Llevar carga'", "Medio", "Para llevar las compras o el trabajo sin cargar la espalda."),
 ("Bicis urbanas / plegables", "Portapaquetes o canasto", "Portapaquetes (30169786) / Bolsos y canastos (33821153)", "Completo", "Uso diario o paseo (si no marcó carga)", "Medio", "Para llevar tus cosas sin cargar la espalda."),
 ("Bicis gravel / MTB / ruta", "Caramañola + soporte", "Caramañolas, botellas y soportes (30169771)", "Completo", "Uso deporte o distancia > 15 km", "Medio", "Hidratación a mano."),
 ("Bicis infantiles", "Casco", "Cascos > Para ciclismo (30169776), solo infantiles", "Esencial", "Siempre", "Medio", "Para que aprenda con el casco puesto desde el primer día."),
 ("Monopatín eléctrico", "Casco", "Cascos > Para ciclismo (30169776)", "Esencial", "Si no tiene casco", "Medio", "A más de 20 km/h, el casco no es opcional."),
 ("Monopatín eléctrico", "Candado", "Candados y accesorios (30169763): cable o plegable", "Esencial", "Estaciona en la calle", "Medio", "Para atarlo cuando bajás a hacer algo."),
 ("Monopatín eléctrico", "Porta celular", "Porta celulares > Para bicicleta (37360471)", "Completo", "Uso diario", "Medio", "Para usar el GPS sin sacar el celular del bolsillo."),
 ("Monopatín eléctrico", "Guantes", "Guantes > Para ciclismo (30169781)", "Completo", "Siempre", "Medio", "Más agarre y protección en las manos."),
 ("Moto eléctrica", "Casco de moto", "Cascos > Para moto (30170011)", "Esencial", "Siempre (obligatorio)", "Medio", "Obligatorio para circular."),
 ("Moto eléctrica", "Traba disco / candado de moto", "Candados > Específicos de moto (35512464)", "Esencial", "Estaciona en la calle", "Alto", "La traba de disco impide que se la lleven rodando."),
 ("Moto eléctrica", "Baúl", "Baúles (30170012)", "Completo", "Uso diario o carga", "Medio", "Para guardar casco, compras o la mochila."),
 ("Moto eléctrica", "Cubremanos", "Cubremanos (30170054)", "Completo", "Siempre", "Medio", "Manos secas y abrigadas en invierno."),
 ("Moto eléctrica", "Guantes de moto", "Guantes > Para moto (30247745)", "Completo", "Siempre", "Medio", "Protección y agarre."),
 ("Moto eléctrica", "Funda", "Fundas para moto (37279095)", "Completo", "Estaciona en la calle", "Medio", "Protege del sol y la lluvia."),
 ("Bici eléctrica tipo moto", "Casco", "Cascos > Para moto (30170011)", "Esencial", "Velocidad máx > 25 km/h", "Medio", "Anda rápido: te recomendamos casco de moto."),
]
for a in acc: ws.append(list(a))
for row in ws.iter_rows(min_row=2):
    for c in row: c.alignment = WR

# ------------------------------------------------------------ 6. Sugerencias (todo el catálogo)
ws = wb.create_sheet("Sugerencias")
header(ws, ["Categoría TN", "ID", "Para qué vehículos", "Dónde aparece", "Condición", "Gancho (texto al cliente)"],
       [40, 11, 30, 26, 30, 70])
B, EB, MO, MON, TODO = "Bicis", "Bicis eléctricas", "Motos eléctricas", "Monopatines", "Todos"
BB = "Bicis y bicis eléctricas"
sug = [
 # Completá tu equipo (útil y concreto)
 ("Herramientas y arreglos", 30169760, BB + ", monopatines", "Completá tu equipo", "Siempre", "Un multiherramienta chico te saca de apuros en la calle."),
 ("Aceites y grasas (herramientas)", 35488195, BB, "Completá tu equipo", "Siempre", "Una cadena lubricada dura el doble y no hace ruido."),
 ("Selladores y garrafas de CO2", 33843328, BB + ", monopatines tubeless", "Completá tu equipo", "Neumáticos con aire", "Para reparar una pinchadura en minutos, sin desarmar nada."),
 ("Cámaras", 33843545, B, "Completá tu equipo", "Neumáticos con aire", "Una cámara de repuesto en la mochila y nunca quedás a pie."),
 ("Bolsos bajo asiento", 39457222, BB, "Completá tu equipo", "Siempre", "Para llevar la cámara, el inflador y las llaves sin mochila."),
 ("Porta celulares > Para bicicleta", 37360471, BB + ", monopatines", "Completá tu equipo", "Uso diario", "El GPS a la vista, sin sacar el celular del bolsillo."),
 ("Porta celulares > Para moto", 35481325, MO, "Completá tu equipo", "Siempre", "El GPS a la vista mientras manejás."),
 ("Timbres y bocinas", 30169819, BB, "Completá tu equipo", "Uso diario o paseo", "Para avisar en la bicisenda sin gritar."),
 ("Espejos retrovisores", 30169817, BB + ", monopatines", "Completá tu equipo", "Uso diario", "Ver quién viene atrás sin darte vuelta."),
 ("Guardabarros", 33843639, B, "Completá tu equipo", "Uso diario", "Llegar seco los días de lluvia."),
 ("Pies de apoyo", 33843770, B, "Completá tu equipo", "Uso diario o paseo", "Para estacionarla parada en cualquier lado."),
 ("Soportes para bicicleta", 30169785, BB, "Completá tu equipo", "Poco espacio para guardar", "Colgala en la pared y ganá lugar en casa."),
 ("Portabicicletas para autos", 30169770, B, "Completá tu equipo", "Deporte / naturaleza o combina con auto", "Para llevarla a donde quieras salir a andar."),
 ("Computadoras", 30170013, B, "Completá tu equipo", "Uso deporte", "Velocidad, distancia y tiempo de cada salida."),
 ("Fundas para bicicleta", 30169769, BB, "Completá tu equipo", "Estaciona afuera o guarda en balcón", "La protege del sol y la lluvia."),
 ("Cargadores > De bicicleta eléctrica", 35484713, EB, "Completá tu equipo", "Siempre", "Un segundo cargador para dejar en la oficina."),
 ("Cargadores > De monopatín", 35484712, MON, "Completá tu equipo", "Siempre", "Un segundo cargador para dejar en la oficina."),
 ("Cargadores > De moto", 35484711, MO, "Completá tu equipo", "Siempre", "Un segundo cargador para cargar donde estés."),
 ("Baterías > De bicicleta eléctrica", 35485009, EB, "Completá tu equipo", "Distancia > 30 km", "Una batería extra duplica la autonomía."),
 ("Baterías > De moto", 35485007, MO, "Completá tu equipo", "Distancia > 30 km", "Una batería extra duplica la autonomía."),
 # También te puede interesar (vidriera)
 ("Productos de limpieza y lubricación", 30169774, BB + ", monopatines", "También te puede interesar", "Siempre", "Limpia y como nueva por muchos años."),
 ("Productos de limpieza y lubricación (moto)", 30582424, MO, "También te puede interesar", "Siempre", "Para que brille como el primer día."),
 ("Indumentaria > Indumentaria", 30240325, BB, "También te puede interesar", "Siempre", "Ropa pensada para andar cómodo."),
 ("Indumentaria > Pantalones", 30169998, BB, "También te puede interesar", "Uso diario o deporte", "Pantalones que no se enganchan en la cadena."),
 ("Indumentaria > Remeras", 30169840, B, "También te puede interesar", "Uso deporte", "Técnicas, respirables y de secado rápido."),
 ("Indumentaria > Zapatillas", 30169845, B, "También te puede interesar", "Uso deporte", "Más potencia en cada pedaleo."),
 ("Indumentaria > Lentes", 30169768, BB + ", monopatines", "También te puede interesar", "Siempre", "Protegen del sol, el viento y los bichos."),
 ("Guantes > Para ciclismo", 30169781, BB, "También te puede interesar", "Siempre", "Más agarre y menos cansancio en las manos."),
 ("Guantes > Para moto", 30247745, MO, "También te puede interesar", "Siempre", "Protección y agarre."),
 ("Navegadores e intercomunicadores", 30170014, MO, "También te puede interesar", "Siempre", "Hablá, escuchá música y seguí el GPS con el casco puesto."),
 ("Luces > Para casco de moto", 33488999, MO, "También te puede interesar", "Circula de noche", "Más visible de noche, a la altura de los ojos de los autos."),
 ("Cubre asientos para moto", 30170029, MO, "También te puede interesar", "Estaciona en la calle", "Asiento seco y fresco aunque quede al sol."),
 ("Fundas de asiento", 30169780, BB, "También te puede interesar", "Uso paseo o diario", "Más comodidad en el asiento."),
 ("Cubre puños de manubrio", 30170032, BB + ", monopatines", "También te puede interesar", "Siempre", "Manos abrigadas en invierno."),
 ("Mochilas, alforjas y morrales", 33844521, TODO, "También te puede interesar", "Siempre", "Para llevar todo cómodo."),
 ("Caramañolas, botellas y soportes", 30169771, BB, "También te puede interesar", "Siempre", "Hidratación a mano."),
 ("Stickers y logos", 30169800, TODO, "También te puede interesar", "Siempre", "Hacela tuya."),
 ("Adaptadores KLICKFix", 30169777, BB, "También te puede interesar", "Siempre", "Poné y sacá canastos y bolsos en un clic."),
 ("Entrenadores y rodillos", 30169782, B, "También te puede interesar", "Uso deporte", "Seguí entrenando los días de lluvia."),
 ("Sillas para niños > Accesorios y repuestos", 39455628, BB, "También te puede interesar", "Llevar a los chicos", "Accesorios para la silla."),
 ("Trailers", 30153548, B, "También te puede interesar", "Llevar a los chicos o carga", "Llevá a los chicos o la carga en un trailer."),
 ("Vehículos para niños", 30153643, TODO, "También te puede interesar", "Llevar a los chicos", "¿Y para los chicos? Que salgan a andar con vos."),
 ("Usados seleccionados", 30154204, TODO, "También te puede interesar", "Presupuesto justo", "Usados revisados, más accesibles."),
 # Mantenimiento / repuestos (se muestran como 'Para más adelante')
 ("Cubiertas", 33843542, B, "Para más adelante", "Siempre", "Cuando gastes las cubiertas, las tenemos."),
 ("Puños y cintas para manubrio", 33843559, B, "Para más adelante", "Siempre", "Puños ergonómicos: menos hormigueo en las manos."),
 ("Asientos y accesorios", 33843551, B, "Para más adelante", "Uso paseo o distancia > 15 km", "Un buen asiento cambia todo en las salidas largas."),
 ("Pedales y accesorios", 33843555, B, "Para más adelante", "Siempre", "Pedales con más agarre."),
 ("Repuestos para bicicleta eléctrica", 35505626, EB, "Para más adelante", "Siempre", "Repuestos originales cuando los necesites."),
 ("Repuestos para monopatines", 30753392, MON, "Para más adelante", "Siempre", "Repuestos originales cuando los necesites."),
 ("Repuestos para motos", 30753379, MO, "Para más adelante", "Siempre", "Repuestos originales cuando los necesites."),
 ("Resto de 'Componentes de bicicleta' (aros, cadenas, cambios, frenos, etc.)", 33843997, B, "Para más adelante", "Siempre", "Todo para mantenerla y mejorarla."),
]
for x in sug: ws.append(list(x))
for row in ws.iter_rows(min_row=2):
    for c in row: c.alignment = WR
ws.append([]); ws.append(["Regla de rotación: en cada visita se elige 1 producto con stock por categoría, alternando entre visitas (los menos mostrados primero), para que TODO el catálogo tenga exposición. 'Prioridad' en Productos puede fijar uno."])
ws.cell(ws.max_row, 1).font = Font(italic=True)


# ------------------------------------------------------------ 7b. Altura por modelo (una fila por bici y talle)
ws = wb.create_sheet("Altura por modelo")
header(ws, ["ID Tiendanube", "Producto", "Marca", "Talle (como está en TN)", "Altura desde (cm)", "Altura hasta (cm)",
            "Fuente", "Estado", "¿La descripción en TN ya lo tiene?"], [13, 30, 18, 18, 13, 13, 34, 22, 18])
MG = "Guía Marin 2022 (PDF)"
OK = PatternFill("solid", fgColor="E5F4EA"); FALTA = PatternFill("solid", fgColor="FCE4E4")
# Tablas de la guía Marin 2022 (cm aprox., leídas del PDF)
T = {
 "kentfield": {"S":(157,170),"M":(168,178),"L":(175,188),"XL":(185,196)},
 "fairfax":   {"XS":(150,157),"S":(155,168),"M":(165,178),"L":(175,188),"XL":(185,193)},
 "muirwoods": {"XS":(150,160),"S":(157,170),"M":(168,180),"L":(178,188),"XL":(185,196)},
 "stinson":   {"S":(152,165),"M":(163,175),"L":(173,185),"XL":(183,191)},
 "stinson st":{"S":(152,165),"M":(163,175),"L":(173,180)},
 "dsx":       {"S":(157,168),"M":(165,178),"L":(175,188),"XL":(185,196)},
 "nicasio":   {"47":(146,152),"50":(150,160),"52":(157,165),"54":(163,175),"56":(173,183),"58":(180,188),"60":(185,196)},
 "gestalt":   {"50":(152,160),"52":(157,165),"54":(163,175),"56":(173,183),"58":(180,188),"60":(185,196)},
 "four corners": {"XS":(150,160),"S":(157,170),"M":(168,180),"L":(178,188),"XL":(185,196)},
 "growing":   {"XS":(146,157),"S":(157,165),"M":(163,178),"L":(175,185),"XL":(183,193)},
 "hardtail29":{"S":(160,168),"M":(165,178),"L":(175,185),"XL":(183,193)},
 "pine":      {"S":(160,168),"M":(165,178),"L":(175,185),"XL":(183,193)},
 "rift29":    {"S":(160,170),"M":(168,180),"L":(178,188),"XL":(185,196)},
 "rift275":   {"XS":(152,163),"S":(160,170)},
 "alcatraz":  {"SHORT":(150,175),"LONG":(170,196)},
}
def letra(t):
    t = str(t).upper().replace("CM","").strip()
    m = re.match(r"^(SHORT|LONG|XXS|XS|XXL|XL|S|M|L|\d{2})", t.replace(" ",""))
    return m.group(1) if m else t
def fila(pid, prod, marca, talle, rango=None, fuente="", estado=None, desc="A verificar"):
    lo, hi = rango if rango else (None, None)
    if estado is None:
        estado = "Completo" if rango else "COMPLETAR"
    ws.append([pid, prod, marca, talle, lo, hi, fuente if rango else "", estado, desc])
    fill = OK if estado == "Completo" else (EST if rango else FALTA)
    for c in (5, 6, 8): ws.cell(ws.max_row, c).fill = fill
def marin(pid, prod, talles, tabla, estado="Completo", extra=None):
    for t in talles:
        r = T[tabla].get(letra(t)) or (T[extra].get(letra(t)) if extra else None)
        fila(pid, prod, "Marin", t, r, MG, estado if r else "COMPLETAR (no está en la guía)")
def libre(pid, prod, marca, talles, desc="A verificar"):
    for t in talles: fila(pid, prod, marca, t, desc=desc)
SMLXL = ["S","M","L","XL"]; XS_XL = ["XS","S","M","L","XL"]
# --- Marin urbanas
marin(283613255,"Kentfield 1",SMLXL,"kentfield")
marin(284254494,"Kentfield 2",SMLXL,"kentfield")
marin(350940557,"Kentfield 3",SMLXL,"kentfield")
marin(350940977,"Kentfield 1 ST (caño bajo)",["S","M","L"],"kentfield","Estimado: guía de Kentfield (sin ST)")
marin(284230738,"Fairfax 1 (2025)",SMLXL,"fairfax")
marin(350941779,"Fairfax 1 ST (caño bajo)",["S","M","L"],"fairfax","Estimado: guía de Fairfax (sin ST)")
marin(353200327,"Fairfax 3 (2025)",XS_XL,"fairfax")
marin(284243181,"Presidio 1 (2025)",SMLXL,"fairfax")
marin(284246048,"Muirwoods (2022)",SMLXL,"muirwoods")
marin(297493608,"Stinson 1",SMLXL,"stinson")
marin(297519717,"Stinson 2",SMLXL,"stinson")
marin(354117226,"Stinson 2 ST",XS_XL,"stinson st")
# --- Marin gravel
marin(283042882,"Nicasio (2023)",["50cm","52cm","54cm","56cm","58cm","60cm"],"nicasio")
marin(283050150,"Nicasio 1 (2025)",["52","54","56","58","60"],"nicasio")
marin(345977305,"Nicasio+ Sword (650B)",["52","54","56"],"nicasio","Estimado: guía de Nicasio")
marin(350939845,"Nicasio 1 ST (caño bajo)",["47","50","52","54","56"],"nicasio","Estimado: guía de Nicasio (sin ST)")
marin(283064870,"Four Corners 1 Sword (2025)",SMLXL,"four corners")
marin(350940160,"Four Corners 1 Cues (2026)",["XS (ROD27,5)","S (ROD27,5)","M (ROD700)","L (ROD700)","XL (ROD700)"],"four corners")
marin(297455735,"DSX 1 (2026)",SMLXL,"dsx")
marin(297486793,"DSX 2 (2026)",SMLXL,"dsx")
marin(351124297,"Gestalt (2026)",["50","52","54","56","58","60"],"gestalt")
# --- Marin MTB
marin(297298148,"Bolinas Ridge 1",["M (ROD29)","L (ROD29)","XL (ROD29)"],"growing","Estimado: guía 'Growing Wheel Size'")
marin(297298255,"Bolinas Ridge 2",["XS (ROD27,5)","S (ROD27,5)","M (ROD29)","L (ROD29)","XL (ROD29)"],"growing","Estimado: guía 'Growing Wheel Size'")
marin(297318978,"Bobcat Trail 4 (2025)",["M (ROD29)","L (ROD29)","XL (ROD29)"],"growing","Estimado: guía 'Growing Wheel Size'")
marin(353164254,"Bobcat Trail 5 (2025)",XS_XL,"growing","Estimado: guía 'Growing Wheel Size'")
marin(297327810,"San Quentin 1",["S (ROD27,5)","M (ROD29)","L (ROD29)","XL (ROD29)"],"growing","Estimado: guía 'Growing Wheel Size'")
marin(354117068,"Pine Mountain 1 - 29",XS_XL,"pine")
marin(354117137,"Team Marin 1",XS_XL,"hardtail29","Estimado: guía 'Hardtail 29'")
marin(352623351,"Rift Zone 1 (2026)",['XS(27,5")','S(27,5")','M(29")','L(29")','XL(29")'],"rift29",extra="rift275")
marin(353164248,"Rift Zone 2 29 (2025)",XS_XL,"rift29")
marin(284282046,"Alpine Trail Carbon 1 (2024)",XS_XL,"rift29","Completo")
marin(350943156,"Apine Trail E Shimano",SMLXL,"rift29")
marin(354117181,"Rift Zone E2 SHIMANO (2024)",XS_XL,"rift29")
fila(284296407,"El Roy (2023)","Marin","XS / S / M / L / XL",estado="COMPLETAR: la guía usa Regular/Grande")
marin(284309508,"Alcatraz 1",["Short","Long"],"alcatraz")
fila(354117074,"Alcatraz 2","Marin","XS / S / M / L / XL",estado="COMPLETAR: la guía usa Short/Long")
fila(354117085,'Alcatraz 24" (2026)',"Marin","Único",estado="COMPLETAR")
# --- Otras marcas con talles
libre(260343619,"Quick 6","Cannondale (a confirmar)",["S","M","L"])
libre(260343873,"Trail 6 MTB 2021","Cannondale (a confirmar)",["XS","S","M","L","XL"])
libre(260348567,"Alight 2 DD Disc","Liv (a confirmar)",["S","M","L"])
libre(260346843,"Space","",["S","M","L"])
libre(260346819,"Cheetah","",SMLXL)
for pid, n, ts in [(349261735,"Roadkiller Lady Disk",["S","M"]),(349261838,"Roadkiller Disk",["M","L"]),(349261744,"I Am Single",["S","M","L"]),
                   (349261748,"Flower Bud Fairy",["S"]),(349261756,"VHS Player",["S","M","L"]),(349261782,"Lucky Clover",["S","M","L"]),
                   (349261785,"Lucky Clover Low",["S","M"]),(349261732,"Wanderer",["S","M","L"]),(349261740,"The Lightning",["S","M","L"]),
                   (349261840,"Mom's Favorite",["M","XL"]),(349261728,"Boys Don't Cry",["S","M","L"]),(349261738,"Lone Ranger",["S","M","L"]),
                   (349261821,"Sunday",["S","M","L"]),(349261825,"Sunday Low",["S","M"]),(349261827,"Big Time",["S","M","L"]),
                   (359825099,"Esker 5.0",["M","L"])]:
    libre(pid, n, "", ts)
# --- Talle único
FT = "Ficha del producto en TN"
for pid, n, marca, r in [(260342361,"2Fold 20","",(140,185)),(260344367,"2Fold 20 Fat","",(140,185)),(260343629,"HSD P9","Tern",(150,195)),
                         (263963220,"Quick Haul P9","Tern",(160,195)),(349261766,"E-Krabi","",(160,200)),(349261769,"E-GOA","",(150,190)),
                         (349261777,"E-Easy Fat","",(160,200))]:
    fila(pid, n, marca, "Único", r, FT, desc="Sí")
for pid, n in [(260345186,"Street 700c"),(349261743,"The Tentacle"),(260340429,"Link D8"),(260340659,"Node D8"),(260341040,"Link C8"),
               (260341051,"Node D7i"),(260341563,"Eclipse D16"),(260342182,"Link A7"),(349261704,"Hopper Mini"),(349261708,"Hopper XL"),
               (349261749,"Krabi V-brake"),(349261759,"Krabi Disk"),(349261760,"Lentus Mini"),(349261768,"GOA V-brake"),(349261771,"Easy Disk"),
               (349261778,"Easy Fat"),(349261789,"Easy 8"),(349261794,"Speed Disk"),(349261833,"Easy Fat Nexus"),(349261834,"Lentus 8"),
               (321841921,"Vecocraft Foldy-E Einhell"),(260347235,"Brina2 X1000"),(264034402,"Nbd S5I"),(273218901,"Slim"),
               (273255228,"X350"),(273267951,"Enduro Pro X"),(291838862,"Rider FT01 - 750W"),(338519904,"Rider FT01 - 1000W"),
               (314027238,"Segway Xyber"),(314180547,"Chopper FT02"),(339104476,"HTK 1000M"),(347573632,"Ponyboy"),(361026582,"City")]:
    fila(pid, n, "", "Único")
# --- Infantiles (talle único, por rodado)
fila(304920554,'Bayview Trail 24"',"Marin","Único",(122,142),MG,"Completo")
fila(354117262,'San Quentin 24"',"Marin","Único",(122,142),MG,"Completo")
fila(353164252,"Rift Zone 26","Marin","Único",(137,157),MG,"Completo")
fila(305009753,'Bayview Trail 20"',"Marin","Único")
for pid, n in [(260342484,"Power"),(260342490,"Jumper"),(349261711,"Bubble 14 Race"),(349261714,"Bubble 16 Race"),(349261795,"Bubble 20 Race"),
               (349261721,"Bubble 24 Race"),(349261724,"Bubble 26 Race"),(349261718,"Bubble 27,5 Race"),(349261814,"Chloe 16 Race"),
               (349261799,"Chloe 20 Race"),(349261804,"Chloe 24 Race"),(349261809,"Chloe 26 Race"),(349261720,"Chloe 27.5 Race"),(350552513,"Ant")]:
    fila(pid, n, "", "Único")
ws.auto_filter.ref = f"A1:I{ws.max_row}"
dv2 = DataValidation(type="list", formula1='"Sí,No,A verificar"', allow_blank=True); ws.add_data_validation(dv2); dv2.add(f"I2:I{ws.max_row+100}")
ws.append([]); ws.append(["Verde = ya cargado. Amarillo = estimado con la guía de un modelo parecido (validar). Rojo = COMPLETAR. Una vez completo, este rango se usa para recomendar el talle y se puede agregar al final de la descripción de cada producto en Tiendanube."])
ws.cell(ws.max_row, 1).font = Font(italic=True)

# ------------------------------------------------------------ 8. Talles genéricos
ws = wb.create_sheet("Talles genéricos")
header(ws, ["Talle de cuadro", "Altura desde (cm)", "Altura hasta (cm)", "Equivalente MTB (pulgadas)", "Equivalente ruta/gravel (cm)"], [16, 16, 16, 24, 26])
for t in [("XXS",135,152,"—","—"),("XS",148,163,"13–14\"","< 50"),("S",158,172,"15–16\"","50–52"),("M",168,181,"17–18\"","53–55"),
          ("L",177,190,"19–20\"","56–58"),("XL",186,198,"21\"+","59+"),("XXL",194,210,"—","—")]: ws.append(list(t))
ws.append([]); ws.append(["Rodado (infantiles)", "Altura desde (cm)", "Altura hasta (cm)", "Edad aprox.", ""])
for c in ws[ws.max_row]: c.font = Font(bold=True)
for t in [('12"',85,100,"2–4 años"),('14"',95,110,"3–5 años"),('16"',105,120,"4–6 años"),('20"',115,135,"6–9 años"),
          ('24"',130,150,"8–12 años"),('26"',145,165,"11+ años"),('27,5"',150,175,"juvenil")]: ws.append(list(t))
ws.append([]); ws.append(["Solo se usa si el modelo no está en 'Altura por modelo'. El asesor lo muestra como talle orientativo."])

# ------------------------------------------------------------ 9. Listas
ws = wb.create_sheet("Listas")
header(ws, ["Tipo de vehículo", "Propulsión", "Terreno apto"], [36, 24, 20])
tipos = ["Bici urbana","Bici plegable","Bici gravel","Bici gravel/ruta","Bici gravel/MTB","Bici de ruta","Bici de montaña (MTB)","Bici dirt/stunt",
         "Bici de carga","Bici infantil/juvenil","Bici eléctrica urbana","Bici eléctrica plegable","Bici eléctrica plegable (rodado ancho)",
         "Bici eléctrica urbana/MTB","Bici eléctrica tipo moto","Bici eléctrica de carga","Bici eléctrica de montaña (eMTB)",
         "Monopatín eléctrico","Moto eléctrica","Moto eléctrica (scooter)"]
prop = ["Pedal","Asistida","Asistida + acelerador","Eléctrica"]
ter = ["Asfalto","Asfalto y mixto","Mixto y tierra","Todo terreno"]
for i in range(len(tipos)):
    ws.append([tipos[i], prop[i] if i < len(prop) else None, ter[i] if i < len(ter) else None])

wb.save(__import__("sys").argv[1])
print(len(rows), "productos")
