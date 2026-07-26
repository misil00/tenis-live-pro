from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timezone
import requests
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
        "version": "TEST-OK-1",
        "status": "ok"
    })
​
@app.route("/api/fixture")
def fixture():
    date_value = request.args.get("date", "2026-07-26")
    date_digits = date_value.replace("-", "")
    results = []
    errors = []
​
    for league in ["atp", "wta"]:
        url = "https://site.api.espn.com/apis/site/v2/sports/tennis/" + league + "/scoreboard?dates=" + date_digits + "&limit=500"
        try:
            response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            data = response.json()
            for event in data.get("events", []):
                results.append({
                    "eventId": str(event.get("id", "")),
                    "league": league,
                    "name": event.get("name") or event.get("shortName") or league.upper(),
                    "date": event.get("date"),
                    "source": "ESPN"
                })
        except Exception as exc:
            errors.append({"league": league, "error": str(exc)})
​
    return jsonify({
        "date": date_value,
        "source": "ESPN",
        "updated": datetime.now(timezone.utc).isoformat(),
        "items": results,
        "errors": errors
    })
​
if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
​
