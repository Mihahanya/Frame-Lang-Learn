import json
import os
import re
import sqlite3
from urllib import error as url_error
from urllib import request as url_request

from fastapi import HTTPException

from .schemas import SearchAdaptCreate, SearchCreate


LLM_API_URL = os.getenv(
    "LLM_API_URL",
    os.getenv("HF_API_URL", "https://router.huggingface.co/v1/chat/completions"),
)
LLM_MODEL = os.getenv(
    "LLM_MODEL", os.getenv("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct")
)
LLM_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv("LLM_FALLBACK_MODELS", "").split(",")
    if model.strip()
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def search_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-ZäöüÄÖÜßα-ωΑ-Ω]+", text.lower())
        if len(token) > 2
    }


def lemma_forms(lemma: str) -> set[str]:
    lemma = normalize(lemma).strip(".,;:!?")
    forms = {lemma}
    if " " in lemma:
        return forms
    if lemma.endswith("en"):
        forms.update(
            {lemma[:-2] + suffix for suffix in ("e", "t", "te", "ten", "et", "end")}
        )
    elif lemma.endswith("er"):
        forms.add(lemma[:-2] + "e")
    elif lemma.endswith("e"):
        forms.update({lemma[:-1] + suffix for suffix in ("en", "t", "te")})
    return forms


def _keywords_from_model(text: str) -> list[str]:
    text = re.sub(r"```(?:text|markdown)?|```", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"(?im)^\s*(?:ключевые слова|keywords?)\s*:\s*", "", text)

    # Models sometimes explain the task first and put the actual list after a colon.
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        text = max(lines, key=lambda line: line.count(","))

    keywords: list[str] = []
    for item in re.split(r"[,;]", text):
        item = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", item)
        item = item.strip(" []().\"'")
        if item and item not in keywords:
            keywords.append(item)
    if not keywords:
        raise ValueError("Модель не вернула список ключевых слов.")
    return keywords


def _model_text(result: dict) -> str:
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Провайдер не вернул варианты ответа.")
    message = choices[0].get("message", {})
    if not isinstance(message, dict):
        raise ValueError("Провайдер вернул сообщение неверного формата.")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    if not content:
        content = message.get("reasoning") or choices[0].get("text")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Провайдер не вернул текстовый ответ.")
    return content


def _provider_error(exc: url_error.HTTPError, model: str) -> HTTPException:
    try:
        details = json.loads(exc.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        details = {}
    message = str(details.get("error", "")).strip()
    if exc.code == 401:
        detail = "Токен выбранного LLM-провайдера недействителен или не принят."
    elif exc.code == 402:
        detail = "Провайдер LLM требует оплату или дополнительные кредиты для этого запроса."
    elif exc.code == 429:
        detail = (
            f"Модель {model} временно перегружена. "
            "Добавьте резервную модель в LLM_FALLBACK_MODELS или повторите запрос позже."
        )
    elif exc.code == 403:
        detail = message or "Провайдер LLM отклонил запрос."
    elif exc.code == 400 and "not supported" in message.lower():
        detail = f"Модель {model} не поддерживается выбранным провайдером."
    else:
        detail = message or f"Провайдер LLM вернул HTTP {exc.code}."
    return HTTPException(status_code=503, detail=detail)


def adapt_search_topic(payload: SearchAdaptCreate) -> list[str]:
    token = os.getenv("LLM_TOKEN", os.getenv("HF_TOKEN"))
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Не задан LLM_TOKEN. Добавьте токен провайдера LLM в файл .env.",
        )
    prompt = (
        "Тема пользователя: "
        f"{payload.topic}\n\n"
        "Составь широкий набор поисковых понятий на языке изучения для субтитров. "
        "Охвати несколько аспектов темы: прямые слова темы, связанные действия, "
        "участников и объекты, эмоции и оценки, типичные ситуации, места и "
        "устойчивые выражения. Используй начальные формы слов и короткие фразы. "
        "Не добавляй общие слова, не связанные с темой. "
        "Верни только 12-20 элементов через запятую в одной строке, без пояснений."
        f"\nЯзык изучения: {'Немецкий' if payload.language == 'de' else payload.language}"
    )
    result = None
    last_error: HTTPException | None = None
    models = list(dict.fromkeys([LLM_MODEL, *LLM_FALLBACK_MODELS]))
    for model in models:
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты — редактор поисковых запросов для приложения изучения "
                            "языка. Твоя задача — расширить тему семантически, чтобы "
                            "локальный поиск нашёл разнообразные реальные фразы."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
                "max_tokens": 300,
            }
        ).encode()
        request = url_request.Request(
            LLM_API_URL,
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with url_request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except url_error.HTTPError as exc:
            last_error = _provider_error(exc, model)
        except (url_error.URLError, TimeoutError) as exc:
            last_error = HTTPException(
                status_code=503,
                detail="Не удалось подключиться к провайдеру LLM. Проверьте сеть Docker.",
            )
            if model == models[-1]:
                raise last_error from exc
    if result is None:
        raise last_error or HTTPException(
            status_code=503, detail="Провайдер LLM не вернул ответ."
        )
    provider_error = result.get("error")
    if provider_error:
        message = (
            provider_error.get("message", "")
            if isinstance(provider_error, dict)
            else str(provider_error)
        )
        raise HTTPException(status_code=503, detail=message or "Провайдер LLM вернул ошибку.")
    try:
        return _keywords_from_model(_model_text(result))[:50]
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Модель не вернула список ключевых слов: {exc}",
        ) from exc


def search_phrases(
    payload: SearchCreate, connection: sqlite3.Connection
) -> dict:
    keywords = [normalize(keyword) for keyword in payload.keywords if normalize(keyword)]
    if not keywords:
        raise HTTPException(status_code=400, detail="Добавьте хотя бы одно ключевое слово.")
    rows = connection.execute(
        """
        SELECT p.id, p.text, p.source_title, p.video_path, p.start_time,
               p.end_time, p.segment_kind
        FROM phrases p
        WHERE p.language = ?
        """,
        (payload.language,),
    ).fetchall()
    scored: list[tuple[int, sqlite3.Row, list[str]]] = []
    for row in rows:
        tokens = search_tokens(row["text"])
        matched: list[str] = []
        score = 0
        for keyword in keywords:
            keyword_tokens = search_tokens(keyword)
            if not keyword_tokens:
                continue
            if len(keyword_tokens) > 1:
                if all(token in tokens for token in keyword_tokens):
                    matched.append(keyword)
                    score += 3
                continue
            forms = lemma_forms(keyword)
            if any(form in tokens for form in forms):
                matched.append(keyword)
                score += 2 if keyword in tokens else 1
        if matched:
            scored.append((score, row, matched))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    results = []
    for score, row, matched in scored[: payload.limit]:
        exercise = connection.execute(
            "SELECT id, kind FROM exercises WHERE phrase_id = ? ORDER BY id LIMIT 1",
            (row["id"],),
        ).fetchone()
        results.append(
            {
                "phrase_id": row["id"],
                "exercise_id": exercise["id"] if exercise else None,
                "kind": exercise["kind"] if exercise else row["segment_kind"],
                "text": row["text"],
                "source_title": row["source_title"],
                "video_path": row["video_path"],
                "time": f"{row['start_time']} - {row['end_time']}",
                "score": score,
                "matched_keywords": matched,
            }
        )
    return {"count": len(results), "total_count": len(scored), "results": results}
