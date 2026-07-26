from flask import Flask, jsonify, request
from flask_cors import CORS
import requests, re
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
ESPN_WEB = "https://www.espn.com/tennis/scoreboard"
HEADERS = {"User-Agent":"Mozilla/5.0","Accept":"application/json"}
LEAGUES = ["atp", "wta"]

STATUS_ES = {
    "STATUS_SCHEDULED":"Programado",
    "STATUS_IN_PROGRESS":"En vivo",
    "STATUS_FINAL":"Final",
    "STATUS_POSTPONED":"Atrasado",
    "STATUS_DELAYED":"Atrasado",
    "STATUS_CANCELED":"Cancelado",
    "STATUS_RETIREMENT":"Retiro",
    "STATUS_WALKOVER":"Walkover",
}

def slugify(text):
    text = (text or "torneo").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "torneo"

def today_ymd():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def espn_date(date_str):
    return date_str.replace("-", "")

def get_json(url, timeout=15):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()

def fetch_scoreboard(league, date_str):
    url = f"{ESPN_BASE}/{league}/scoreboard?dates={espn_date(date_str)}&limit=500"
    return get_json(url)

def fetch_summary(league, event_id):
    url = f"{ESPN_BASE}/{league}/summary?event={event_id}"
    return get_json(url)

def athlete_name(c):
    return (c.get("athlete") or {}).get("displayName") or c.get("displayName") or (c.get("team") or {}).get("displayName") or "Jugador por confirmar"

def player_score(c):
    lines = c.get("linescores") or []
    vals = []
    for x in lines:
        v = x.get("displayValue") or x.get("value")
        if v is not None and str(v) != "": vals.append(str(v))
    if vals: return " ".join(vals)
    return str(c.get("score") or "")

def channels_for(tournament, league):
    name = (tournament or "").lower()
    links = []
    if any(x in name for x in ["wimbledon", "us open", "australian", "roland", "french open"]):
        links.append({"name":"Watch ESPN Tennis", "url":"https://www.espn.com/watch/catalog/aa07e63d-cf6e-3705-9c8d-8d5208e6af14/tennis"})
        links.append({"name":"ESPN App", "url":"https://www.espn.com/watch/"})
    if league == "atp": links.append({"name":"Tennis TV ATP", "url":"https://www.tennistv.com/"})
    if league == "wta": links.append({"name":"WTA Dónde ver", "url":"https://www.wtatennis.com/where-to-watch-tennis"})
    links.append({"name":"Tennis Channel", "url":"https://www.tennischannel.com/"})
    return links

def guess_tournament(ev, comp, league):
    candidates = [
        ev.get("season",{}).get("displayName"),
        ev.get("group",{}).get("name"),
        comp.get("conferenceCompetition",{}).get("displayName"),
        comp.get("competition",{}).get("displayName"),
        ev.get("league",{}).get("name"),
    ]
    for x in candidates:
        if x and str(x).strip(): return str(x).strip()
    # Si ESPN no separa tournament, no inventamos un nombre específico.
    return "ATP" if league == "atp" else "WTA"

def normalize_event(ev, league):
    comp = (ev.get("competitions") or [{}])[0]
    stype = comp.get("status",{}).get("type",{})
    status = stype.get("name") or ev.get("status",{}).get("type",{}).get("name") or "STATUS_SCHEDULED"
    status_text = STATUS_ES.get(status, stype.get("description") or stype.get("shortDetail") or "Programado")
    competitors = comp.get("competitors") or []
    players = []
    for c in competitors[:2]:
        players.append({
            "id": (c.get("athlete") or {}).get("id") or c.get("id"),
            "name": athlete_name(c),
            "seed": c.get("seed") or (c.get("curatedRank") or {}).get("current"),
            "score": player_score(c),
            "winner": bool(c.get("winner")),
        })
    while len(players) < 2:
        players.append({"name":"Jugador por confirmar", "score":"", "winner":False})
    winner = next((p["name"] for p in players if p.get("winner")), None)
    tournament = guess_tournament(ev, comp, league)
    venue = comp.get("venue") or {}
    notes = comp.get("notes") or []
    round_name = ""
    if notes:
        round_name = notes[0].get("headline") or notes[0].get("type") or ""
    if not round_name:
        round_name = stype.get("shortDetail") or ev.get("shortName") or "Ronda por confirmar"
    tid = slugify(tournament)
    return {
        "eventId": str(ev.get("id") or ""),
        "league": league,
        "tournamentId": tid,
        "tournament": tournament,
        "round": round_name,
        "court": venue.get("fullName") or venue.get("shortName") or "Cancha por confirmar",
        "status": status,
        "statusText": status_text,
        "startTime": ev.get("date") or comp.get("date"),
        "players": players,
        "winnerName": winner,
        "channels": channels_for(tournament, league),
        "stats": {},
        "source": "ESPN",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
    }

