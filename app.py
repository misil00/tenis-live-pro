from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timezone
import os
​
app = Flask(__name__)
CORS(app)
​
@app.route("/")
def home():
    return "Tenis Live Pro API OK"
​
@app.route("/api/version")
def version():
    return jsonify({
        "app": "Tenis Live Pro",
        "version": "RENDER-TEST-OK-2",
        "status": "ok"
    })
​
@app.route("/api/fixture")
def fixture():
    date_value = request.args.get("date", "2026-07-26")
    return jsonify({
        "date": date_value,
        "source": "ESPN",
        "updated": datetime.now(timezone.utc).isoformat(),
        "matches": [],
        "tournaments": [],
        "errors": [],
        "message": "Render funciona. Luego activamos ESPN completo."
    })
​
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
