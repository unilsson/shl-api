import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles


# ============================================================
# Configuration
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

# Läs .env från samma katalog som app.py
load_dotenv(BASE_DIR / ".env")


def require_env(name: str) -> str:
    """
    Läs en obligatorisk miljövariabel.

    Appen ska inte innehålla miljöspecifika fallbackvärden.
    All konfiguration ligger i .env.
    """
    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Obligatorisk konfiguration saknas: {name}. "
            "Skapa .env från .env.example och fyll i värdet."
        )

    return value.strip()


def env_path(name: str) -> Path:
    """
    Läs en obligatorisk sökväg från miljön.

    Relativa paths räknas från katalogen där app.py ligger.
    """
    path = Path(require_env(name)).expanduser()

    if not path.is_absolute():
        path = BASE_DIR / path

    return path.resolve()


SHL_URL = require_env("SHL_URL")
SHL_SEASON_UUID = require_env("SHL_SEASON_UUID")
SHL_SERIES_UUID = require_env("SHL_SERIES_UUID")
SHL_GAME_TYPE_UUID = require_env("SHL_GAME_TYPE_UUID")
SHL_GAME_PLACE = require_env("SHL_GAME_PLACE")
SHL_PLAYED = require_env("SHL_PLAYED")

DATA_DIR = env_path("SHL_DATA_DIR")
CACHE_FILE = env_path("SHL_CACHE_FILE")

TIMEZONE_NAME = require_env("SHL_TIMEZONE")
LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)

SHL_MIN_GAMES = int(require_env("SHL_MIN_GAMES"))
SHL_REQUEST_TIMEOUT = float(require_env("SHL_REQUEST_TIMEOUT"))
SHL_USER_AGENT = require_env("SHL_USER_AGENT")
SHL_LOGO_BASE_URL = require_env("SHL_LOGO_BASE_URL").rstrip("/")
LOGO_DIR = env_path("SHL_LOGO_DIR")
LOGO_INDEX_FILE = LOGO_DIR / "index.json"
PUBLIC_BASE_URL = require_env("PUBLIC_BASE_URL").rstrip("/")
API_ROOT_PATH = require_env("API_ROOT_PATH")

LOGO_DIR.mkdir(parents=True, exist_ok=True)


TEAM_LOGO_SLUGS = {
    "Björklöven": "ifb1_ifb",
    "Brynäs IF": "bif1_bif",
    "Djurgården Hockey Herr": "dif1_dif",
    "Frölunda HC": "fhc1_fhc",
    "Färjestad BK": "fbk1_fbk",
    "HV71": "hv711_hv71",
    "Linköping HC": "lhc1_lhc",
    "Luleå Hockey": "lhf1_lhf",
    "Malmö Redhawks": "mif1_mif",
    "Rögle BK": "rbk1_rbk",
    "Skellefteå AIK": "saik1_saik",
    "Timrå IK": "tik1_tik",
    "Växjö Lakers": "vlh1_vlh",
    "Örebro Hockey": "ohk1_ohk",
}


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="SHL API",
    description="Lokalt cache-API för SHL:s spelschema",
    version="1.1.1",
    root_path=API_ROOT_PATH,
)

app.mount(
    "/logos",
    StaticFiles(directory=str(LOGO_DIR)),
    name="logos",
)


# ============================================================
# Helpers
# ============================================================

def load_schedule() -> dict:
    """
    Läs aktuell SHL-cache från disk.
    """

    if not CACHE_FILE.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "Ingen lokal SHL-cache finns ännu. "
                "Kör POST /refresh för att hämta schemat."
            ),
        )

    try:
        with CACHE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"SHL-cachen innehåller ogiltig JSON: {exc}",
        )

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Kunde inte läsa SHL-cachen: {exc}",
        )

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail="SHL-cachen har oväntat format.",
        )

    return data


def get_games(data: dict) -> list:
    """
    Hämta gameInfo-arrayen och verifiera formatet.
    """

    games = data.get("gameInfo", [])

    if not isinstance(games, list):
        raise HTTPException(
            status_code=500,
            detail="gameInfo saknas eller har fel format i SHL-cachen.",
        )

    return games


