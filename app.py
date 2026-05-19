from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import os

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

TOKEN   = os.environ.get("ML_TOKEN", "")
USER_ID = os.environ.get("ML_USER_ID", "246901020")
BASE    = "https://api.mercadolibre.com"

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

@app.route("/")
def index():
    return app.send_static_file("index.html")

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
        details = ml_get("/items?ids=" + ",".join(ids), token)
        items = [x["body"] for x in details if x.get("code") == 200]
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
