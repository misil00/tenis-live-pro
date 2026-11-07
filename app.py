from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import unescape
import json, os, re, requests

app = Flask(__name__)
CORS(app)

BASE = "https://www.espn.com/tennis"
SITE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
ATHLETE_SITE = "https://site.web.api.espn.com/apis/common/v3/sports/tennis"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json",
}
LEAGUES = ["atp", "wta"]
FINAL_STATUSES = {"STATUS_FINAL", "STATUS_RETIRED", "STATUS_RETIREMENT", "STATUS_WALKOVER"}

STATUS_ES = {
    "STATUS_SCHEDULED": "Programado", "STATUS_IN_PROGRESS": "En vivo",
    "STATUS_FINAL": "Finalizado", "STATUS_RETIRED": "Retiro", "STATUS_RETIREMENT": "Retiro",
    "STATUS_WALKOVER": "Walkover", "STATUS_POSTPONED": "Pospuesto",
    "STATUS_CANCELED": "Cancelado", "STATUS_SUSPENDED": "Suspendido",
    "STATUS_DELAYED": "Retrasado",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def compact(d):
    return (d or today()).replace("-", "")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "item").lower()).strip("-") or "item"


def get(url, as_json=False, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json() if as_json else r.text


def clean_html(html):
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", "\n", html))
    skip = ["Skip to", "Terms of Use", "Privacy", "Copyright", "Disney", "Nielsen",
            "Your Privacy", "Children", "Work for ESPN", "Latest Tennis Videos"]
    return [line for raw in text.splitlines()
            if (line := re.sub(r"\s+", " ", raw).strip()) and not any(x in line for x in skip)]


def clean_text(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def es_text(v):
    v = v or ""
    repl = [("3rd Set", "3er set"), ("2nd Set", "2do set"), ("1st Set", "1er set"),
            ("In Progress", "En vivo"), ("Retired", "Retiro"), ("Suspended", "Suspendido"),
            ("Quarterfinal", "Cuartos"), ("Semifinal", "Semifinal"), ("Round", "Ronda"),
            ("Centre Court", "Cancha central"), ("Court", "Cancha"), ("Stadium", "Estadio"),
            ("Grandstand", "Cancha principal"), ("Qualifying", "Clasificación"),
            ("Men's Singles", "Individual masculino"), ("Women's Singles", "Individual femenino"),
            ("Men's Doubles", "Dobles masculino"), ("Women's Doubles", "Dobles femenino")]
    for a, b in repl:
        v = v.replace(a, b)
    return v


def image_href(v):
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("href") or v.get("url") or ""
    return ""


def country_flag(a):
    code = str(((a.get("citizenship") or {}).get("abbreviation") if isinstance(a.get("citizenship"), dict) else "")
               or ((a.get("flag") or {}).get("alt") if isinstance(a.get("flag"), dict) else "")).upper()
    if len(code) != 3 and isinstance(a.get("flag"), dict):
        m = re.search(r"/([a-z]{3})\.png", a["flag"].get("href") or "", re.I)
        code = m.group(1).upper() if m else code
    if len(code) != 3:
        return ""
    iso3_to_2 = {"USA":"US","ESP":"ES","ITA":"IT","GER":"DE","SRB":"RS","AUS":"AU","RUS":"RU",
                 "FRA":"FR","GBR":"GB","POL":"PL","BLR":"BY","KAZ":"KZ","JPN":"JP","CHN":"CN",
                 "CAN":"CA","CZE":"CZ","UKR":"UA","ARG":"AR","BRA":"BR","BEL":"BE","NED":"NL",
                 "SUI":"CH","AUT":"AT","CRO":"HR","GRE":"GR","NOR":"NO","DEN":"DK","SWE":"SE"}
    code2 = iso3_to_2.get(code)
    return "" if not code2 else "".join(chr(127397 + ord(c)) for c in code2)


def score_value(line):
    """Preserva juegos y desempate: 7(5), no solamente el primer número."""
    if not isinstance(line, dict):
        raw = str(line or "").strip()
        return raw if re.fullmatch(r"\d{1,2}(?:\(\d{1,2}\))?", raw) else ""
    raw = str(line.get("displayValue") if line.get("displayValue") is not None else line.get("value", "")).strip()
    m = re.search(r"\d{1,2}(?:\(\d{1,2}\))?", raw)
    base = m.group(0) if m else ""
    tb = line.get("tiebreak") or line.get("tiebreakScore") or line.get("tieBreak")
    if base == "0" and tb not in (None, ""):
        return str(tb)
    if base and tb not in (None, "") and "(" not in base:
        base += f"({tb})"
    return base


def score_sets_from_linescores(a_lines, b_lines):
    sets = []
    for i in range(max(len(a_lines or []), len(b_lines or []))):
        av = score_value(a_lines[i]) if i < len(a_lines or []) else ""
        bv = score_value(b_lines[i]) if i < len(b_lines or []) else ""
        if av or bv:  # conservar también el set actual aunque una fuente llegue parcialmente
            sets.append({"set": f"Set {i + 1}", "p1": av or "0", "p2": bv or "0"})
    return sets[:5]


def numeric_score(v):
    m = re.match(r"\d+", str(v or "0"))
    return int(m.group(0)) if m else 0


def detect_winner(players, sets, status):
    if status not in FINAL_STATUSES or len(players) < 2 or not sets:
        return
    a = sum(numeric_score(s["p1"]) > numeric_score(s["p2"]) for s in sets)
    b = sum(numeric_score(s["p2"]) > numeric_score(s["p1"]) for s in sets)
    if a != b:
        players[0 if a > b else 1]["winner"] = True


def point_scores(comp, competitors):
    situation = comp.get("situation") or {}
    values = situation.get("competitors") or situation.get("scores") or []
    result = ["", ""]
    if isinstance(values, list):
        by_id = {str(x.get("id")): x for x in values if isinstance(x, dict)}
        for i, c in enumerate(competitors[:2]):
            x = by_id.get(str(c.get("id"))) or (values[i] if i < len(values) and isinstance(values[i], dict) else {})
            result[i] = str(x.get("displayValue") or x.get("pointScore") or x.get("score") or "")
    for i, c in enumerate(competitors[:2]):
        result[i] = str(c.get("gameScore") or c.get("pointScore") or result[i] or "")
    return result


def match_format(ev, comp, league, tournament):
    """Usa el formato de ESPN; solo infiere por campeonato cuando la fuente no lo declara."""
    raw = comp.get("format") or ev.get("format") or {}
    best = None
    if isinstance(raw, dict):
        for key in ("bestOf", "numberOfSets", "sets", "maxSets"):
            try:
                value = int(raw.get(key))
                if value in (1, 3, 5): best = value; break
            except (TypeError, ValueError):
                pass
    type_text = " ".join(str(x or "") for x in [
        (comp.get("type") or {}).get("text"), (comp.get("type") or {}).get("abbreviation"),
        ev.get("name"), ev.get("shortName"), tournament]).lower()
    doubles = any(x in type_text for x in ("doubles", "dobles", " / "))
    qualifying = any(x in type_text for x in ("qualifying", "clasificación", "qualification"))
    grand_slam = any(x in type_text for x in ("australian open", "roland garros", "french open", "wimbledon", "us open"))
    source = "ESPN" if best else "Regla del campeonato"
    if not best:
        if grand_slam and league == "atp" and not doubles and not qualifying:
            best = 5
        else:
            best = 3
    if doubles:
        label = "Dobles: 2 sets y desempate decisivo si corresponde"
        sets_to_win = 2
    else:
        sets_to_win = 3 if best == 5 else 2
        label = f"Mejor de {best}: gana quien obtiene {sets_to_win} sets"
    return {"bestOf": best, "setsToWin": sets_to_win, "doubles": doubles,
            "grandSlam": grand_slam, "qualifying": qualifying, "label": label, "source": source,
            "raw": raw if isinstance(raw, dict) else {"value": str(raw)}}


def validate_score_source(a_lines, b_lines, sets, fmt):
    raw_a = [score_value(x) for x in (a_lines or [])]
    raw_b = [score_value(x) for x in (b_lines or [])]
    max_raw = max(len(raw_a), len(raw_b))
    same = len(sets) == max_raw
    for i, item in enumerate(sets):
        same = same and item["p1"] == (raw_a[i] or "0") and item["p2"] == (raw_b[i] or "0")
    within_format = len(sets) <= int(fmt.get("bestOf") or 5)
    ok = same and within_format
    return {"ok": ok, "source": "ESPN", "rawP1": raw_a, "rawP2": raw_b,
            "message": "Marcador validado con ESPN" if ok else "El marcador recibido no coincide completamente con ESPN"}


def player_from_competitor(c, point=""):
    roster = c.get("roster") or {}
    roster_athletes = roster.get("athletes") or []
    a = c.get("athlete") or (roster_athletes[0] if len(roster_athletes) == 1 else {})
    player_id = str(a.get("id") or c.get("id") or "")
    name = a.get("displayName") or roster.get("displayName") or c.get("displayName") or (c.get("team") or {}).get("displayName") or ""
    profile = ((a.get("links") or [{}])[0].get("href") if isinstance(a.get("links"), list) and a.get("links") else "")
    return {
        "id": player_id, "name": name, "shortName": a.get("shortName") or name,
        "flag": country_flag(a), "country": ((a.get("citizenship") or {}).get("displayName") if isinstance(a.get("citizenship"), dict) else "") or ((a.get("flag") or {}).get("alt") if isinstance(a.get("flag"), dict) else "") or "",
        "photo": image_href(a.get("headshot")) or (f"https://a.espncdn.com/i/headshots/tennis/players/full/{player_id}.png" if player_id and "-" not in player_id else ""),
        "profileUrl": profile or (f"https://www.espn.com/tennis/player/_/id/{player_id}/{slug(name)}" if player_id else ""),
        "age": a.get("age") or "", "height": a.get("displayHeight") or "", "weight": a.get("displayWeight") or "",
        "hand": a.get("hand") or a.get("stance") or "", "pointScore": point,
        "winner": bool(c.get("winner")), "seed": c.get("seed") or "", "rank": c.get("rank") or "",
    }


def fetch_site_matches(date_text):
    out, tours, errors = [], {}, []
    sources_ok = 0
    for league in LEAGUES:
        try:
            data = get(f"{SITE}/{league}/scoreboard?dates={compact(date_text)}&limit=800", True)
            if not isinstance(data, dict) or "events" not in data:
                raise ValueError("respuesta sin events")
            sources_ok += 1
        except Exception as exc:
            errors.append({"source": f"ESPN {league.upper()}", "message": str(exc)[:160]})
            continue
        entries = []
        for ev in data.get("events", []):
            for group in ev.get("groupings") or []:
                for comp in group.get("competitions") or []:
                    start_text = comp.get("date") or comp.get("startDate") or ""
                    # ESPN devuelve el torneo completo; conservar solamente el día solicitado.
                    try:
                        local_day = datetime.fromisoformat(start_text.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
                    except (ValueError, TypeError):
                        local_day = start_text[:10]
                    if local_day == date_text:
                        entries.append((ev, comp))
        for ev, comp in entries:
            competitors = comp.get("competitors") or []
            if len(competitors) < 2:
                continue
            tournament = ev.get("name") or ev.get("shortName") or league.upper()
            tid = slug(tournament)
            tours.setdefault(tid, {"tournamentId": tid, "tournament": tournament,
                "location": (comp.get("venue") or {}).get("fullName") or "", "dateRangeText": "",
                "surface": (comp.get("venue") or {}).get("surface") or "", "league": league,
                "counts": {"live": 0, "scheduled": 0, "final": 0, "total": 0}, "summary": ""})
            st = (comp.get("status") or {}).get("type") or {}
            status = st.get("name") or "STATUS_SCHEDULED"
            detail = st.get("shortDetail") or st.get("detail") or st.get("description") or ""
            points = point_scores(comp, competitors)
            players = [player_from_competitor(c, points[i]) for i, c in enumerate(competitors[:2])]
            a_lines, b_lines = competitors[0].get("linescores") or [], competitors[1].get("linescores") or []
            sets = score_sets_from_linescores(a_lines, b_lines)
            fmt = match_format(ev, comp, league, tournament)
            score_validation = validate_score_source(a_lines, b_lines, sets, fmt)
            if status == "STATUS_SCHEDULED":
                sets, points = [], ["", ""]
                for p in players: p["pointScore"] = ""
            detect_winner(players, sets, status)
            status_text = es_text(detail) if detail else STATUS_ES.get(status, es_text(status))
            winner = next((p["name"] for p in players if p.get("winner")), None)
            out.append({
                "matchId": str(comp.get("id") or ""), "eventId": str(ev.get("id") or ""), "league": league,
                "tournament": tournament, "tournamentId": tid, "category": league.upper(),
                "name": players[0]["name"] + " vs " + players[1]["name"],
                "round": es_text((comp.get("round") or {}).get("displayName") or (comp.get("type") or {}).get("text") or "Ronda"),
                "court": es_text((comp.get("venue") or {}).get("court") or (comp.get("venue") or {}).get("shortName") or (comp.get("venue") or {}).get("fullName") or ""),
                "status": status, "statusText": status_text, "timeText": "", "startTime": ev.get("date") or comp.get("date"),
                "players": players, "scoreSets": sets, "pointScores": points,
                "format": fmt, "scoreValidation": score_validation,
                "scoreText": ", ".join(f"{s['p1']}-{s['p2']}" for s in sets), "winnerName": winner,
                "stats": {"Estado": status_text, "Ronda": es_text((comp.get("round") or {}).get("displayName") or (comp.get("type") or {}).get("text") or ""),
                          "Cancha": es_text((comp.get("venue") or {}).get("court") or (comp.get("venue") or {}).get("fullName") or ""), "Ganador": winner or ""},
                "detailUrl": BASE + "/scoreboard", "updated": now_iso(),
            })
    out = list({m["matchId"]: m for m in out}.values())
    for t in tours.values():
        tm = [m for m in out if m["tournamentId"] == t["tournamentId"]]
        t["counts"] = {"live": sum(m["status"] == "STATUS_IN_PROGRESS" for m in tm),
                       "scheduled": sum(m["status"] == "STATUS_SCHEDULED" for m in tm),
                       "final": sum(m["status"] in FINAL_STATUSES for m in tm), "total": len(tm)}
        t["summary"] = f"{t['tournament']}: {t['counts']['live']} en vivo, {t['counts']['scheduled']} programados y {t['counts']['final']} finalizados."
    order = {"STATUS_IN_PROGRESS": 0, "STATUS_SCHEDULED": 1, "STATUS_FINAL": 2, "STATUS_RETIREMENT": 2, "STATUS_WALKOVER": 2}
    out.sort(key=lambda m: (order.get(m["status"], 9), m.get("startTime") or "", m["tournament"]))
    return out, list(tours.values()), errors, sources_ok


def parse_rankings(kind="atp"):
    if kind not in LEAGUES:
        return [], "tipo inválido"
    try:
        data = get(f"{SITE}/{kind}/rankings?region=mx&lang=es", True)
    except Exception as exc:
        return [], str(exc)[:160]
    ranking = next((item for item in data.get("rankings", []) if item.get("type") == kind), None)
    if not ranking:
        return [], f"ESPN no entregó el ranking {kind.upper()}"
    unique = {}
    for item in ranking.get("ranks", []):
        athlete = item.get("athlete") or {}
        try:
            position = int(item.get("current"))
        except (TypeError, ValueError):
            continue
        player_id = str(athlete.get("id") or "")
        name = athlete.get("displayName") or ""
        if not name or not player_id or not 1 <= position <= 150:
            continue
        links = athlete.get("links") or []
        unique[position] = {
            "rank": position, "move": item.get("trend") or "-", "name": name,
            "points": str(item.get("points") or ""), "age": athlete.get("age") or "",
            "id": player_id, "league": kind,
            "country": athlete.get("citizenshipCountry") or athlete.get("flagAltText") or "",
            "flagImage": athlete.get("flag") or "",
            "photo": athlete.get("headshot") or f"https://a.espncdn.com/i/headshots/tennis/players/full/{player_id}.png",
            "profileUrl": next((link.get("href") for link in links if "playercard" in (link.get("rel") or [])), ""),
        }
    result = [unique[position] for position in range(1, 151) if position in unique]
    return result, "" if len(result) == 150 else f"ESPN entregó {len(result)} de 150 posiciones verificables"


def parse_tennis_news(limit=20):
    feeds, errors = [], []
    for league in LEAGUES:
        try:
            feeds.extend((get(f"{SITE}/{league}/news?limit={max(20, min(limit * 2, 40))}", True).get("articles") or []))
        except Exception as exc:
            errors.append(f"{league.upper()}: {str(exc)[:100]}")
    if not feeds:
        return [], "; ".join(errors) or "Noticias no disponibles"
    keywords = ("injur", "withdraw", "retir", "suspend", "ban", "sanction", "title", "champion", "final",
                "upset", "record", "rank", "draw", "lesión", "baja", "sanción", "campeón")
    articles = []
    seen = set()
    for a in feeds:
        title, desc = (a.get("headline") or a.get("title") or "").strip(), (a.get("description") or "").strip()
        article_id = str(a.get("id") or title).lower()
        if not title or article_id in seen:
            continue
        seen.add(article_id)
        links = a.get("links") or {}
        importance = sum(k in (title + " " + desc).lower() for k in keywords)
        articles.append({"title": title, "description": desc,
            "url": ((links.get("web") or {}).get("href") or (links.get("mobile") or {}).get("href") or ""),
            "published": a.get("published") or a.get("lastModified") or now_iso(), "source": "ESPN",
            "important": importance > 0, "importance": importance})
    articles.sort(key=lambda a: (a["important"], a["published"]), reverse=True)
    return articles[:limit], "; ".join(errors)


def find_value(obj, *keys):
    if not isinstance(obj, dict): return ""
    for key in keys:
        v = obj.get(key)
        if v not in (None, "", [], {}):
            if isinstance(v, dict): return v.get("displayValue") or v.get("value") or v.get("name") or ""
            return v
    return ""


def player_profile(league, player_id):
    urls = [f"{ATHLETE_SITE}/{league}/athletes/{player_id}", f"{SITE}/{league}/athletes/{player_id}"]
    last_error = ""
    for url in urls:
        try:
            data = get(url, True)
            a = data.get("athlete") or data.get("player") or data
            if not isinstance(a, dict) or not (a.get("displayName") or a.get("fullName")):
                continue
            stats = data.get("statistics") or a.get("statistics") or []
            flat = {}
            if isinstance(stats, list):
                for s in stats:
                    if isinstance(s, dict): flat[str(s.get("name") or s.get("label") or "").lower()] = s.get("displayValue") or s.get("value")
            return {"id": str(player_id), "league": league, "name": a.get("displayName") or a.get("fullName"),
                "photo": image_href(a.get("headshot")) or f"https://a.espncdn.com/i/headshots/tennis/players/full/{player_id}.png",
                "country": find_value(a, "citizenship", "country"),
                "age": find_value(a, "age"), "birthDate": find_value(a, "dateOfBirth", "birthDate"),
                "height": find_value(a, "displayHeight", "height"), "weight": find_value(a, "displayWeight", "weight"),
                "hand": find_value(a, "hand", "stance"), "turnedPro": find_value(a, "debutYear", "turnedPro"),
                "rank": find_value(a, "rank"), "careerHigh": find_value(a, "careerHighRank", "careerHigh"),
                "titles": flat.get("titles") or flat.get("career titles") or find_value(a, "titles", "careerTitles"),
                "grandSlams": flat.get("grand slam titles") or find_value(a, "grandSlamTitles"),
                "record": flat.get("record") or flat.get("career record") or find_value(a, "record"),
                "prizeMoney": flat.get("prize money") or find_value(a, "displayPrizeMoney", "prizeMoney"),
                "profileUrl": next((x.get("href") for x in (a.get("links") or []) if isinstance(x, dict) and x.get("href")), ""),
                "updated": now_iso()}, ""
        except Exception as exc:
            last_error = str(exc)[:160]
    try:
        url = f"https://www.espn.com/tennis/player/_/id/{player_id}"
        html = get(url)
        text = " ".join(clean_html(html))
        title = re.search(r"<title>([^<]+)", html, re.I)
        name = re.sub(r"\s+(Stats|Noticias|News|Pictures)[\s\S]*$", "", unescape(title.group(1)), flags=re.I).strip() if title else ""
        def grab(pattern):
            m = re.search(pattern, text, re.I)
            return m.group(1).strip(" :-") if m else ""
        career = re.search(r"Career\s+(\d+)\s+(\d+)\s+(\d+\s*-\s*\d+)", text, re.I)
        if name:
            return {"id": str(player_id), "league": league, "name": name,
                "photo": f"https://a.espncdn.com/i/headshots/tennis/players/full/{player_id}.png",
                "age": grab(r"Age\s*:?\s*(\d{1,2})"),
                "birthDate": grab(r"Birth Date\s*:?\s*(.+?)(?=\s+Hometown|\s+Height|\s+Weight|$)"),
                "height": grab(r"Height\s*:?\s*([0-9'\"\-\.\s]+?)(?=\s+Weight|\s+Turned|\s+Career|$)"),
                "weight": grab(r"Weight\s*:?\s*([0-9]+\s*lbs?)"),
                "hand": grab(r"\b(Left|Right)\b\s+Turned Pro"),
                "turnedPro": grab(r"Turned Pro\s*:?\s*(\d{4})"),
                "rank": grab(r"(?:ATP|WTA) Rank\s*#?\s*(\d+)"),
                "titles": career.group(1) if career else "", "grandSlams": career.group(2) if career else "",
                "record": re.sub(r"\s+", "", career.group(3)) if career else "",
                "prizeMoney": grab(r"Career\s*(\$[0-9,]+)"), "profileUrl": url, "updated": now_iso()}, ""
    except Exception as exc:
        last_error = str(exc)[:160]
    return {}, last_error or "perfil no disponible"


@app.route("/")
def home(): return "OK"


@app.route("/api/version")
def version(): return jsonify({"app": "Tenis Live Pro", "version": "PRO-TENNIS-LIVE-2", "status": "ok"})


@app.route("/api/fixture")
def fixture():
    d = request.args.get("date") or today()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return jsonify({"error": "Fecha inválida"}), 400
    matches, tournaments, errors, sources_ok = fetch_site_matches(d)
    return jsonify({"date": d, "updated": now_iso(), "matches": matches, "tournaments": tournaments,
                    "validated": sources_ok == len(LEAGUES), "sourcesOk": sources_ok, "errors": errors})


@app.route("/api/rankings")
def rankings():
    kind = (request.args.get("type") or "atp").lower()
    players, error = parse_rankings(kind)
    return jsonify({"type": kind, "updated": now_iso(), "players": players, "complete": len(players) == 150,
                    "expected": 150, "error": error or None})


@app.route("/api/news")
def news():
    try: limit = max(1, min(int(request.args.get("limit") or 12), 30))
    except ValueError: return jsonify({"error": "Límite inválido"}), 400
    articles, error = parse_tennis_news(limit)
    return jsonify({"updated": now_iso(), "articles": articles, "validated": not bool(error), "error": error or None})


@app.route("/api/player")
def player():
    league, pid = (request.args.get("league") or "").lower(), request.args.get("id") or ""
    if league not in LEAGUES or not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
        return jsonify({"error": "Jugador inválido"}), 400
    profile, error = player_profile(league, pid)
    return jsonify({"player": profile, "validated": bool(profile), "error": error or None})


def gemini_answer(question):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None, "GEMINI_API_KEY no está configurada."

    model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview").strip()
    if not re.fullmatch(r"gemini-[A-Za-z0-9._-]+", model):
        return None, "GEMINI_MODEL no tiene un formato válido."
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "systemInstruction": {
            "parts": [{
                "text": (
                    "Responde siempre en español latinoamericano natural, claro y fluido. "
                    "Contesta directamente lo que preguntan, sin copiar titulares como si fueran una respuesta. "
                    "Para datos actuales, usa Google Search y basa la respuesta en fuentes verificables. "
                    "No inventes marcadores, fechas ni posiciones; si no puedes confirmar un dato, dilo. "
                    "Mantén la respuesta breve y distingue los rankings ATP y WTA."
                )
            }]
        },
        "contents": [{"role": "user", "parts": [{"text": question}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 700},
    }
    try:
        response = requests.post(
            url,
            headers={"x-goog-api-key": api_key},
            json=payload,
            timeout=(5, 25),
        )
    except requests.Timeout:
        return None, "Gemini agotó el tiempo de espera."
    except requests.RequestException:
        app.logger.exception("No se pudo conectar con Gemini")
        return None, "No se pudo conectar con Gemini."

    if response.status_code == 429:
        return None, "Gemini alcanzó su límite de uso."
    if response.status_code in (401, 403):
        app.logger.error("Gemini rechazó la clave de API (HTTP %s)", response.status_code)
        return None, f"Gemini rechazó la clave (HTTP {response.status_code})."
    if not response.ok:
        app.logger.error("Gemini respondió con HTTP %s", response.status_code)
        return None, f"Gemini respondió con HTTP {response.status_code}."

    try:
        data = response.json()
    except ValueError:
        app.logger.error("Gemini devolvió una respuesta que no es JSON")
        return None, "Gemini devolvió una respuesta inválida."

    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
    answer = " ".join(
        part.get("text", "").strip()
        for part in parts
        if isinstance(part, dict) and part.get("text", "").strip()
    )
    if not answer:
        return None, "Gemini no devolvió una respuesta."

    grounding = candidates[0].get("groundingMetadata") or {}
    sources, seen_urls = [], set()
    for chunk in grounding.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        source_url = web.get("uri", "")
        if source_url.startswith("https://") and source_url not in seen_urls:
            seen_urls.add(source_url)
            sources.append({
                "title": clean_text(web.get("title") or source_url),
                "url": source_url,
                "source": "Google Search",
            })
    return {"answer": answer, "sources": sources, "updated": now_iso(), "query": question,
            "provider": "Gemini"}, None


def groq_answer(question):
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None, "GROQ_API_KEY no está configurada."

    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", model):
        return None, "GROQ_MODEL no tiene un formato válido."
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Responde siempre en español latinoamericano natural, claro y fluido. "
                    "Contesta directamente y con brevedad. No inventes resultados, rankings, "
                    "fechas ni datos actuales. Si la pregunta requiere información en tiempo real "
                    "que no aparece en la conversación, dilo claramente y no adivines."
                ),
            },
            {"role": "user", "content": question},
        ],
        "temperature": 0.2,
        "max_tokens": 700,
    }
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=(5, 25),
        )
    except requests.Timeout:
        return None, "Groq agotó el tiempo de espera."
    except requests.RequestException:
        app.logger.exception("No se pudo conectar con Groq")
        return None, "No se pudo conectar con Groq."

    if response.status_code == 429:
        return None, "Groq alcanzó su límite de uso."
    if response.status_code in (401, 403):
        app.logger.error("Groq rechazó la clave de API (HTTP %s)", response.status_code)
        return None, f"Groq rechazó la clave (HTTP {response.status_code})."
    if not response.ok:
        app.logger.error("Groq respondió con HTTP %s", response.status_code)
        return None, f"Groq respondió con HTTP {response.status_code}."
    try:
        data = response.json()
    except ValueError:
        app.logger.error("Groq devolvió una respuesta que no es JSON")
        return None, "Groq devolvió una respuesta inválida."

    choices = data.get("choices") or []
    answer = ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
    if not answer:
        return None, "Groq no devolvió una respuesta."
    return {"answer": answer, "sources": [], "updated": now_iso(), "query": question,
            "provider": "Groq"}, None


@app.post("/api/consulta")
def consulta():
    body = request.get_json(silent=True) or {}
    question = re.sub(r"\s+", " ", str(body.get("question") or "")).strip()
    subject = re.sub(r"\s+", " ", str(body.get("subject") or "")).strip()[:80]
    if len(question) < 3 or len(question) > 300:
        return jsonify({"error": "Escribe una pregunta válida."}), 400
    if subject:
        question = f"{question}\nContexto: {subject}"
    failures = []
    for provider in (gemini_answer, groq_answer):
        result, error = provider(question)
        if result:
            return jsonify(result)
        failures.append(error)
        app.logger.warning("Consulta fallida: %s", error)
    return jsonify({"error": "No se pudo responder la consulta. " + " ".join(failures)}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