def team_name(team: dict) -> str:
    """
    Hämta bästa tillgängliga namn för ett lag.
    """

    if not isinstance(team, dict):
        return "Okänt lag"

    names = team.get("names") or {}

    return (
        team.get("siteDisplayName")
        or names.get("longSite")
        or names.get("shortSite")
        or names.get("long")
        or names.get("short")
        or names.get("full")
        or team.get("code")
        or "Okänt lag"
    )


def parse_game_start(game: dict) -> datetime | None:
    """
    Tolka SHL:s starttid.

    Om SHL returnerar en tid utan timezone antar vi den timezone som
    är konfigurerad i SHL_TIMEZONE.
    """

    value = (
        game.get("rawStartDateTime")
        or game.get("startDateTime")
    )

    if not value:
        return None

    value = str(value).strip()

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)

    return parsed.astimezone(LOCAL_TZ)


def game_sort_key(game: dict) -> datetime:
    """
    Sorteringsnyckel för matcher.
    """

    start = parse_game_start(game)

    if start is not None:
        return start

    return datetime.max.replace(tzinfo=LOCAL_TZ)



def slugify_team_name(name: str) -> str:
    """
    Skapa ett stabilt filnamn från ett lagnamn.
    """

    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_name).strip("-").lower()

    return slug or "team"


def load_logo_index() -> dict:
    """
    Läs lokal mapping mellan lagnamn och logofil.
    """

    if not LOGO_INDEX_FILE.exists():
        return {}

    try:
        with LOGO_INDEX_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def save_logo_index(index: dict) -> None:
    """
    Skriv logoindex atomärt.
    """

    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = LOGO_INDEX_FILE.with_suffix(".json.tmp")

    with tmp_file.open("w", encoding="utf-8") as file:
        json.dump(index, file, ensure_ascii=False, indent=2)

    tmp_file.replace(LOGO_INDEX_FILE)


def local_logo_url(team: str) -> str | None:
    """
    Returnera publik HTTPS-URL till lokalt cachad laglogga.
    """

    index = load_logo_index()
    filename = index.get(team)

    if not filename:
        return None

    path = LOGO_DIR / filename

    if not path.exists():
        return None

    return f"{PUBLIC_BASE_URL}/logos/{filename}"


def discover_team_logo_sources(expected_teams: set[str]) -> dict:
    """
    Bygg käll-URL:er direkt från Sportality-sluggar.

    Mappingen är explicit eftersom SHL:s tabellsida renderas klient-side
    och därför inte är lämplig att skrapa server-side.
    """

    return {
        team: f"{SHL_LOGO_BASE_URL}/{TEAM_LOGO_SLUGS[team]}.svg"
        for team in expected_teams
        if team in TEAM_LOGO_SLUGS
    }


def validate_svg(response: requests.Response, source_url: str) -> None:
    """
    Kontrollera att svaret ser ut som en SVG-bild.
    """

    content_type = (
        response.headers.get("Content-Type", "")
        .split(";")[0]
        .strip()
        .lower()
    )

    body_start = response.content.lstrip()[:512].lower()

    if (
        content_type not in {
            "image/svg+xml",
            "application/svg+xml",
            "text/xml",
            "application/xml",
        }
        and b"<svg" not in body_start
    ):
        raise RuntimeError(
            f"Ogiltigt loggosvar från {source_url}: "
            f"{content_type or 'Content-Type saknas'}"
        )


