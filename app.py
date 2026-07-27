from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
from html import unescape
import os
import re
import requests

app = Flask(__name__)
CORS(app)

SITE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
LEAGUES = ["atp", "wta"]
HEADERS_JSON = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
HEADERS_HTML = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"}

STATUS_ES = {
    "STATUS_SCHEDULED": "Programado",
    "STATUS_IN_PROGRESS": "En vivo",
    "STATUS_FINAL": "Final",
    "STATUS_RETIREMENT": "Retiro",
    "STATUS_WALKOVER": "Walkover",
    "STATUS_POSTPONED": "Atrasado",
    "STATUS_CANCELED": "Cancelado",
}

LIVE_WORDS = ["1st Set", "2nd Set", "3rd Set", "In Progress", "Suspended"]
FINAL_WORDS = ["Final", "Retired", "Walkover"]
ROUND_WORDS = ["Round", "Qualifying", "Quarterfinal", "Semifinal", "Final"]
SKIP_WORDS = [
    "Skip to", "Terms of Use", "Privacy", "Copyright", "GAMBLING", "ESPN", "Latest Tennis Videos",
    "Work for", "Corrections", "Children", "Nielsen", "Ad Sales", "Your Privacy", "Disney",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def today_ymd():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def slug(text):
    text = (text or "item").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "item"


def compact_date(date_text):
    return date_text.replace("-", "")


def parse_date_text(value):
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00")).date()
    except Exception:
        return None


def requested_date(date_text):
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    except Exception:
        return datetime.now(timezone.utc).date()


def fetch_json(url, timeout=20):
    r = requests.get(url, headers=HEADERS_JSON, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_text(url, timeout=20):
    r = requests.get(url, headers=HEADERS_HTML, timeout=timeout)
    r.raise_for_status()
    return r.text


def tournament_url(event_id, league):
    competition_type = "1" if league == "atp" else "2"
    return "https://www.espn.com/tennis/scoreboard/tournament/_/eventId/" + str(event_id) + "/competitionType/" + competition_type


def global_scoreboard_url(date_text):
    return "https://www.espn.com/tennis/scoreboard/_/date/" + compact_date(date_text)


def clean_lines(html):
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", html)
    text = unescape(text)
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if any(w in line for w in SKIP_WORDS):
            continue
        if line in ["2026", "2025", "2024", "2023", "2022", "2021", "2020", "2019", "2018", "2017", "2016", "2015"]:
            continue
        if line.startswith("-"):
            continue
        lines.append(line)
    return lines


def is_status_line(line):
    if line in FINAL_WORDS:
        return True
    if line in LIVE_WORDS:
        return True
    if re.match(r"^\d{1,2}:\d{2}\s?(AM|PM)$", line, re.I):
        return True
    return False


def status_from_label(label):
    if any(w.lower() in (label or "").lower() for w in ["1st set", "2nd set", "3rd set", "in progress", "suspended"]):
        return "STATUS_IN_PROGRESS", label
    if any(w.lower() in (label or "").lower() for w in ["final", "retired", "walkover"]):
        return "STATUS_FINAL", label
    return "STATUS_SCHEDULED", "Programado"


def looks_like_court_line(line):
    if " - " not in line:
        return False
    left = line.split(" - ", 1)[0]
    return any(w.lower() in left.lower() for w in ROUND_WORDS)


def looks_like_round(line):
    return any(w.lower() in line.lower() for w in ROUND_WORDS) and " - " not in line and len(line) <= 40


def looks_like_score(line):
    stripped = re.sub(r"[^0-9]", "", line)
    return bool(stripped) and len(stripped) == len(re.sub(r"\s", "", line))


def looks_like_name(line):
    if not re.search(r"[A-Za-z]", line):
        return False
    if is_status_line(line) or looks_like_round(line) or looks_like_court_line(line):
        return False
    bad = ["Defending Champion", "Men's Singles", "Women's Singles", "Men's Doubles", "Women's Doubles", "Mixed Doubles"]
    if line in bad:
        return False
    return True


def parse_players_and_scores(chunk):
    names = []
    scores = []
    for line in chunk:
        if looks_like_name(line):
            names.append(line)
        elif looks_like_score(line):
            scores.append(line)
    players = []
    for i, name in enumerate(names[:2]):
        players.append({
            "name": name,
            "score": scores[i] if i < len(scores) else "",
            "winner": False,
            "ranking": "En seguimiento",
        })
    return players, scores


def parse_tournament_page(url, event_id, league, tournament_name):
    info = {
        "title": tournament_name,
        "dateRangeText": "",
        "location": "",
        "competition": "",
        "defendingChampion": "",
        "matches": [],
        "detailUrl": url,
    }
    try:
        html = fetch_text(url, timeout=12)
    except Exception:
        return info
    lines = clean_lines(html)
    for i, line in enumerate(lines):
        if "Tennis Live Scores" in line:
            info["title"] = line.replace("Tennis Live Scores", "").replace("-", " ").strip()
        if re.search(r"[A-Za-z]+ \d{1,2} -", line) and not info["dateRangeText"]:
            info["dateRangeText"] = line
            if i + 1 < len(lines):
                info["location"] = lines[i + 1]
            if i + 2 < len(lines):
                info["competition"] = lines[i + 2]
        if line == "Defending Champion" and i + 1 < len(lines):
            info["defendingChampion"] = lines[i + 1]

    current_round = ""
    active_chunk = None
    match_index = 0
    for line in lines:
        if looks_like_round(line):
            current_round = line
            continue
        if is_status_line(line):
            active_chunk = [line]
            continue
        if active_chunk is not None:
            active_chunk.append(line)
            if looks_like_court_line(line):
                match_index += 1
                label = active_chunk[0]
                status, status_text = status_from_label(label)
                round_text, court = line.split(" - ", 1)
                players, raw_scores = parse_players_and_scores(active_chunk[1:-1])
                if status == "STATUS_FINAL" and len(players) >= 2:
                    players[0]["winner"] = True
                match_name = "Partido publicado"
                if len(players) >= 2:
                    match_name = players[0]["name"] + " vs " + players[1]["name"]
                elif len(players) == 1:
                    match_name = players[0]["name"] + " vs rival por publicar"
                item = {
                    "matchId": str(event_id) + "-" + str(match_index),
                    "eventId": str(event_id),
                    "league": league,
                    "tournament": info["title"] or tournament_name,
                    "tournamentId": slug(info["title"] or tournament_name),
                    "name": match_name,
                    "round": round_text or current_round or "Ronda",
                    "court": court,
                    "status": status,
                    "statusText": status_text,
                    "timeText": label if status == "STATUS_SCHEDULED" else "",
                    "players": players,
                    "winnerName": players[0]["name"] if status == "STATUS_FINAL" and len(players) >= 2 else None,
                    "scoreText": " / ".join(raw_scores),
                    "detailUrl": url,
                    "stats": build_stats(status, players, raw_scores, round_text, court),
                    "analysis": build_analysis(status, players, info.get("defendingChampion", "")),
                    "source": "compiled",
                    "lastUpdated": now_iso(),
                }
                info["matches"].append(item)
                active_chunk = None
    return info


def build_stats(status, players, scores, round_text, court):
    return {
        "ronda": round_text or "Ronda",
        "cancha": court or "Cancha",
        "marcador": " / ".join(scores) if scores else "En seguimiento",
        "estado": STATUS_ES.get(status, status),
        "jugadores": " vs ".join([p.get("name", "") for p in players]) if players else "Por confirmar",
    }


def build_analysis(status, players, defending_champion):
    names = [p.get("name") for p in players if p.get("name")]
    if status == "STATUS_FINAL" and len(names) >= 2:
        return {
            "title": "Resultado y lectura",
            "verdict": names[0] + " avanzo en el torneo.",
            "bullets": ["Marcador tomado del fixture del partido.", "El resultado queda guardado para el resumen del dia."],
        }
    if len(names) >= 2:
        bullets = [
            "Previa activa: comparar ranking, forma reciente, historial y desempeno por superficie.",
            "La app marcara favorito cuando el expediente tenga datos cruzados suficientes.",
        ]
        if defending_champion:
            bullets.append("Campeon defensor del torneo: " + defending_champion + ".")
        return {"title": "Previa del partido", "verdict": "Partido en seguimiento: " + names[0] + " vs " + names[1] + ".", "bullets": bullets}
    return {"title": "Programacion", "verdict": "Partido publicado por el torneo; rivales visibles cuando el cuadro los confirme.", "bullets": ["Hora, ronda y cancha quedan disponibles para seguimiento."]}


def base_channels(name, league):
    out = []
    if league == "atp":
        out.append({"name": "Transmision ATP", "url": "https://www.tennistv.com/"})
    if league == "wta":
        out.append({"name": "Transmision WTA", "url": "https://www.wtatennis.com/where-to-watch-tennis"})
    out.append({"name": "Entrar", "url": "https://www.espn.com/tennis/scoreboard"})
    return out


def tournament_card(event, league, date_text):
    comp = (event.get("competitions") or [{}])[0]
    notes = comp.get("notes") or []
    name = event.get("name") or event.get("shortName") or league.upper()
    if notes:
        name = notes[0].get("headline") or notes[0].get("type") or name
    event_id = str(event.get("id") or "")
    url = tournament_url(event_id, league)
    detail = parse_tournament_page(url, event_id, league, name)
    full_name = detail.get("title") or name
    event_day = parse_date_text(event.get("date") or comp.get("date"))
    req_day = requested_date(date_text)
    active = True
    if event_day:
        active = event_day <= req_day <= event_day + timedelta(days=9)
    matches = detail.get("matches", []) if active else []
    counts = count_matches(matches)
    return {
        "eventId": event_id,
        "league": league,
        "tournamentId": slug(full_name),
        "tournament": full_name,
        "round": detail.get("competition") or "Campeonato",
        "court": detail.get("location") or "Sede",
        "status": "STATUS_SCHEDULED" if active else "STATUS_FINAL",
        "statusText": "Vigente" if active else "Finalizado",
        "startTime": event.get("date") or comp.get("date"),
        "players": [],
        "winnerName": None,
        "channels": base_channels(full_name, league),
        "stats": {},
        "source": "compiled",
        "lastUpdated": now_iso(),
        "isTournamentOnly": True,
        "isActiveTournament": active,
        "detailUrl": url,
        "dateRangeText": detail.get("dateRangeText"),
        "location": detail.get("location"),
        "competition": detail.get("competition"),
        "defendingChampion": detail.get("defendingChampion"),
        "matches": matches,
        "counts": counts,
        "summary": tournament_summary_text(full_name, detail, counts),
    }


def count_matches(matches):
    live = len([m for m in matches if m.get("status") == "STATUS_IN_PROGRESS"])
    final = len([m for m in matches if m.get("status") == "STATUS_FINAL"])
    scheduled = len([m for m in matches if m.get("status") == "STATUS_SCHEDULED"])
    return {"live": live, "scheduled": scheduled, "final": final, "total": len(matches)}


def tournament_summary_text(name, detail, counts):
    parts = [name + ": "]
    bits = []
    if detail.get("location"):
        bits.append(detail.get("location"))
    if detail.get("competition"):
        bits.append(detail.get("competition"))
    bits.append(str(counts.get("live", 0)) + " en vivo")
    bits.append(str(counts.get("scheduled", 0)) + " programados")
    bits.append(str(counts.get("final", 0)) + " finalizados")
    return parts[0] + ", ".join(bits) + "."


def event_to_match(event, league):
    comp = (event.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None
    players = []
    for c in competitors[:2]:
        name = ((c.get("athlete") or {}).get("displayName") or c.get("displayName") or (c.get("team") or {}).get("displayName") or "")
        if not name:
            return None
        score = " ".join([str(x.get("displayValue") or x.get("value") or "") for x in c.get("linescores") or []]).strip()
        players.append({"name": name, "score": score, "winner": bool(c.get("winner")), "ranking": "En seguimiento"})
    status_type = comp.get("status", {}).get("type", {})
    status = status_type.get("name") or "STATUS_SCHEDULED"
    name = event.get("name") or event.get("shortName") or league.upper()
    venue = comp.get("venue") or {}
    return {
        "matchId": str(event.get("id") or ""),
        "eventId": str(event.get("id") or ""),
        "league": league,
        "tournament": name,
        "tournamentId": slug(name),
        "name": players[0]["name"] + " vs " + players[1]["name"],
        "round": status_type.get("shortDetail") or "Ronda",
        "court": venue.get("fullName") or venue.get("shortName") or "Cancha",
        "status": status,
        "statusText": STATUS_ES.get(status, status_type.get("description") or "Programado"),
        "timeText": "",
        "startTime": event.get("date") or comp.get("date"),
        "players": players,
        "winnerName": next((p["name"] for p in players if p.get("winner")), None),
        "stats": build_stats(status, players, [p.get("score", "") for p in players], status_type.get("shortDetail"), venue.get("shortName")),
        "analysis": build_analysis(status, players, ""),
        "detailUrl": global_scoreboard_url(today_ymd()),
        "source": "compiled",
        "lastUpdated": now_iso(),
    }


def load_fixture(date_text):
    tournaments = []
    matches = []
    errors = []
    for league in LEAGUES:
        url = SITE + "/" + league + "/scoreboard?dates=" + compact_date(date_text) + "&limit=500"
        try:
            data = fetch_json(url)
            for event in data.get("events", []):
                match = event_to_match(event, league)
                if match:
                    matches.append(match)
                else:
                    card = tournament_card(event, league, date_text)
                    if card.get("isActiveTournament"):
                        tournaments.append(card)
                        matches.extend(card.get("matches") or [])
        except Exception as exc:
            errors.append({"league": league, "error": str(exc)})
    matches.sort(key=lambda m: (m.get("status") != "STATUS_IN_PROGRESS", m.get("status") != "STATUS_SCHEDULED", m.get("timeText") or m.get("startTime") or ""))
    tournaments.sort(key=lambda t: t.get("startTime") or "")
    return matches, tournaments, errors


def day_summary(matches, tournaments):
    counts = count_matches(matches)
    highlight = None
    for m in matches:
        if m.get("status") == "STATUS_IN_PROGRESS":
            highlight = m
            break
    if not highlight and matches:
        highlight = matches[0]
    text = "Hoy hay " + str(len(tournaments)) + " torneos activos, " + str(counts["live"]) + " partidos en vivo, " + str(counts["scheduled"]) + " programados y " + str(counts["final"]) + " finalizados."
    if highlight:
        text += " Partido destacado: " + (highlight.get("name") or "partido publicado") + " en " + (highlight.get("tournament") or "torneo") + "."
    return {"text": text, "counts": counts, "highlight": highlight}


@app.route("/")
def home():
    return "Tenis Live Pro API - Smart Fixture"


@app.route("/api/version")
def version():
    return jsonify({"app": "Tenis Live Pro", "version": "SMART-FIXTURE-1", "status": "ok"})


@app.route("/api/fixture")
def fixture():
    date_text = request.args.get("date") or today_ymd()
    matches, tournaments, errors = load_fixture(date_text)
    return jsonify({"date": date_text, "updated": now_iso(), "matches": matches, "tournaments": tournaments, "summary": day_summary(matches, tournaments), "errors": errors})


@app.route("/api/tournament/<league>/<event_id>")
def tournament_detail(league, event_id):
    url = tournament_url(event_id, league)
    detail = parse_tournament_page(url, event_id, league, request.args.get("name") or "Torneo")
    return jsonify({"eventId": event_id, "league": league, "updated": now_iso(), "detail": detail})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
