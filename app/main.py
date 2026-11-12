import mimetypes
import os
import random
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import connect, get_connection, initialize
from .schemas import (
    AttemptCreate,
    DictionarySessionCreate,
    DictionaryAttemptCreate,
    DictionaryWordCreate,
    DictionaryWordUpdate,
    HistoryReviewCreate,
    MaterialCreate,
    MediaScanCreate,
    SessionCreate,
    SearchAdaptCreate,
    SearchCreate,
    SearchSessionCreate,
)
from .subtitles import is_study_worthy, parse_subtitles
from .search import (
    adapt_search_topic,
    search_phrases as find_search_phrases,
    search_tokens,
)
from .translation import TranslationError, translate_to_russian


LANGUAGES = {"de": "Deutsch", "el": "Ελληνικά"}
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media"))
VIDEO_SUFFIXES = {".mp4", ".webm", ".ogg", ".m4v", ".mkv", ".avi"}
SUBTITLE_SUFFIXES = {".srt", ".xml", ".ttml", ".dfxp"}
PROCESSING_VERSION = 4
@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    yield


app = FastAPI(title="Real Language Learner", lifespan=lifespan)
app.mount("/media", StaticFiles(directory=MEDIA_DIR, check_dir=False), name="media")


def normalize(text: str) -> str:
    text = text.lower().strip()
    return re.sub(r"\s+", " ", text)


def get_or_create_translation(
    phrase: sqlite3.Row, connection: sqlite3.Connection
) -> str:
    phrase_id = phrase["phrase_id"] if "phrase_id" in phrase.keys() else phrase["id"]
    cached = connection.execute(
        "SELECT text FROM translations WHERE phrase_id = ?", (phrase_id,)
    ).fetchone()
    if cached:
        return cached["text"]
    source_language = "de" if phrase["language"] == "de" else "el"
    translated = translate_to_russian(phrase["text"], source_language)
    connection.execute(
        "INSERT INTO translations (phrase_id, text) VALUES (?, ?)",
        (phrase_id, translated),
    )
    connection.commit()
    return translated


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


_TRANSLATION_DISTRACTORS = (
    "вчера",
    "возможно",
    "быстро",
    "важный",
    "вместе",
    "город",
    "действительно",
    "здесь",
    "известный",
    "каждый",
    "книга",
    "место",
    "новый",
    "потом",
    "почему",
    "просто",
    "сегодня",
    "снова",
    "теперь",
    "хороший",
)


def translation_words(text: str, phrase_id: int) -> list[str]:
    words = re.findall(r"\S+", text)
    if not words:
        return []
    existing = {word.lower() for word in words}
    candidates = [
        word for word in _TRANSLATION_DISTRACTORS if word.lower() not in existing
    ]
    randomizer = random.Random(phrase_id)
    randomizer.shuffle(candidates)
    result = words + candidates[: max(3, min(5, len(words)))]
    randomizer.shuffle(result)
    return result


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/training")
def training() -> FileResponse:
    return FileResponse("app/static/training.html")


@app.get("/history")
def history_page() -> FileResponse:
    return FileResponse("app/static/history.html")


@app.get("/history-review")
def history_review_page() -> FileResponse:
    return FileResponse("app/static/history-review.html")


@app.get("/theme.css")
def theme_css() -> FileResponse:
    return FileResponse("app/static/theme.css", media_type="text/css")


@app.get("/theme.js")
def theme_js() -> FileResponse:
    return FileResponse("app/static/theme.js", media_type="application/javascript")


@app.get("/dictionary")
def dictionary_page() -> FileResponse:
    return FileResponse("app/static/dictionary.html")


@app.get("/dictionary/training")
def dictionary_training_page() -> FileResponse:
    return FileResponse("app/static/dictionary-training.html")


@app.get("/api/languages")
def languages() -> dict[str, str]:
    return LANGUAGES


@app.post("/api/search/adapt")
def search_adapt(payload: SearchAdaptCreate) -> dict[str, list[str]]:
    return {"keywords": adapt_search_topic(payload)}