def refresh_team_logos(games: list, force: bool = False) -> dict:
    """
    Ladda ned saknade lagloggor till lokal runtime-cache.

    Befintliga filer återanvänds om force=False.
    """

    expected_teams = {
        team_name(game.get(side) or {})
        for game in games
        for side in ("homeTeamInfo", "awayTeamInfo")
    }
    expected_teams.discard("Okänt lag")

    index = load_logo_index()

    needed = {
        team
        for team in expected_teams
        if (
            force
            or not index.get(team)
            or not (LOGO_DIR / index[team]).exists()
        )
    }

    if not needed:
        return {
            "status": "ok",
            "teams": len(expected_teams),
            "downloaded": 0,
            "missing": [],
        }

    sources = discover_team_logo_sources(expected_teams)

    downloaded = 0
    missing = []

    for team in sorted(needed):
        source_url = sources.get(team)

        if not source_url:
            missing.append(team)
            continue

        try:
            response = requests.get(
                source_url,
                headers={"User-Agent": SHL_USER_AGENT},
                timeout=SHL_REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            validate_svg(response, source_url)

            filename = f"{slugify_team_name(team)}.svg"
            target = LOGO_DIR / filename
            tmp_file = target.with_suffix(target.suffix + ".tmp")

            if len(response.content) > 2 * 1024 * 1024:
                raise RuntimeError("bildfilen är större än 2 MiB")

            with tmp_file.open("wb") as file:
                file.write(response.content)

            tmp_file.replace(target)

            old_filename = index.get(team)

            if old_filename and old_filename != filename:
                try:
                    (LOGO_DIR / old_filename).unlink(missing_ok=True)
                except OSError:
                    pass

            index[team] = filename
            downloaded += 1

        except (requests.RequestException, OSError, RuntimeError):
            missing.append(team)

    save_logo_index(index)

    return {
        "status": "ok" if not missing else "partial",
        "teams": len(expected_teams),
        "downloaded": downloaded,
        "missing": sorted(set(missing)),
    }


def serialize_game(game: dict) -> dict:
    """
    Returnera en förenklad representation av en SHL-match.
    """

    start = parse_game_start(game)
    venue = game.get("venueInfo") or {}

    home = team_name(game.get("homeTeamInfo") or {})
    away = team_name(game.get("awayTeamInfo") or {})

    result = {
        "id": game.get("uuid"),
        "round": game.get("roundNumber"),
        "round_label": game.get("roundLabel"),
        "start": start.isoformat() if start else None,
        "date": start.strftime("%Y-%m-%d") if start else None,
        "time": start.strftime("%H:%M") if start else None,
        "state": game.get("state"),
        "home": home,
        "home_logo": local_logo_url(home),
        "away": away,
        "away_logo": local_logo_url(away),
        "venue": venue.get("name"),
    }

    home_info = game.get("homeTeamInfo") or {}
    away_info = game.get("awayTeamInfo") or {}

    if isinstance(home_info.get("score"), (int, float)):
        result["home_score"] = home_info["score"]

    if isinstance(away_info.get("score"), (int, float)):
        result["away_score"] = away_info["score"]

    return result


def is_upcoming(game: dict) -> bool:
    """
    Kontrollera om en match är framtida och ännu inte spelad.
    """

    start = parse_game_start(game)

    if start is None:
        return False

    now = datetime.now(LOCAL_TZ)

    return (
        start > now
        and str(game.get("state", "")).lower() == "pre-game"
    )


# ============================================================
# API endpoints
# ============================================================

@app.get("/")
def root():
    return {
        "service": "SHL API",
        "version": "1.1.1",
        "status": "ok",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
    }


@app.get("/config")
def config():
    """
    Visa aktiv, icke-hemlig konfiguration för felsökning.
    """

    return {
        "base_dir": str(BASE_DIR),
        "data_dir": str(DATA_DIR),
        "cache_file": str(CACHE_FILE),
        "shl_url": SHL_URL,
        "season_uuid": SHL_SEASON_UUID,
        "series_uuid": SHL_SERIES_UUID,
        "game_type_uuid": SHL_GAME_TYPE_UUID,
        "game_place": SHL_GAME_PLACE,
        "played": SHL_PLAYED,
        "timezone": TIMEZONE_NAME,
        "minimum_games": SHL_MIN_GAMES,
        "request_timeout": SHL_REQUEST_TIMEOUT,
        "logo_base_url": SHL_LOGO_BASE_URL,
        "logo_dir": str(LOGO_DIR),
        "public_base_url": PUBLIC_BASE_URL,
        "api_root_path": API_ROOT_PATH,
    }


@app.get("/status")
def status():
    """
    Status för den lokala cachen.
    """

    data = load_schedule()
    games = get_games(data)

    stat = CACHE_FILE.stat()

    modified = datetime.fromtimestamp(
        stat.st_mtime,
        tz=LOCAL_TZ,
    )

    rounds = {
        game.get("roundNumber")
        for game in games
        if game.get("roundNumber") is not None
    }

    upcoming_games = [
        game
        for game in games
        if is_upcoming(game)
    ]

    return {
        "status": "ok",
        "games": len(games),
        "rounds": len(rounds),
        "upcoming_games": len(upcoming_games),
        "last_updated": modified.isoformat(),
        "cache_file": str(CACHE_FILE),
    }


@app.get("/next-round")
def next_round():
    """
    Returnera den omgång som den närmaste framtida matchen tillhör.

    Observera att uppskjutna matcher kan göra att en omgång innehåller
    matcher på vitt skilda datum. För en tittarvänlig lista bör /matches
    användas i stället.
    """

    data = load_schedule()
    games = get_games(data)

    upcoming_games = [
        game
        for game in games
        if is_upcoming(game)
    ]

    if not upcoming_games:
        raise HTTPException(
            status_code=404,
            detail="Inga kommande SHL-matcher hittades.",
        )

    upcoming_games.sort(key=game_sort_key)

    next_game = upcoming_games[0]
    round_number = next_game.get("roundNumber")

    if round_number is None:
        raise HTTPException(
            status_code=500,
            detail="Nästa match saknar roundNumber.",
        )

    round_games = [
        game
        for game in games
        if game.get("roundNumber") == round_number
    ]

    round_games.sort(key=game_sort_key)

    return {
        "round": round_number,
        "round_label": next_game.get("roundLabel"),
        "matches": [
            serialize_game(game)
            for game in round_games
        ],
    }


@app.get("/round/{round_number}")
def round_by_number(round_number: int):
    """
    Returnera samtliga matcher i en viss SHL-omgång.
    """

    data = load_schedule()
    games = get_games(data)

    round_games = [
        game
        for game in games
        if game.get("roundNumber") == round_number
    ]

    if not round_games:
        raise HTTPException(
            status_code=404,
            detail=f"Omgång {round_number} hittades inte.",
        )

    round_games.sort(key=game_sort_key)

    return {
        "round": round_number,
        "matches": [
            serialize_game(game)
            for game in round_games
        ],
    }


@app.get("/upcoming")
def upcoming(
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximalt antal kommande matcher",
    ),
):
    """
    Returnera de närmaste kommande matcherna sorterade på starttid.
    """

    data = load_schedule()
    games = get_games(data)

    upcoming_games = [
        game
        for game in games
        if is_upcoming(game)
    ]

    upcoming_games.sort(key=game_sort_key)

    selected = upcoming_games[:limit]

    return {
        "count": len(selected),
        "matches": [
            serialize_game(game)
            for game in selected
        ],
    }


