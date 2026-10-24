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
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if not isinstance(self.context, (tuple, list)) or len(self.context) > 3:
            raise ValueError('翻译上下文格式无效。')
        frozen = []
        for pair in self.context:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not all(isinstance(value, str) for value in pair):
                raise ValueError('翻译上下文格式无效。')
            frozen.append(tuple(pair))
        object.__setattr__(self, 'context', tuple(frozen))


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