@app.post("/api/search")
def search_phrases(
    payload: SearchCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return find_search_phrases(payload, connection)


@app.post("/api/search/sessions")
def create_search_session(
    payload: SearchSessionCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    placeholders = ", ".join("?" for _ in payload.phrase_ids)
    phrases = connection.execute(
        f"SELECT id FROM phrases WHERE language = ? AND id IN ({placeholders})",
        [payload.language, *payload.phrase_ids],
    ).fetchall()
    phrase_ids = list(dict.fromkeys(row["id"] for row in phrases))
    if not phrase_ids:
        raise HTTPException(status_code=400, detail="По найденным фразам нечего тренировать.")
    cursor = connection.execute(
        """
        INSERT INTO training_sessions
            (language, listening_count, translation_count, listening_length)
        VALUES (?, ?, ?, ?)
        """,
        (
            payload.language,
            payload.listening_count,
            payload.translation_count,
            payload.listening_length,
        ),
    )
    session_id = cursor.lastrowid
    connection.executemany(
        "INSERT INTO session_candidates (session_id, phrase_id) VALUES (?, ?)",
        [(session_id, phrase_id) for phrase_id in phrase_ids],
    )
    connection.commit()
    return {"id": session_id, "language": payload.language, "count": len(phrase_ids)}


def dictionary_word(row: sqlite3.Row) -> dict:
    result = row_to_dict(row)
    result["selected"] = bool(result["selected"])
    return result


@app.get("/api/dictionary")
def get_dictionary(
    language: str, connection: sqlite3.Connection = Depends(get_connection)
) -> list[dict]:
    if language not in LANGUAGES:
        raise HTTPException(status_code=400, detail="Неподдерживаемый язык.")
    rows = connection.execute(
        "SELECT * FROM dictionary_words WHERE language = ? ORDER BY id",
        (language,),
    ).fetchall()
    return [dictionary_word(row) for row in rows]


@app.post("/api/dictionary")
def add_dictionary_word(
    payload: DictionaryWordCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    word = payload.word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="Слово не может быть пустым.")
    try:
        translation = translate_to_russian(
            word, "de" if payload.language == "de" else "el"
        )
    except TranslationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    connection.execute(
        """
        INSERT INTO dictionary_words (language, word, translation)
        VALUES (?, ?, ?)
        ON CONFLICT(language, word) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        """,
        (payload.language, word, translation),
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM dictionary_words WHERE language = ? AND word = ?",
        (payload.language, word),
    ).fetchone()
    return dictionary_word(row)


@app.patch("/api/dictionary/{word_id}")
def update_dictionary_word(
    word_id: int,
    payload: DictionaryWordUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    updates = ["translation = ?", "updated_at = CURRENT_TIMESTAMP"]
    params: list[str | int] = [payload.translation.strip()]
    if payload.selected is not None:
        updates.append("selected = ?")
        params.append(int(payload.selected))
    params.append(word_id)
    cursor = connection.execute(
        f"UPDATE dictionary_words SET {', '.join(updates)} WHERE id = ?", params
    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Слово не найдено.")
    connection.commit()
    return dictionary_word(
        connection.execute(
            "SELECT * FROM dictionary_words WHERE id = ?", (word_id,)
        ).fetchone()
    )


@app.delete("/api/dictionary/{word_id}")
def delete_dictionary_word(
    word_id: int, connection: sqlite3.Connection = Depends(get_connection)
) -> dict[str, bool]:
    connection.execute(
        "DELETE FROM dictionary_attempts WHERE word_id = ?", (word_id,)
    )
    connection.execute(
        "DELETE FROM dictionary_session_items WHERE word_id = ?", (word_id,)
    )
    cursor = connection.execute("DELETE FROM dictionary_words WHERE id = ?", (word_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Слово не найдено.")
    connection.commit()
    return {"deleted": True}


@app.post("/api/dictionary/sessions")
def create_dictionary_session(
    payload: DictionarySessionCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    available = connection.execute(
        "SELECT COUNT(*) FROM dictionary_words WHERE language = ? AND selected = 1",
        (payload.language,),
    ).fetchone()[0]
    if available == 0 or (
        payload.to_russian_count == 0 and payload.from_russian_count == 0
    ):
        raise HTTPException(status_code=400, detail="Выбери слова и количество заданий.")
    cursor = connection.execute(
        """
        INSERT INTO dictionary_sessions
            (language, to_russian_count, from_russian_count)
        VALUES (?, ?, ?)
        """,
        (payload.language, payload.to_russian_count, payload.from_russian_count),
    )
    connection.commit()
    return {"id": cursor.lastrowid, "language": payload.language}


def dictionary_target_count(
    session: sqlite3.Row, direction: str
) -> int:
    return (
        session["to_russian_count"]
        if direction == "to_russian"
        else session["from_russian_count"]
    )


@app.get("/api/dictionary/sessions/{session_id}/exercise")
def next_dictionary_exercise(
    session_id: int, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    session = connection.execute(
        "SELECT * FROM dictionary_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Словарная тренировка не найдена.")
    is_review = False
    position = 0
    total = 0
    for direction in ("to_russian", "from_russian"):
        issued = connection.execute(
            """
            SELECT COUNT(*) FROM dictionary_session_items
            WHERE session_id = ? AND direction = ?
            """,
            (session_id, direction),
        ).fetchone()[0]
        if issued >= dictionary_target_count(session, direction):
            continue
        position = issued + 1
        total = dictionary_target_count(session, direction)
        word = connection.execute(
            """
            SELECT * FROM dictionary_words
            WHERE language = ? AND selected = 1
              AND NOT EXISTS (
                SELECT 1 FROM dictionary_session_items i
                WHERE i.session_id = ? AND i.word_id = dictionary_words.id
                  AND i.direction = ?
              )
            ORDER BY RANDOM() LIMIT 1
            """,
            (session["language"], session_id, direction),
        ).fetchone()
        if word is None:
            continue
        connection.execute(
            """
            INSERT INTO dictionary_session_items (session_id, word_id, direction)
            VALUES (?, ?, ?)
            """,
            (session_id, word["id"], direction),
        )
        connection.commit()
        break
    else:
        word = None

    if word is None:
        is_review = True
        word = connection.execute(
            """
            SELECT w.*, i.direction
            FROM dictionary_words w
            JOIN dictionary_session_items i ON i.word_id = w.id
            WHERE i.session_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM dictionary_attempts a
                WHERE a.session_id = i.session_id AND a.word_id = i.word_id
                  AND a.direction = i.direction AND a.is_correct = 1
              )
            ORDER BY RANDOM() LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if word is not None:
            connection.execute(
                "UPDATE dictionary_sessions SET review_count = review_count + 1 WHERE id = ?",
                (session_id,),
            )
            connection.commit()
            position = session["review_count"] + 1
    if word is None:
        raise HTTPException(status_code=404, detail="Словарная тренировка завершена.")
    direction = word["direction"] if "direction" in word.keys() else direction
    return {
        "id": word["id"],
        "word": word["word"] if direction == "to_russian" else word["translation"],
        "direction": direction,
        "is_review": is_review,
        "number": position,
        "total": total,
        "prompt": "Переведи на русский." if direction == "to_russian" else "Переведи на изучаемый язык.",
    }


@app.post("/api/dictionary/sessions/{session_id}/attempt")
def submit_dictionary_attempt(
    session_id: int,
    payload: DictionaryAttemptCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    item = connection.execute(
        """
        SELECT 1 FROM dictionary_session_items
        WHERE session_id = ? AND word_id = ? AND direction = ?
        """,
        (session_id, payload.word_id, payload.direction),
    ).fetchone()
    if item is None:
        raise HTTPException(status_code=400, detail="Это слово не входит в тренировку.")
    word = connection.execute(
        "SELECT * FROM dictionary_words WHERE id = ?", (payload.word_id,)
    ).fetchone()
    if word is None:
        raise HTTPException(status_code=404, detail="Слово не найдено.")
    expected = (
        word["translation"] if payload.direction == "to_russian" else word["word"]
    )
    correct = normalize(payload.answer) == normalize(expected)
    connection.execute(
        """
        INSERT INTO dictionary_attempts
            (session_id, word_id, direction, answer, is_correct)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, word["id"], payload.direction, payload.answer, int(correct)),
    )
    connection.commit()
    return {
        "correct": correct,
        "answer": expected,
        "word": word["word"],
        "translation": word["translation"],
        "message": "Верно!" if correct else "Почти. Попробуй ещё раз.",
    }


@app.post("/api/sessions")
def create_session(
    payload: SessionCreate, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    cursor = connection.execute(
        """
        INSERT INTO training_sessions
            (language, listening_count, translation_count, listening_length)
        VALUES (?, ?, ?, ?)
        """,
        (
            payload.language,
            payload.listening_count,
            payload.translation_count,
            payload.listening_length,
        ),
    )
    connection.commit()
    return {"id": cursor.lastrowid, "language": payload.language}


@app.post("/api/materials")
def add_material(
    payload: MaterialCreate, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    subtitles = parse_subtitles(payload.srt_text, ".srt")
    if not subtitles:
        raise HTTPException(status_code=400, detail="Не удалось найти строки SRT.")

    subtitles = [subtitle for subtitle in subtitles if is_study_worthy(subtitle)]
    connection.executemany(
        """
        INSERT INTO phrases
            (language, source_title, video_path, text, start_time, end_time)
        VALUES (?, ?, NULL, ?, ?, ?)
        """,
        [
            (
                payload.language,
                payload.title,
                subtitle.text,
                subtitle.start_time,
                subtitle.end_time,
            )
            for subtitle in subtitles
        ],
    )
    connection.commit()
    return {"imported": len(subtitles), "title": payload.title}


def find_video(subtitle_path: Path) -> Path | None:
    for candidate in subtitle_path.parent.iterdir():
        if candidate.is_file() and candidate.stem == subtitle_path.stem:
            if candidate.suffix.lower() in VIDEO_SUFFIXES:
                return candidate
    return None


def _seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _format_seconds(value: float) -> str:
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"


def make_segments(subtitles: list) -> list[tuple[str, str, str, str]]:
    subtitles = [item for item in subtitles if is_study_worthy(item)]
    segments: list[tuple[str, str, str, str]] = []
    for index, item in enumerate(subtitles):
        segments.append(("translation", item.text, item.start_time, item.end_time))
    for start_index in range(len(subtitles)):
        start = subtitles[start_index]
        text_parts = [start.text]
        for end_index in range(start_index + 1, len(subtitles)):
            previous = subtitles[end_index - 1]
            current = subtitles[end_index]
            gap = _seconds(current.start_time) - _seconds(previous.end_time)
            candidate_text = " ".join(text_parts + [current.text])
            if gap > 1.5 or len(candidate_text) > 180:
                break
            if _seconds(current.end_time) - _seconds(start.start_time) > 15:
                break
            text_parts.append(current.text)
            if len(text_parts) >= 2:
                segments.append(
                    (
                        "listening",
                        " ".join(text_parts),
                        start.start_time,
                        current.end_time,
                    )
                )
    return segments


def delete_media_phrases(
    connection: sqlite3.Connection, phrase_ids: list[int]
) -> int:
    if not phrase_ids:
        return 0
    placeholders = ", ".join("?" for _ in phrase_ids)
    exercise_ids = [
        row["id"]
        for row in connection.execute(
            f"SELECT id FROM exercises WHERE phrase_id IN ({placeholders})",
            phrase_ids,
        ).fetchall()
    ]
    if exercise_ids:
        exercise_placeholders = ", ".join("?" for _ in exercise_ids)
        connection.execute(
            f"DELETE FROM history_review_attempts WHERE exercise_id IN ({exercise_placeholders})",
            exercise_ids,
        )
        connection.execute(
            f"DELETE FROM history_review_items WHERE exercise_id IN ({exercise_placeholders})",
            exercise_ids,
        )
        connection.execute(
            f"DELETE FROM attempts WHERE exercise_id IN ({exercise_placeholders})",
            exercise_ids,
        )
    connection.execute(
        f"DELETE FROM session_candidates WHERE phrase_id IN ({placeholders})",
        phrase_ids,
    )
    connection.execute(
        f"DELETE FROM session_items WHERE phrase_id IN ({placeholders})",
        phrase_ids,
    )
    connection.execute(
        f"DELETE FROM translations WHERE phrase_id IN ({placeholders})",
        phrase_ids,
    )
    connection.execute(
        f"DELETE FROM exercises WHERE phrase_id IN ({placeholders})",
        phrase_ids,
    )
    connection.execute(
        f"DELETE FROM phrases WHERE id IN ({placeholders})",
        phrase_ids,
    )
    return len(phrase_ids)


def scan_media(language: str) -> dict[str, int | str]:
    language_dir = MEDIA_DIR / language
    language_dir.mkdir(parents=True, exist_ok=True)
    subtitle_count = 0
    matched_count = 0
    imported_count = 0
    with connect() as connection:
        media_files: set[str] = set()
        for subtitle_path in language_dir.iterdir():
            if (
                subtitle_path.is_file()
                and subtitle_path.suffix.lower() in SUBTITLE_SUFFIXES
            ):
                video_path = find_video(subtitle_path)
                if video_path is not None:
                    media_files.add(f"/media/{language}/{video_path.name}")
        stale_ids = [
            row["id"]
            for row in connection.execute(
                """
                SELECT id, video_path FROM phrases
                WHERE language = ? AND video_path IS NOT NULL
                """,
                (language,),
            ).fetchall()
            if row["video_path"] not in media_files
        ]
        deleted_count = delete_media_phrases(connection, stale_ids)
        for subtitle_path in language_dir.iterdir():
            if not subtitle_path.is_file() or subtitle_path.suffix.lower() not in SUBTITLE_SUFFIXES:
                continue
            subtitle_count += 1
            video_path = find_video(subtitle_path)
            if video_path is None:
                continue
            matched_count += 1
            existing = connection.execute(
                """
                SELECT processing_version FROM phrases
                WHERE source_title = ? AND video_path = ? LIMIT 1
                """,
                (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
            ).fetchone()
            if existing and existing["processing_version"] == PROCESSING_VERSION:
                continue
            subtitles = parse_subtitles(
                subtitle_path.read_text(encoding="utf-8-sig"), subtitle_path.suffix
            )
            if existing:
                connection.execute(
                    """
                    DELETE FROM history_review_attempts
                    WHERE exercise_id IN (
                        SELECT e.id FROM exercises e
                        JOIN phrases p ON p.id = e.phrase_id
                        WHERE p.source_title = ? AND p.video_path = ?
                    )
                    """,
                    (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
                )
                connection.execute(
                    """
                    DELETE FROM history_review_items
                    WHERE exercise_id IN (
                        SELECT e.id FROM exercises e
                        JOIN phrases p ON p.id = e.phrase_id
                        WHERE p.source_title = ? AND p.video_path = ?
                    )
                    """,
                    (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
                )
                connection.execute(
                    """
                    DELETE FROM session_items
                    WHERE phrase_id IN (
                        SELECT id FROM phrases
                        WHERE source_title = ? AND video_path = ?
                    )
                    """,
                    (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
                )
                connection.execute(
                    """
                    DELETE FROM session_candidates
                    WHERE phrase_id IN (
                        SELECT id FROM phrases
                        WHERE source_title = ? AND video_path = ?
                    )
                    """,
                    (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
                )
                connection.execute(
                    """
                    DELETE FROM translations
                    WHERE phrase_id IN (
                        SELECT id FROM phrases
                        WHERE source_title = ? AND video_path = ?
                    )
                    """,
                    (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
                )
                connection.execute(
                    """
                    DELETE FROM attempts WHERE exercise_id IN (
                        SELECT e.id FROM exercises e JOIN phrases p ON p.id = e.phrase_id
                        WHERE p.source_title = ? AND p.video_path = ?
                    )
                    """,
                    (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
                )
                connection.execute(
                    """
                    DELETE FROM exercises WHERE phrase_id IN (
                        SELECT id FROM phrases WHERE source_title = ? AND video_path = ?
                    )
                    """,
                    (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
                )
                connection.execute(
                    "DELETE FROM phrases WHERE source_title = ? AND video_path = ?",
                    (subtitle_path.stem, f"/media/{language}/{video_path.name}"),
                )
            segments = make_segments(subtitles)
            connection.executemany(
                """
                INSERT INTO phrases
                    (language, source_title, video_path, text, start_time, end_time,
                     segment_kind, duration_seconds, processing_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        language,
                        subtitle_path.stem,
                        f"/media/{language}/{video_path.name}",
                        text,
                        start,
                        end,
                        kind,
                        _seconds(end) - _seconds(start),
                        PROCESSING_VERSION,
                    )
                    for kind, text, start, end in segments
                ],
            )
            imported_count += len(segments)
        phrase_texts = connection.execute(
            "SELECT text FROM phrases WHERE language = ?", (language,)
        ).fetchall()
        all_words = [
            word
            for row in phrase_texts
            for word in search_tokens(row["text"])
        ]
        connection.commit()
    return {
        "folder": str(language_dir),
        "subtitle_files": subtitle_count,
        "matched_videos": matched_count,
        "imported_phrases": imported_count,
        "deleted_phrases": deleted_count,
        "total_phrases": len(phrase_texts),
        "total_words": len(all_words),
        "unique_words": len(set(all_words)),
    }


@app.post("/api/media/scan")
def rescan_media(
    payload: MediaScanCreate,
) -> dict[str, int | str]:
    result = scan_media(payload.language)
    return {"imported": int(result["imported_phrases"]), **result}


@app.post("/api/history-reviews")
def create_history_review(
    payload: HistoryReviewCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    failed = connection.execute(
        """
        SELECT DISTINCT e.id
        FROM attempts a
        JOIN exercises e ON e.id = a.exercise_id
        JOIN phrases p ON p.id = e.phrase_id
        WHERE a.is_correct = 0 AND p.language = ?
        ORDER BY a.created_at DESC
        LIMIT ?
        """,
        (payload.language, payload.history_limit),
    ).fetchall()
    if len(failed) < payload.exercise_count:
        raise HTTPException(
            status_code=400,
            detail=f"Недостаточно неудачных упражнений: доступно {len(failed)}.",
        )
    selected = random.sample(failed, payload.exercise_count)
    cursor = connection.execute(
        """
        INSERT INTO history_review_sessions
            (language, history_limit, requested_count)
        VALUES (?, ?, ?)
        """,
        (payload.language, payload.history_limit, payload.exercise_count),
    )
    session_id = cursor.lastrowid
    connection.executemany(
        """
        INSERT INTO history_review_items (session_id, exercise_id, position)
        VALUES (?, ?, ?)
        """,
        [
            (session_id, row["id"], position)
            for position, row in enumerate(selected, start=1)
        ],
    )
    connection.commit()
    return {"id": session_id, "count": payload.exercise_count}


@app.get("/api/history-reviews/{session_id}/exercise")
def next_history_review_exercise(
    session_id: int, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    item = connection.execute(
        """
        SELECT i.position, e.id, e.kind, p.id AS phrase_id, p.text,
               p.source_title, p.language, p.video_path, p.start_time, p.end_time
        FROM history_review_items i
        JOIN exercises e ON e.id = i.exercise_id
        JOIN phrases p ON p.id = e.phrase_id
        WHERE i.session_id = ? AND i.completed = 0
        ORDER BY CASE WHEN EXISTS (
            SELECT 1 FROM history_review_attempts a
            WHERE a.session_id = i.session_id
              AND a.exercise_id = i.exercise_id
        ) THEN 1 ELSE 0 END, i.position
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if item is None:
        raise HTTPException(status_code=404, detail="Закрепление завершено.")
    translation = get_or_create_translation(item, connection)
    words = item["text"].split()
    shuffled = words[:]
    random.Random(item["phrase_id"]).shuffle(shuffled)
    return {
        "id": item["id"],
        "kind": item["kind"],
        "phrase_id": item["phrase_id"],
        "language": item["language"],
        "source_title": item["source_title"],
        "video_path": item["video_path"],
        "time": f"{item['start_time']} - {item['end_time']}",
        "words": shuffled,
        "translation_words": translation_words(translation, item["phrase_id"])
        if item["kind"] == "translation"
        else [],
        "phrase_text": item["text"],
        "translation": translation,
        "is_review": True,
        "number": item["position"],
        "total": connection.execute(
            "SELECT requested_count FROM history_review_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()["requested_count"],
        "prompt": (
            "Послушай фрагмент и восстанови услышанную фразу."
            if item["kind"] == "listening"
            else "Переведи эту фразу на русский."
        ),
    }


@app.post("/api/history-reviews/{session_id}/attempt")
def submit_history_review_attempt(
    session_id: int,
    exercise_id: int,
    payload: AttemptCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    exercise = connection.execute(
        """
        SELECT e.id, e.kind, p.id AS phrase_id, p.text, p.language
        FROM history_review_items i
        JOIN exercises e ON e.id = i.exercise_id
        JOIN phrases p ON p.id = e.phrase_id
        WHERE i.session_id = ? AND e.id = ? AND i.completed = 0
        """,
        (session_id, exercise_id),
    ).fetchone()
    if exercise is None:
        raise HTTPException(status_code=404, detail="Упражнение не найдено.")
    expected = (
        get_or_create_translation(exercise, connection)
        if exercise["kind"] == "translation"
        else exercise["text"]
    )
    correct = normalize(payload.answer) == normalize(expected)
    connection.execute(
        """
        INSERT INTO history_review_attempts
            (session_id, exercise_id, answer, is_correct)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, exercise_id, payload.answer, int(correct)),
    )
    if correct:
        connection.execute(
            """
            UPDATE history_review_items SET completed = 1
            WHERE session_id = ? AND exercise_id = ?
            """,
            (session_id, exercise_id),
        )
    connection.commit()
    return {
        "correct": correct,
        "answer": expected,
        "original": exercise["text"],
        "message": "Верно!" if correct else "Почти. Попробуй ещё раз.",
    }


def count_phrases() -> int:
    with connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM phrases").fetchone()[0]


@app.get("/api/sessions/{session_id}/exercise")
def next_exercise(
    session_id: int,
    exclude_phrase_ids: list[int] | None = Query(default=None),
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    session = connection.execute(
        "SELECT * FROM training_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")

    issued = connection.execute(
        """
        SELECT kind, COUNT(*) AS count
        FROM session_items
        WHERE session_id = ?
        GROUP BY kind
        """,
        (session_id,),
    ).fetchall()
    issued_by_kind = {row["kind"]: row["count"] for row in issued}
    targets = {
        "listening": session["listening_count"],
        "translation": session["translation_count"],
    }
    review_query = """
        SELECT si.phrase_id, si.kind
        FROM phrases p
        JOIN session_items si ON si.phrase_id = p.id AND si.session_id = ?
        JOIN exercises e ON e.phrase_id = p.id AND e.kind = si.kind
        WHERE p.language = ?
          AND EXISTS (
              SELECT 1 FROM attempts wrong
              WHERE wrong.exercise_id = e.id AND wrong.is_correct = 0
          )
          AND NOT EXISTS (
              SELECT 1 FROM attempts correct
              WHERE correct.exercise_id = e.id AND correct.is_correct = 1
          )
        ORDER BY RANDOM()
        LIMIT 1
    """
    remaining_kinds = [
        kind
        for kind in ("listening", "translation")
        if targets[kind] > issued_by_kind.get(kind, 0)
    ]
    preferred_kind = random.choice(remaining_kinds) if remaining_kinds else None
    review_mode = preferred_kind is None
    review_number = 0
    if review_mode:
        review = connection.execute(
            review_query, (session_id, session["language"])
        ).fetchone()
        if review is None:
            raise HTTPException(status_code=404, detail="Тренировка завершена.")
        preferred_kind = review["kind"]
        connection.execute(
            "UPDATE training_sessions SET review_count = review_count + 1 WHERE id = ?",
            (session_id,),
        )
        connection.commit()
        review_number = session["review_count"] + 1

    phrase_query = """
        SELECT p.*, e.kind AS exercise_kind
        FROM phrases p
        LEFT JOIN exercises e ON e.phrase_id = p.id AND e.kind = ?
        WHERE p.language = ? AND p.segment_kind = ?
    """
    phrase_params: list[str | int] = [
        preferred_kind,
        session["language"],
        preferred_kind,
    ]
    has_candidates = connection.execute(
        "SELECT 1 FROM session_candidates WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    if has_candidates:
        phrase_query += """
            AND EXISTS (
                SELECT 1 FROM session_candidates candidates
                WHERE candidates.session_id = ? AND candidates.phrase_id = p.id
            )
        """
        phrase_params.append(session_id)
    if preferred_kind == "listening":
        length_limits = {
            "short": ("p.duration_seconds <= ?", 4.5),
            "medium": ("p.duration_seconds <= ?", 9),
            "long": ("p.duration_seconds > ? AND p.duration_seconds <= ?", 9, 15),
        }
        length_filter, *length_values = length_limits.get(
            session["listening_length"], length_limits["medium"]
        )
        phrase_query += f" AND ({length_filter})"
        phrase_params.extend(length_values)
    if not review_mode:
        phrase_query += """
            AND NOT EXISTS (
                SELECT 1 FROM session_items si
                WHERE si.session_id = ? AND si.phrase_id = p.id AND si.kind = p.segment_kind
            )
        """
        phrase_params.append(session_id)
    if review_mode:
        phrase_query += """
            AND EXISTS (
                SELECT 1
                FROM exercises review_e
                JOIN attempts review_a ON review_a.exercise_id = review_e.id
                JOIN session_items review_si
                  ON review_si.phrase_id = p.id
                 AND review_si.kind = review_e.kind
                 AND review_si.session_id = ?
                WHERE review_e.phrase_id = p.id
                  AND review_a.is_correct = 0
                  AND NOT EXISTS (
                      SELECT 1
                      FROM attempts successful
                      WHERE successful.exercise_id = review_e.id
                        AND successful.is_correct = 1
                  )
            )
        """
        phrase_params.append(session_id)
    if exclude_phrase_ids and not review_mode:
        placeholders = ", ".join("?" for _ in exclude_phrase_ids)
        phrase_query += f" AND p.id NOT IN ({placeholders})"
        phrase_params.extend(exclude_phrase_ids)
    phrase_query += """
        ORDER BY
            RANDOM()
        LIMIT 1
        """
    phrase = connection.execute(phrase_query, phrase_params).fetchone()
    if phrase is None:
        raise HTTPException(
            status_code=404 if exclude_phrase_ids else 404,
            detail=(
                "Больше непоказанных заданий этого типа нет."
                if exclude_phrase_ids
                else "Для этого языка пока нет импортированных субтитров."
            ),
        )

    kind = phrase["segment_kind"]
    connection.execute(
        """
        INSERT OR IGNORE INTO session_items (session_id, phrase_id, kind)
        VALUES (?, ?, ?)
        """,
        (session_id, phrase["id"], kind),
    )
    connection.commit()
    exercise = connection.execute(
        "SELECT * FROM exercises WHERE phrase_id = ? AND kind = ? ORDER BY id LIMIT 1",
        (phrase["id"], kind),
    ).fetchone()
    if exercise is None:
        cursor = connection.execute(
            "INSERT INTO exercises (phrase_id, kind) VALUES (?, ?)",
            (phrase["id"], kind),
        )
        connection.commit()
        exercise_id = cursor.lastrowid
    else:
        exercise_id = exercise["id"]

    words = phrase["text"].split()
    shuffled = words[:]
    random.Random(phrase["id"]).shuffle(shuffled)
    try:
        translation = get_or_create_translation(phrase, connection)
    except TranslationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "id": exercise_id,
        "kind": kind,
        "phrase_id": phrase["id"],
        "language": phrase["language"],
        "source_title": phrase["source_title"],
        "video_path": phrase["video_path"],
        "time": f"{phrase['start_time']} - {phrase['end_time']}",
        "words": shuffled,
        "translation_words": translation_words(translation, phrase["id"])
        if kind == "translation"
        else [],
        "word_count": len(words),
        "is_review": review_mode,
        "number": review_number if review_mode else sum(issued_by_kind.values()) + 1,
        "total": sum(targets.values()),
        "phrase_text": phrase["text"],
        "translation": translation,
        "prompt": (
            "Послушай фрагмент и восстанови услышанную фразу."
            if kind == "listening"
            else "Переведи эту фразу на русский."
        ),
    }


@app.post("/api/exercises/{exercise_id}/attempt")
def submit_attempt(
    exercise_id: int,
    payload: AttemptCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    exercise = connection.execute(
        """
        SELECT e.id, e.kind, p.id AS phrase_id, p.language, p.text
        FROM exercises e
        JOIN phrases p ON p.id = e.phrase_id
        WHERE e.id = ?
        """,
        (exercise_id,),
    ).fetchone()
    if exercise is None:
        raise HTTPException(status_code=404, detail="Упражнение не найдено.")

    is_translation = exercise["kind"] == "translation"
    try:
        expected_answer = (
            get_or_create_translation(exercise, connection)
            if is_translation
            else exercise["text"]
        )
    except TranslationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    is_correct = normalize(payload.answer) == normalize(expected_answer)
    connection.execute(
        "INSERT INTO attempts (exercise_id, answer, is_correct) VALUES (?, ?, ?)",
        (exercise_id, payload.answer, int(is_correct is True)),
    )
    connection.commit()
    try:
        translation = get_or_create_translation(exercise, connection)
    except TranslationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "correct": is_correct,
        "answer": expected_answer,
        "original": exercise["text"],
        "translation": translation,
        "message": (
            "Верно!"
            if is_correct
            else "Почти. Попробуй ещё раз."
        ),
    }


@app.get("/api/exercises/{exercise_id}")
def get_exercise(
    exercise_id: int, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    exercise = connection.execute(
        """
        SELECT e.id, e.kind, p.id AS phrase_id, p.text, p.source_title,
               p.language, p.video_path, p.start_time, p.end_time
        FROM exercises e
        JOIN phrases p ON p.id = e.phrase_id
        WHERE e.id = ?
        """,
        (exercise_id,),
    ).fetchone()
    if exercise is None:
        raise HTTPException(status_code=404, detail="Упражнение не найдено.")
    words = exercise["text"].split()
    shuffled = words[:]
    random.Random(exercise["phrase_id"]).shuffle(shuffled)
    try:
        translation = get_or_create_translation(exercise, connection)
    except TranslationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "id": exercise["id"],
        "kind": exercise["kind"],
        "phrase_id": exercise["phrase_id"],
        "language": exercise["language"],
        "source_title": exercise["source_title"],
        "video_path": exercise["video_path"],
        "time": f"{exercise['start_time']} - {exercise['end_time']}",
        "words": shuffled,
        "translation_words": translation_words(translation, exercise["phrase_id"])
        if exercise["kind"] == "translation"
        else [],
        "phrase_text": exercise["text"],
        "translation": translation,
        "is_review": False,
        "number": 1,
        "total": 1,
        "prompt": (
            "Послушай фрагмент и восстанови услышанную фразу."
            if exercise["kind"] == "listening"
            else "Переведи эту фразу на русский."
        ),
    }


@app.get("/api/history")
@app.get("/api/sessions/{session_id}/history")
def history(
    session_id: int | None = None,
    connection: sqlite3.Connection = Depends(get_connection),
) -> list[dict]:
    session_filter = ""
    params: list[int] = []
    if session_id is not None:
        session = connection.execute(
            "SELECT language FROM training_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Сессия не найдена.")
        session_filter = "AND p.language = ?"
        params.append(session["language"])
    rows = connection.execute(
        """
        SELECT e.id AS exercise_id, p.text, p.source_title, p.video_path,
               p.start_time, p.end_time, e.kind,
               MAX(a.created_at) AS completed_at,
               MAX(a.is_correct) AS is_correct,
               COUNT(a.id) AS attempts
        FROM exercises e
        JOIN phrases p ON p.id = e.phrase_id
        LEFT JOIN attempts a ON a.exercise_id = e.id
        WHERE a.id IS NOT NULL
        """
        + session_filter
        + """
        GROUP BY e.id
        ORDER BY completed_at DESC
        LIMIT 100
        """,
        params,
    ).fetchall()
    result = [row_to_dict(row) for row in rows]
    dictionary_rows = connection.execute(
        """
        SELECT s.id AS session_id, s.language, s.created_at,
               COUNT(DISTINCT i.word_id) AS word_count,
               GROUP_CONCAT(DISTINCT w.word) AS words,
               MAX(a.created_at) AS completed_at,
               CASE WHEN COUNT(a.id) = 0 THEN 0
                    WHEN SUM(a.is_correct) = COUNT(a.id) THEN 1 ELSE 0 END AS is_correct
        FROM dictionary_sessions s
        LEFT JOIN dictionary_session_items i ON i.session_id = s.id
        LEFT JOIN dictionary_words w ON w.id = i.word_id
        LEFT JOIN dictionary_attempts a ON a.session_id = s.id
        GROUP BY s.id
        ORDER BY COALESCE(completed_at, s.created_at) DESC
        LIMIT 100
        """
    ).fetchall()
    result.extend(
        {
            "dictionary_session_id": row["session_id"],
            "language": row["language"],
            "text": row["words"] or "",
            "kind": "dictionary",
            "completed_at": row["completed_at"] or row["created_at"],
            "is_correct": row["is_correct"],
            "word_count": row["word_count"],
        }
        for row in dictionary_rows
    )
    result.sort(key=lambda item: item["completed_at"], reverse=True)
    return result[:100]


@app.get("/api/training-estimate")
def training_estimate(
    language: str, connection: sqlite3.Connection = Depends(get_connection)
) -> dict[str, float]:
    if language not in LANGUAGES:
        raise HTTPException(status_code=400, detail="Неподдерживаемый язык.")
    result: dict[str, float] = {}
    for kind, base_seconds in (("listening", 210), ("translation", 90)):
        rows = connection.execute(
            """
            SELECT e.id, COUNT(a.id) AS attempts
            FROM exercises e
            JOIN phrases p ON p.id = e.phrase_id
            JOIN attempts a ON a.exercise_id = e.id
            WHERE p.language = ? AND e.kind = ?
            GROUP BY e.id
            ORDER BY MAX(a.created_at) DESC
            LIMIT 10
            """,
            (language, kind),
        ).fetchall()
        average_attempts = (
            sum(row["attempts"] for row in rows) / len(rows) if rows else 1
        )
        result[kind] = round(base_seconds * average_attempts / 60, 1)
    return result