@app.get("/next-matchday")
def next_matchday():
    """
    Returnera samtliga ännu ospelade matcher på nästa kalenderdag
    som har SHL-matcher.

    Exempel:
    Om nästa framtida match spelas på torsdag returneras alla
    pre-game-matcher på torsdagen, men inga matcher från senare dagar.

    Urvalet baseras på faktisk lokal starttid och påverkas inte av
    omgångsnummer.
    """

    data = load_schedule()
    games = get_games(data)

    upcoming_games = [
        game
        for game in games
        if is_upcoming(game)
    ]

    if not upcoming_games:
        raise HTTPException(
            status_code=404,
            detail="Inga kommande SHL-matcher hittades.",
        )

    upcoming_games.sort(key=game_sort_key)

    first_start = parse_game_start(upcoming_games[0])

    if first_start is None:
        raise HTTPException(
            status_code=500,
            detail="Kunde inte läsa starttiden för nästa match.",
        )

    match_date = first_start.date()

    matchday_games = [
        game
        for game in upcoming_games
        if (
            parse_game_start(game) is not None
            and parse_game_start(game).date() == match_date
        )
    ]

    matchday_games.sort(key=game_sort_key)

    return {
        "date": match_date.isoformat(),
        "count": len(matchday_games),
        "matches": [
            serialize_game(game)
            for game in matchday_games
        ],
    }


