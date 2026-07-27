from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
import os
import re
import requests
​
app = Flask(__name__)
CORS(app)
​
SITE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
LEAGUES = ["atp", "wta"]
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
​
STATUS_ES = {
    "STATUS_SCHEDULED": "Programado",
    "STATUS_IN_PROGRESS": "En vivo",
    "STATUS_FINAL": "Final",
    "STATUS_RETIREMENT": "Retiro",
    "STATUS_WALKOVER": "Walkover",
    "STATUS_POSTPONED": "Atrasado",
    "STATUS_CANCELED": "Cancelado",
}
​
​
def now_iso():
    return datetime.now(timezone.utc).isoformat()
​
​
def slug(text):
    text = (text or "torneo").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "torneo"
​
​
def ymd_compact(date_text):
    return date_text.replace("-", "")
​
​
def parse_day(value):
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00")).date()
    except Exception:
        return None
​
​
def requested_day(date_text):
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    except Exception:
        return datetime.now(timezone.utc).date()
​
​
def channels(tournament, league):
    out = [{"name": "ESPN", "url": "https://www.espn.com/tennis/scoreboard"}]
    if league == "atp":
        out.append({"name": "Tennis TV ATP", "url": "https://www.tennistv.com/"})
    if league == "wta":
        out.append({"name": "WTA Donde ver", "url": "https://www.wtatennis.com/where-to-watch-tennis"})
    out.append({"name": "Tennis Channel", "url": "https://www.tennischannel.com/"})
    return out
​
​
def player_name(c):
    return ((c.get("athlete") or {}).get("displayName") or c.get("displayName") or (c.get("team") or {}).get("displayName") or "")
​
​
def player_score(c):
    vals = []
    for item in c.get("linescores") or []:
        v = item.get("displayValue") or item.get("value")
        if v is not None and str(v) != "":
            vals.append(str(v))
    return " ".join(vals) if vals else str(c.get("score") or "")
​
​
def tournament_name(event, comp, league):
    notes = comp.get("notes") or []
    if notes:
        return notes[0].get("headline") or notes[0].get("type") or event.get("name") or league.upper()
    return event.get("name") or event.get("shortName") or league.upper()
​
​
def event_to_match(event, league):
    comp = (event.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None
​
    players = []
    for c in competitors[:2]:
        name = player_name(c)
        if not name:
            return None
        players.append({
            "id": (c.get("athlete") or {}).get("id") or c.get("id"),
            "name": name,
            "seed": c.get("seed") or (c.get("curatedRank") or {}).get("current"),
            "score": player_score(c),
            "winner": bool(c.get("winner")),
        })
​
    status_type = comp.get("status", {}).get("type", {})
    status = status_type.get("name") or "STATUS_SCHEDULED"
    name = tournament_name(event, comp, league)
    venue = comp.get("venue") or {}
    return {
        "eventId": str(event.get("id") or ""),
        "league": league,
        "tournamentId": slug(name),
        "tournament": name,
        "round": status_type.get("shortDetail") or event.get("shortName") or "Ronda por confirmar",
        "court": venue.get("fullName") or venue.get("shortName") or "Cancha por confirmar",
        "status": status,
        "statusText": STATUS_ES.get(status, status_type.get("description") or "Programado"),
        "startTime": event.get("date") or comp.get("date"),
        "players": players,
        "winnerName": next((p["name"] for p in players if p.get("winner")), None),
        "channels": channels(name, league),
        "stats": {},
        "source": "ESPN",
        "lastUpdated": now_iso(),
    }
​
​
def event_to_tournament(event, league, date_text):
    comp = (event.get("competitions") or [{}])[0]
    name = tournament_name(event, comp, league)
    event_day = parse_day(event.get("date") or comp.get("date"))
    req_day = requested_day(date_text)
    active = True
    if event_day:
        # ESPN tennis sometimes returns tournament cards, not matches.
        # Keep only tournaments whose card date is close to requested day.
        active = event_day <= req_day <= event_day + timedelta(days=7)
    espn_url = "https://www.espn.com/tennis/scoreboard/tournament/_/eventId/" + str(event.get("id") or "") + "/competitionType/" + ("1" if league == "atp" else "2")
    return {
        "eventId": str(event.get("id") or ""),
        "league": league,
        "tournamentId": slug(name),
        "tournament": name,
        "round": "Campeonato",
        "court": "Informacion del campeonato",
        "status": "STATUS_SCHEDULED" if active else "STATUS_FINAL",
        "statusText": "Vigente" if active else "No vigente",
        "startTime": event.get("date") or comp.get("date"),
        "players": [],
        "winnerName": None,
        "channels": channels(name, league),
        "stats": {},
        "source": "ESPN",
        "lastUpdated": now_iso(),
        "isTournamentOnly": True,
        "isActiveTournament": active,
        "espnTournamentUrl": espn_url,
    }
​
​
def load_fixture(date_text):
    matches = []
    tournaments = []
    errors = []
    for league in LEAGUES:
        url = SITE + "/" + league + "/scoreboard?dates=" + ymd_compact(date_text) + "&limit=500"
        try:
            data = requests.get(url, headers=HEADERS, timeout=20).json()
            for event in data.get("events", []):
                match = event_to_match(event, league)
                if match:
                    matches.append(match)
                else:
                    card = event_to_tournament(event, league, date_text)
                    if card.get("isActiveTournament"):
                        tournaments.append(card)
        except Exception as exc:
            errors.append({"league": league, "error": str(exc)})
    return matches, tournaments, errors
​
​
@app.route("/")
def home():
    return "Tenis Live Pro API - ESPN"
​
​
@app.route("/api/version")
def version():
    return jsonify({"app": "Tenis Live Pro", "version": "ESPN-FINAL-2", "status": "ok", "source": "ESPN"})
​
​
@app.route("/api/fixture")
def fixture():
    date_text = request.args.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    matches, tournaments, errors = load_fixture(date_text)
    return jsonify({"date": date_text, "source": "ESPN", "updated": now_iso(), "matches": matches, "tournaments": tournaments, "errors": errors})
​
​
@app.route("/api/live")
def live():
    date_text = request.args.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    matches, tournaments, errors = load_fixture(date_text)
    return jsonify({"date": date_text, "source": "ESPN", "matches": [m for m in matches if "IN_PROGRESS" in (m.get("status") or "")], "errors": errors, "updated": now_iso()})
​
​
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)
