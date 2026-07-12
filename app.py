from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import os
import json
import io

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

from sync import sync_bp
app.register_blueprint(sync_bp)

TOKEN   = os.environ.get("ML_TOKEN", "")
USER_ID = os.environ.get("ML_USER_ID", "246901020")
BASE    = "https://api.mercadolibre.com"

# Cargar costos desde costos.json
COSTOS = {}
costos_path = os.path.join(os.path.dirname(__file__), "costos.json")
if os.path.exists(costos_path):
    with open(costos_path) as f:
        COSTOS = json.load(f)

def ml_get(path, token=None):
    t = token or TOKEN
    r = requests.get(BASE + path, headers={"Authorization": f"Bearer {t}"}, timeout=15)
    r.raise_for_status()
    return r.json()

def ml_put(path, body, token=None):
    t = token or TOKEN
    r = requests.put(BASE + path, headers={"Authorization": f"Bearer {t}"}, json=body, timeout=15)
    r.raise_for_status()
    return r.json()

def get_sku_raiz(item):
    sku = item.get("seller_custom_field")
    if not sku:
        for a in item.get("attributes", []):
            if a.get("id") == "SELLER_SKU":
                sku = a.get("value_name")
                break
    if sku:
        return str(sku).strip()[:6]
    return None

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/sync")
def sync_page():
    return app.send_static_file("sync.html")

@app.route("/api/items")
def get_items():
    token   = request.args.get("token", TOKEN)
    user_id = request.args.get("user_id", USER_ID)
    offset  = int(request.args.get("offset", 0))
    limit   = int(request.args.get("limit", 50))
    try:
        data = ml_get(f"/users/{user_id}/items/search?status=active&limit={limit}&offset={offset}", token)
        ids  = data.get("results", [])
        total = data["paging"]["total"]
        if not ids:
            return jsonify({"items": [], "total": total})
        items = []
        for i in range(0, len(ids), 20):
            chunk = ids[i:i+20]
            details = ml_get("/items?ids=" + ",".join(chunk), token)
            items += [x["body"] for x in details if x.get("code") == 200]
        for item in items:
            sku = get_sku_raiz(item)
            item["_sku_raiz"] = sku
            item["_costo"] = COSTOS.get(sku) if sku else None
        return jsonify({"items": items, "total": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/competitors")
def get_competitors():
    token      = request.args.get("token", TOKEN)
    catalog_id = request.args.get("catalog_id")
    user_id    = request.args.get("user_id", USER_ID)
    if not catalog_id:
        return jsonify({"competitors": []})
    try:
        data  = ml_get(f"/products/{catalog_id}/items?limit=10", token)
        comps = [
            x for x in data.get("results", [])
            if str(x["seller_id"]) != str(user_id) and x.get("price", 0) > 0
        ]
        return jsonify({"competitors": comps})
    except Exception as e:
        return jsonify({"competitors": [], "error": str(e)})

@app.route("/api/fees")
def get_fees():
    token   = request.args.get("token", TOKEN)
    item_id = request.args.get("item_id")
    price   = request.args.get("price")
    if not item_id:
        return jsonify({"error": "Falta item_id"}), 400
    try:
        path = f"/items/{item_id}/fees"
        if price:
            path += f"?price={price}"
        data = ml_get(path, token)
        # Extraer comisión de venta
        sale_fee_pct = None
        sale_fee_amt = None
        for component in data.get("sale_fee_components", []):
            if component.get("type") == "sale_fee":
                sale_fee_pct = component.get("fee")
                sale_fee_amt = component.get("amount")
        return jsonify({
            "sale_fee_pct": sale_fee_pct,
            "sale_fee_amt": sale_fee_amt,
            "raw": data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/update-price", methods=["POST"])
def update_price():
    token   = request.json.get("token", TOKEN)
    item_id = request.json.get("item_id")
    price   = request.json.get("price")
    if not item_id or not price:
        return jsonify({"error": "Faltan parámetros"}), 400
    try:
        ml_put(f"/items/{item_id}", {"price": price}, token)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/upload-costos", methods=["POST"])
def upload_costos():
    global COSTOS
    if "file" not in request.files:
        return jsonify({"error": "No se encontró el archivo"}), 400
    f = request.files["file"]
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        nuevos = {}
        for r in rows[1:]:
            sku = r[0]
            costo = r[4]
            if sku and costo:
                try:
                    sku_raiz = str(sku).strip()[:6]
                    costo_val = float(costo)
                    if costo_val > 0:
                        nuevos[sku_raiz] = costo_val
                except:
                    pass
        COSTOS = nuevos
        # Guardar en disco para persistir
        with open(costos_path, "w") as cf:
            json.dump(COSTOS, cf)
        return jsonify({"ok": True, "skus_cargados": len(COSTOS)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/costos-stats")
def costos_stats():
    return jsonify({"total_skus": len(COSTOS), "sample": list(COSTOS.items())[:3]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
