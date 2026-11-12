import json
import os
from urllib import error, parse, request


TRANSLATION_URL = os.getenv(
    "TRANSLATION_URL", "https://api.mymemory.translated.net/get"
)


class TranslationError(RuntimeError):
    pass


def translate_to_russian(text: str, source_language: str) -> str:
    if len(text.encode("utf-8")) > 500:
        raise TranslationError("Фраза слишком длинная для бесплатного переводчика.")
    query = parse.urlencode(
        {"q": text, "langpair": f"{source_language}|ru", "mt": "1"}
    )
    http_request = request.Request(
        f"{TRANSLATION_URL}?{query}",
        headers={"Accept": "application/json"},
    )
    last_error: Exception | None = None
    for _ in range(3):
        try:
            with request.urlopen(http_request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get(
                    "error", f"HTTP {exc.code}"
                )
            except (ValueError, UnicodeDecodeError):
                detail = f"HTTP {exc.code}"
            raise TranslationError(f"LibreTranslate: {detail}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
    else:
        raise TranslationError("Бесплатный переводчик не ответил.") from last_error
    translated = data.get("responseData", {}).get("translatedText")
    if not translated:
        detail = data.get("responseStatus", "Переводчик вернул пустой перевод.")
        raise TranslationError(detail)
    return translated.strip()
