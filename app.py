import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query


# ============================================================
# Configuration
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

# Läs .env från samma katalog som app.py
load_dotenv(BASE_DIR / ".env")


def env_path(name: str, default: Path) -> Path:
    """
    Läs en sökväg från en miljövariabel.

    Relativa paths räknas från katalogen där app.py ligger.
    Exempel:
        SHL_DATA_DIR=./data
    blir:
        /home/user/Development/shl-api/data
    """
    value = os.getenv(name)

    if not value:
        return default.resolve()

    path = Path(value).expanduser()

    if not path.is_absolute():
        path = BASE_DIR / path

    return path.resolve()


SHL_URL = os.getenv(
    "SHL_URL",
    "https://www.shl.se/api/sports-v2/game-schedule",
)

SHL_SEASON_UUID = os.getenv(
    "SHL_SEASON_UUID",
    "ndcf81nlb3",
)

SHL_SERIES_UUID = os.getenv(
    "SHL_SERIES_UUID",
    "qQ9-bb0bzEWUk",
)

SHL_GAME_TYPE_UUID = os.getenv(
    "SHL_GAME_TYPE_UUID",
    "qQ9-af37Ti40B",
)

SHL_MIN_GAMES = int(
    os.getenv(
        "SHL_MIN_GAMES",
        "300",
    )
)

DATA_DIR = env_path(
    "SHL_DATA_DIR",
    BASE_DIR / "data",
)

CACHE_FILE = env_path(
    "SHL_CACHE_FILE",
    DATA_DIR / "schedule.json",
)

TIMEZONE_NAME = os.getenv(
    "SHL_TIMEZONE",
    "Europe/Stockholm",
)

LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="SHL API",
    description="Lokalt cache-API för SHL:s spelschema",
    version="1.0.0",
    root_path="/api/shl",
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

    Om SHL returnerar en tid utan timezone antar vi Europe/Stockholm.
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


def serialize_game(game: dict) -> dict:
    """
    Returnera en förenklad representation av en SHL-match.
    """

    start = parse_game_start(game)

    venue = game.get("venueInfo") or {}

    result = {
        "id": game.get("uuid"),
        "round": game.get("roundNumber"),
        "round_label": game.get("roundLabel"),
        "start": start.isoformat() if start else None,
        "date": start.strftime("%Y-%m-%d") if start else None,
        "time": start.strftime("%H:%M") if start else None,
        "state": game.get("state"),
        "home": team_name(game.get("homeTeamInfo") or {}),
        "away": team_name(game.get("awayTeamInfo") or {}),
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
        "version": "1.0.0",
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
    Visa aktiv konfiguration.

    Inga credentials finns i denna tjänst, så informationen är
    avsiktligt tillgänglig för felsökning på det interna nätet.
    """

    return {
        "base_dir": str(BASE_DIR),
        "data_dir": str(DATA_DIR),
        "cache_file": str(CACHE_FILE),
        "shl_url": SHL_URL,
        "season_uuid": SHL_SEASON_UUID,
        "series_uuid": SHL_SERIES_UUID,
        "game_type_uuid": SHL_GAME_TYPE_UUID,
        "timezone": TIMEZONE_NAME,
        "minimum_games": SHL_MIN_GAMES,
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
    Returnera nästa SHL-omgång.

    Algoritm:

    1. Hitta första framtida pre-game-match.
    2. Läs matchens roundNumber.
    3. Returnera samtliga matcher som tillhör den omgången.

    Detta är viktigt eftersom uppskjutna matcher från tidigare
    omgångar annars skulle kunna göra att fel omgång väljs.
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
    ),
):
    """
    Returnera kommande matcher sorterade på starttid.
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
        "gamePlace": "all",
        "played": "all",
    }

    headers = {
        "Accept": "application/json",
        "User-Agent": "shl-api/1.0",
    }

    try:
        response = requests.get(
            SHL_URL,
            params=params,
            headers=headers,
            timeout=30,
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

        # Atomärt byte:
        # den gamla filen försvinner först när den nya är helt skriven.
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

    return {
        "status": "ok",
        "games": len(games),
        "rounds": len(rounds),
        "updated": datetime.now(LOCAL_TZ).isoformat(),
        "cache_file": str(CACHE_FILE),
    }

