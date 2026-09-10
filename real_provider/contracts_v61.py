from dataclasses import dataclass, asdict

@dataclass
class ProviderRequestV61:
    system: str
    user: str
    temperature: float = 0.1
    max_tokens: int = 1200
    def as_dict(self):
        return asdict(self)

@dataclass
class ProviderResponseV61:
    ok: bool
    content: str = ""
    model: str = ""
    latency_ms: int = 0
    error: str | None = None
    def as_dict(self):
        return asdict(self)
