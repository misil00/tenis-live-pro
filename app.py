from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import re
from html import unescape
from datetime import datetime, timezone, date

app = Flask(__name__)
CORS(app)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Referer": "https://www.espn.com/tennis/scoreboard",
}

SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
ESPN_HOST = "www.espn.com"
LEAGUES = ["atp", "wta"]

STATUS_ES = {
    "STATUS_SCHEDULED": "Programado",
    "STATUS_IN_PROGRESS": "En vivo",
    "STATUS_FINAL": "Final",
    "STATUS_POSTPONED": "Atrasado",
    "STATUS_DELAYED": "Atrasado",
    "STATUS_CANCELED": "Cancelado",
    "STATUS_RETIREMENT": "Retiro",
    "STATUS_WALKOVER": "Walkover",
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def today_ymd():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def yyyymmdd(date_str):
    return date_str.replace("-", "")


def slugify(text):
    text = (text or "torneo").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "torneo"


def tournament_url(event_id, competition_type):
    return "https://" + ESPN_HOST + f"/tennis/scoreboard/tournament/_/eventId/{event_id}/competitionType/{competition_type}"


def get_json(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_text(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def channels_for(tournament, league):
    name = (tournament or "").lower()
    links = []
    if any(x in name for x in ["wimbledon", "us open", "australian", "roland", "french open"]):
        links.append({"name": "ESPN", "url": "https://www.espn.com/watch/"})
        links.append({"name": "Watch ESPN Tennis", "url": "https://www.espn.com/watch/catalog/aa07e63d-cf6e-3705-9c8d-8d5208e6af14/tennis"})
    if league == "atp":
        links.append({"name": "Tennis TV ATP", "url": "https://www.tennistv.com/"})
    if league == "wta":
        links.append({"name": "WTA Donde ver", "url": "https://www.wtatennis.com/where-to-watch-tennis"})
    links.append({"name": "Tennis Channel", "url": "https://www.tennischannel.com/"})
    return links


def strip_tags(html):
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    txt = re.sub(r"<[^>]+>", "\n", html)
    txt = unescape(txt)
    return [x.strip() for x in txt.splitlines() if x.strip()]


def parse_date_range(text):
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?\s*-\s*(?:([A-Za-z]+)\s+)?(\d{1,2}),\s*(\d{4})", text or "")
    if not m:
        return None
    m1, d1, y1, m2, d2, y2 = m.groups()
    mon1 = MONTHS.get(m1.lower())
    mon2 = MONTHS.get((m2 or m1).lower())
    if not mon1 or not mon2:
        return None
    try:
        start = date(int(y1 or y2), mon1, int(d1))
        end = date(int(y2), mon2, int(d2))
        return start, end
    except Exception:
        return None


def tournament_page_info(event_id, competition_type, league):
    url = tournament_url(event_id, competition_type)
    info = {"url": url, "dateRangeText": "", "startDate": None, "endDate": None, "location": "", "competition": ""}
    try:
        lines = strip_tags(get_text(url))
    except Exception:
        return info

    title = next((x for x in lines if "Tennis Live Scores" in x), "")
    if title:
        info["pageTitle"] = title.replace("Tennis Live Scores - ESPN", "").strip()

    date_line = next((x for x in lines if parse_date_range(x)), "")
    if date_line:
        info["dateRangeText"] = date_line
        rng = parse_date_range(date_line)
        if rng:
            info["startDate"] = rng[0].isoformat()
            info["endDate"] = rng[1].isoformat()
            try:
                idx = lines.index(date_line)
                if idx + 1 < len(lines):
                    info["location"] = lines[idx + 1]
                if idx + 2 < len(lines):
                    info["competition"] = lines[idx + 2]
            except ValueError:
                pass
    return info


def is_active_range(info, date_str):
    if not info.get("startDate") or not info.get("endDate"):
        return True
    try:
        current = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = datetime.strptime(info["startDate"], "%Y-%m-%d").date()
        end = datetime.strptime(info["endDate"], "%Y-%m-%d").date()
        return start <= current <= end
    except Exception:
        return True


def athlete_name(c):
    return ((c.get("athlete") or {}).get("displayName") or c.get("displayName") or (c.get("team") or {}).get("displayName") or "")


def player_score(c):
    vals = []
    for x in c.get("linescores") or []:
        v = x.get("displayValue") or x.get("value")
        if v is not None and str(v) != "":
            vals.append(str(v))
    if vals:
        return " ".join(vals)
    return str(c.get("score") or "")


def normalize_match_event(ev, league, tournament_fallback=None):
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None

    stype = comp.get("status", {}).get("type", {})
    status = stype.get("name") or ev.get("status", {}).get("type", {}).get("name") or "STATUS_SCHEDULED"
    status_text = STATUS_ES.get(status, stype.get("description") or stype.get("shortDetail") or "Programado")

    players = []
    for c in competitors[:2]:
        name = athlete_name(c)
        if not name:
            return None
        players.append({
            "id": (c.get("athlete") or {}).get("id") or c.get("id"),
            "name": name,
            "seed": c.get("seed") or (c.get("curatedRank") or {}).get("current"),
            "score": player_score(c),
            "winner": bool(c.get("winner")),
        })

    tournament = tournament_fallback or ev.get("season", {}).get("displayName") or ev.get("group", {}).get("name") or ("ATP" if league == "atp" else "WTA")
    venue = comp.get("venue") or {}
    notes = comp.get("notes") or []
    round_name = ""
    if notes:
        round_name = notes[0].get("headline") or notes[0].get("type") or ""
    if not round_name:
        round_name = stype.get("shortDetail") or ev.get("shortName") or "Ronda por confirmar"

    return {
        "eventId": str(ev.get("id") or ""),
        "league": league,
        "tournamentId": slugify(tournament),
        "tournament": tournament,
        "round": round_name,
        "court": venue.get("fullName") or venue.get("shortName") or "Cancha por confirmar",
        "status": status,
        "statusText": status_text,
        "startTime": ev.get("date") or comp.get("date"),
        "players": players,
        "winnerName": next((p["name"] for p in players if p.get("winner")), None),
        "channels": channels_for(tournament, league),
        "stats": {},
        "source": "ESPN",
        "lastUpdated": now_iso(),
    }


def tournament_summary_event(ev, league, date_str):
    comp = (ev.get("competitions") or [{}])[0]
    tournament = ev.get("name") or ev.get("shortName") or ("ATP" if league == "atp" else "WTA")
    comp_type = "1" if league == "atp" else "2"
    event_id = str(ev.get("id") or "")
    info = tournament_page_info(event_id, comp_type, league)
    if info.get("pageTitle") and len(info["pageTitle"]) > len(tournament):
        tournament = info["pageTitle"]
    stype = comp.get("status", {}).get("type", {}) or {}
    active = is_active_range(info, date_str)
    return {
        "eventId": event_id,
        "league": league,
        "tournamentId": slugify(tournament),
        "tournament": tournament,
        "round": info.get("competition") or "Campeonato",
        "court": info.get("location") or "Sede por confirmar",
        "status": stype.get("name") or "STATUS_SCHEDULED",
        "statusText": "Vigente" if active else "No vigente",
        "startTime": ev.get("date") or comp.get("date"),
        "players": [],
        "winnerName": None,
        "channels": channels_for(tournament, league),
        "stats": {},
        "source": "ESPN",
        "lastUpdated": now_iso(),
        "isTournamentOnly": True,
        "espnTournamentUrl": info.get("url"),
        "dateRangeText": info.get("dateRangeText"),
        "location": info.get("location"),
        "competition": info.get("competition"),
        "isActiveTournament": active,
    }


def fetch_scoreboard(league, date_str):
    url = f"{SITE_BASE}/{league}/scoreboard?dates={yyyymmdd(date_str)}&limit=500"
    return get_json(url)


def load_matches(date_str):
    matches, tournaments, errors = [], [], []
    for league in LEAGUES:
        try:
            data = fetch_scoreboard(league, date_str)
            for ev in data.get("events", []):
                match = normalize_match_event(ev, league)
                if match:
                    matches.append(match)
                else:
                    card = tournament_summary_event(ev, league, date_str)
                    if card.get("isActiveTournament"):
                        tournaments.append(card)
        except Exception as e:
            errors.append({"league": league, "error": str(e)})
    matches.sort(key=lambda m: m.get("startTime") or "")
    tournaments.sort(key=lambda m: m.get("startTime") or "")
    return matches, tournaments, errors


@app.route("/")
def index():
    return "Tenis Live Pro API - ESPN v1.0 FINAL3"


@app.route("/api/version")
def version():
    return jsonify({"app": "Tenis Live Pro", "version": "1.0 FINAL3", "source": "ESPN", "fix": "clean_file_no_hidden_chars"})


@app.route("/api/fixture")
def fixture():
    date_str = request.args.get("date") or today_ymd()
    matches, tournaments, errors = load_matches(date_str)
    return jsonify({"date": date_str, "source": "ESPN", "updated": now_iso(), "matches": matches, "tournaments": tournaments, "errors": errors})


@app.route("/api/tournaments/today")
def tournaments_today():
    date_str = request.args.get("date") or today_ymd()
    matches, tournaments, errors = load_matches(date_str)
    data = {}
    for m in matches + tournaments:
        tid = m.get("tournamentId") or slugify(m.get("tournament"))
        if tid not in data:
            data[tid] = {"id": tid, "name": m.get("tournament"), "league": m.get("league"), "matches": 0, "live": 0, "final": 0, "scheduled": 0, "espnTournamentUrl": m.get("espnTournamentUrl"), "dateRangeText": m.get("dateRangeText"), "location": m.get("location")}
        data[tid]["matches"] += 1
        if "IN_PROGRESS" in (m.get("status") or ""):
            data[tid]["live"] += 1
        elif "FINAL" in (m.get("status") or ""):
            data[tid]["final"] += 1
        else:
            data[tid]["scheduled"] += 1
    return jsonify({"date": date_str, "source": "ESPN", "tournaments": list(data.values()), "errors": errors, "updated": now_iso()})


@app.route("/api/tournament/<tournament_id>/fixture")
def tournament_fixture(tournament_id):
    date_str = request.args.get("date") or today_ymd()
    matches, tournaments, errors = load_matches(date_str)
    return jsonify({"date": date_str, "source": "ESPN", "tournamentId": tournament_id, "matches": [m for m in matches if m.get("tournamentId") == tournament_id], "tournamentCards": [m for m in tournaments if m.get("tournamentId") == tournament_id], "errors": errors, "updated": now_iso()})


@app.route("/api/live")
def live():
    date_str = request.args.get("date") or today_ymd()
    matches, tournaments, errors = load_matches(date_str)
    return jsonify({"date": date_str, "source": "ESPN", "matches": [m for m in matches if "IN_PROGRESS" in (m.get("status") or "")], "errors": errors, "updated": now_iso()})


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