@app.get("/matches")
def matches_within_days(
    days: int = Query(
        default=7,
        ge=1,
        le=60,
        description="Antal dagar framåt som matcher ska hämtas",
    ),
):
    """
    Returnera alla ännu ospelade SHL-matcher från nu och angivet
    antal dagar framåt, oberoende av omgång.
    """

    data = load_schedule()
    games = get_games(data)

    now = datetime.now(LOCAL_TZ)
    end_time = now + timedelta(days=days)

    selected_games = []

    for game in games:
        start = parse_game_start(game)

        if start is None:
            continue

        if (
            now <= start <= end_time
            and str(game.get("state", "")).lower() == "pre-game"
        ):
            selected_games.append(game)

    selected_games.sort(key=game_sort_key)

    return {
        "days": days,
        "from": now.isoformat(),
        "to": end_time.isoformat(),
        "count": len(selected_games),
        "matches": [
            serialize_game(game)
            for game in selected_games
        ],
    }


@app.get("/teams")
def teams():
    """
    Visa aktuella lag och lokalt cachade logo-URL:er.
    """

    data = load_schedule()
    games = get_games(data)

    names = sorted({
        team_name(game.get(side) or {})
        for game in games
        for side in ("homeTeamInfo", "awayTeamInfo")
    })

    return {
        "count": len(names),
        "teams": [
            {
                "name": name,
                "logo": local_logo_url(name),
            }
            for name in names
            if name != "Okänt lag"
        ],
    }


@app.post("/refresh-logos")
def refresh_logos(
    force: bool = Query(
        default=False,
        description="Ladda om även loggor som redan finns lokalt",
    ),
):
    """
    Hämta lagloggor från Sportality-CDN och lagra dem lokalt.
    """

    data = load_schedule()
    games = get_games(data)

    try:
        return refresh_team_logos(games, force=force)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.post("/refresh")
def refresh():
    """
    Hämta hela SHL-schemat från shl.se och uppdatera cachen.

    Den gamla cachen behålls om hämtning eller validering misslyckas.
    """

    params = {
        "seasonUuid": SHL_SEASON_UUID,
        "seriesUuid": SHL_SERIES_UUID,
        "gameTypeUuid": SHL_GAME_TYPE_UUID,
        "gamePlace": SHL_GAME_PLACE,
        "played": SHL_PLAYED,
    }

    headers = {
        "Accept": "application/json",
        "User-Agent": SHL_USER_AGENT,
    }

    try:
        response = requests.get(
            SHL_URL,
            params=params,
            headers=headers,
            timeout=SHL_REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kunde inte hämta SHL-data: {exc}",
        )

    try:
        data = response.json()

    except requests.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"SHL returnerade ogiltig JSON: {exc}",
        )

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail="SHL returnerade ett oväntat JSON-format.",
        )

    games = data.get("gameInfo")

    if not isinstance(games, list):
        raise HTTPException(
            status_code=502,
            detail="SHL-svaret saknar gameInfo.",
        )

    if len(games) < SHL_MIN_GAMES:
        raise HTTPException(
            status_code=502,
            detail=(
                f"SHL returnerade endast {len(games)} matcher. "
                f"Minst {SHL_MIN_GAMES} förväntades. "
                "Den gamla cachen har behållits."
            ),
        )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    CACHE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp_file = CACHE_FILE.with_suffix(
        CACHE_FILE.suffix + ".tmp"
    )

    try:
        with tmp_file.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

        # Atomärt byte: den gamla filen ersätts först när den nya
        # är helt nedskriven och validerad.
        tmp_file.replace(CACHE_FILE)

    except OSError as exc:
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            pass

        raise HTTPException(
            status_code=500,
            detail=f"Kunde inte skriva SHL-cache: {exc}",
        )

    rounds = {
        game.get("roundNumber")
        for game in games
        if game.get("roundNumber") is not None
    }

    try:
        logo_refresh = refresh_team_logos(games, force=False)
    except RuntimeError as exc:
        logo_refresh = {
            "status": "warning",
            "detail": str(exc),
        }

    return {
        "status": "ok",
        "games": len(games),
        "rounds": len(rounds),
        "updated": datetime.now(LOCAL_TZ).isoformat(),
        "cache_file": str(CACHE_FILE),
        "logos": logo_refresh,
    }
