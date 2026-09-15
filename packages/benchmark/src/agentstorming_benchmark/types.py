# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Core types shared by runners, metrics, and analysis."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Literal
import json
import hashlib


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Question:
    """A benchmark question."""
    id: str
    dataset: str
    subject: str                         # e.g. "physics", "biology", "math-competition"
    question: str
    choices: list[str] | None            # multiple-choice options, if any
    answer_key: str                      # the ground-truth answer
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """One message in a transcript."""
    role: Role
    speaker: str                         # "single", "orchestrator", "mathematician", ...
    content: str
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z")
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenUsage:
    """Tokens consumed by a single LLM call."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __iadd__(self, other: "TokenUsage") -> "TokenUsage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        return self


@dataclass
class RunResult:
    """Output of one runner invocation on one question with one seed."""
    config_id: str
    dataset: str
    question_id: str
    seed: int
    model: str
    transcript: list[Message]
    extracted_answer: str                # what we will score
    is_correct: bool
    usage: TokenUsage
    cost_usd: float
    latency_sec: float
    final_statement: str = ""            # for CtC scoring (moderator synthesis or final answer)
    moderator_turns: int = 0
    participant_turns: int = 0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def dump(self) -> dict[str, Any]:
        d = asdict(self)
        d["transcript"] = [asdict(m) for m in self.transcript]
        d["usage"] = asdict(self.usage)
        return d

    def hash(self) -> str:
        payload = f"{self.config_id}|{self.dataset}|{self.question_id}|{self.seed}|{self.model}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RunResult":
        data = dict(data)
        data["usage"] = TokenUsage(**data["usage"])
        data["transcript"] = [Message(**m) for m in data["transcript"]]
        return cls(**data)


def save_result(result: RunResult, path) -> None:
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(result.dump(), f, indent=2, default=str)


def load_result(path) -> RunResult:
    with open(path) as f:
        return RunResult.from_json(json.load(f))
