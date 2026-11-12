from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")
    listening_count: int = Field(default=3, ge=0, le=20)
    translation_count: int = Field(default=5, ge=0, le=20)
    listening_length: str = Field(default="medium", pattern="^(short|medium|long)$")


class MaterialCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")
    title: str = Field(min_length=1, max_length=200)
    srt_text: str = Field(min_length=1)


class MediaScanCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")


class AttemptCreate(BaseModel):
    answer: str


class DictionaryWordCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")
    word: str = Field(min_length=1, max_length=100)


class DictionaryWordUpdate(BaseModel):
    translation: str = Field(min_length=1, max_length=200)
    selected: bool | None = None


class DictionarySessionCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")
    to_russian_count: int = Field(default=5, ge=0, le=50)
    from_russian_count: int = Field(default=5, ge=0, le=50)


class DictionaryAttemptCreate(BaseModel):
    word_id: int
    direction: str = Field(pattern="^(to_russian|from_russian)$")
    answer: str


class HistoryReviewCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")
    history_limit: int = Field(ge=10, le=1000000)
    exercise_count: int = Field(ge=1, le=100)


class SearchAdaptCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")
    topic: str = Field(min_length=2, max_length=1000)


class SearchCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")
    keywords: list[str] = Field(min_length=1, max_length=50)
    limit: int = Field(default=30, ge=1, le=100)


class SearchSessionCreate(BaseModel):
    language: str = Field(pattern="^(de|el)$")
    phrase_ids: list[int] = Field(min_length=1, max_length=100)
    listening_count: int = Field(default=3, ge=0, le=20)
    translation_count: int = Field(default=3, ge=0, le=20)
    listening_length: str = Field(default="medium", pattern="^(short|medium|long)$")