def parse_summary_stats(summary):
    stats = {}
    box = summary.get("boxscore") or {}
    teams = box.get("teams") or []
    if len(teams) >= 2:
        def sm(t):
            return {s.get("name"): s.get("displayValue") for s in t.get("statistics", [])}
        a, b = sm(teams[0]), sm(teams[1])
        for key, label in [("aces","aces"),("doubleFaults","doblesFaltas"),("firstServePct","primerServicio"),("breakPointsConverted","breakPoints")]:
            av, bv = a.get(key), b.get(key)
            if av or bv: stats[label] = f"{av or '—'} - {bv or '—'}"
    return stats

def load_matches(date_str):
    matches, errors = [], []
    for league in LEAGUES:
        try:
            data = fetch_scoreboard(league, date_str)
            for ev in data.get("events", []):
                matches.append(normalize_event(ev, league))
        except Exception as e:
            errors.append({"league": league, "error": str(e)})
    matches.sort(key=lambda m: m.get("startTime") or "")
    return matches, errors

@app.route("/")
def index():
    return "Tenis Live Pro API - ESPN fixture real por campeonato"

@app.route("/api/fixture")
def fixture():
    date_str = request.args.get("date") or today_ymd()
    matches, errors = load_matches(date_str)
    return jsonify({"date": date_str,"source": "ESPN","updated": datetime.now(timezone.utc).isoformat(),"matches": matches,"errors": errors})

@app.route("/api/tournaments/today")
def tournaments_today():
    date_str = request.args.get("date") or today_ymd()
    matches, errors = load_matches(date_str)
    data = {}
    for m in matches:
        tid = m.get("tournamentId") or slugify(m.get("tournament"))
        if tid not in data:
            data[tid] = {"id": tid, "name": m.get("tournament"), "league": m.get("league"), "matches": 0, "live": 0, "final": 0, "scheduled": 0}
        data[tid]["matches"] += 1
        if "IN_PROGRESS" in (m.get("status") or ""): data[tid]["live"] += 1
        elif "FINAL" in (m.get("status") or ""): data[tid]["final"] += 1
        else: data[tid]["scheduled"] += 1
    return jsonify({"date": date_str, "tournaments": list(data.values()), "errors": errors, "updated": datetime.now(timezone.utc).isoformat()})

@app.route("/api/tournament/<tournament_id>/fixture")
def tournament_fixture(tournament_id):
    date_str = request.args.get("date") or today_ymd()
    matches, errors = load_matches(date_str)
    filtered = [m for m in matches if (m.get("tournamentId") == tournament_id)]
    return jsonify({"date": date_str, "tournamentId": tournament_id, "matches": filtered, "errors": errors, "updated": datetime.now(timezone.utc).isoformat()})

@app.route("/api/match/<league>/<event_id>")
def match_detail(league, event_id):
    try:
        data = fetch_summary(league, event_id)
        header = data.get("header") or {}
        ev = {"id": event_id, "competitions": header.get("competitions", []), "date": None}
        match = normalize_event(ev, league)
        match["stats"] = parse_summary_stats(data)
        match["rawStatus"] = "ok"
        return jsonify(match)
    except Exception as e:
        return jsonify({"error": str(e), "eventId": event_id}), 500

@app.route("/api/live")
def live():
    date_str = request.args.get("date") or today_ymd()
    matches, errors = load_matches(date_str)
    live_matches = [m for m in matches if "IN_PROGRESS" in (m.get("status") or "")]
    return jsonify({"date": date_str, "matches": live_matches, "errors": errors, "updated": datetime.now(timezone.utc).isoformat()})

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
