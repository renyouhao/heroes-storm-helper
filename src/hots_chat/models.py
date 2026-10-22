from dataclasses import dataclass


@dataclass(frozen=True)
class ChatEvent:
    message_id: str
    generation: int
    channel: str
    sender: str
    timestamp: str
    text: str


@dataclass(frozen=True)
class TranslationJob:
    job_id: str
    message_id: str | None
    generation: int
    text: str
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class TranslationResult:
    job_id: str
    translated: str
    detected_language: str | None
    request_id: str | None
    elapsed_ms: float


@dataclass(frozen=True)
class AppEvent:
    kind: str
    generation: int
    payload: object
