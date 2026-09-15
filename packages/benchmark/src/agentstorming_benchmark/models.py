# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Bedrock Converse client wrapper with retries, concurrency, and cost accounting.

Uses the *sync* Bedrock client wrapped in a thread pool so we can orchestrate
many agents with asyncio without pulling in aiobotocore. This is simpler and
equally performant when the bottleneck is Bedrock-side latency.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

import boto3
from botocore.config import Config as BotoConfig
from tenacity import AsyncRetrying, RetryError, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type

from .config import AWS_REGION, CONCURRENCY, PRIMARY_MODEL, PRICES_USD_PER_MTOK, CONSTANTS
from .types import Message, TokenUsage


log = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Bedrock Converse-shaped message (role + text content)."""
    role: str
    text: str


@lru_cache(maxsize=1)
def _client():
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=BotoConfig(
            retries={"max_attempts": 8, "mode": "adaptive"},
            read_timeout=180,
            connect_timeout=30,
        ),
    )


# Global concurrency cap across the whole process. Bedrock tokens per minute
# and requests per minute are the main bottleneck; 8 concurrent calls gives
# comfortable headroom at the default Converse quota.
_sem: asyncio.Semaphore | None = None


def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(CONCURRENCY)
    return _sem


# Bedrock throttling / service exceptions we want to retry.
class _TransientError(Exception):
    pass


_MODELS_WITHOUT_TEMPERATURE = (
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
)


def _model_rejects_temperature(model: str) -> bool:
    return any(s in model for s in _MODELS_WITHOUT_TEMPERATURE)


def _is_transient(exc: BaseException) -> bool:
    msg = str(exc)
    return any(s in msg for s in ("Throttling", "ThrottlingException", "Too many requests",
                                  "ServiceUnavailable", "ReadTimeoutError", "EndpointConnectionError",
                                  "InternalServerException", "ModelStreamErrorException",
                                  # SSL/TLS intermittent issues: retry.
                                  "SSLError", "SSL validation", "SSL_ERROR",
                                  "ConnectionError", "Connection reset", "Connection aborted",
                                  "RemoteDisconnected", "BadStatusLine"))


async def converse(
    messages: Sequence[ChatMessage],
    *,
    system: str | None = None,
    model: str = PRIMARY_MODEL,
    temperature: float = CONSTANTS.default_temperature,
    max_tokens: int = CONSTANTS.max_output_tokens,
    stop_sequences: Sequence[str] | None = None,
) -> tuple[str, TokenUsage]:
    """One Bedrock Converse call. Returns (text, usage).

    Retries on throttle/transient errors with exponential backoff + jitter.
    Subject to the process-global concurrency semaphore.
    """
    sem = _get_sem()
    bedrock_messages = [
        {"role": m.role, "content": [{"text": m.text}]} for m in messages
    ]
    # Some 2026 models (notably Claude Opus 4.5+ extended-thinking
    # variants) refuse `temperature` and require it to be omitted —
    # they decide deterministically. Detect by model id substring and
    # drop the parameter in that case.
    inference_config: dict = {"maxTokens": max_tokens}
    if not _model_rejects_temperature(model):
        inference_config["temperature"] = temperature
    if stop_sequences:
        inference_config["stopSequences"] = list(stop_sequences)
    kwargs: dict = {
        "modelId": model,
        "messages": bedrock_messages,
        "inferenceConfig": inference_config,
    }
    if system:
        kwargs["system"] = [{"text": system}]

    async with sem:
        try:
            async for attempt in AsyncRetrying(
                wait=wait_exponential_jitter(initial=2, max=60),
                stop=stop_after_attempt(8),
                retry=retry_if_exception_type(_TransientError),
                reraise=True,
            ):
                with attempt:
                    loop = asyncio.get_running_loop()
                    try:
                        resp = await loop.run_in_executor(None, lambda: _client().converse(**kwargs))
                    except Exception as e:
                        if _is_transient(e):
                            raise _TransientError(str(e)) from e
                        raise
        except RetryError as e:
            raise RuntimeError(f"bedrock retries exhausted: {e}") from e

    # Some Bedrock providers (notably DeepSeek R1) emit a separate
    # 'reasoningContent' block before / instead of 'text'; fall back
    # to the first block that has a text field, or empty string.
    content_blocks = resp["output"]["message"]["content"]
    out = ""
    for block in content_blocks:
        if "text" in block:
            out = block["text"]
            break
        if "reasoningContent" in block:
            rc = block["reasoningContent"]
            if isinstance(rc, dict) and "text" in rc:
                out = rc["text"]
    usage = resp.get("usage", {})
    tu = TokenUsage(
        input_tokens=usage.get("inputTokens", 0),
        output_tokens=usage.get("outputTokens", 0),
        cache_read_input_tokens=usage.get("cacheReadInputTokens", 0),
        cache_creation_input_tokens=usage.get("cacheWriteInputTokens", 0),
    )
    return out, tu


def cost_usd(usage: TokenUsage, model: str = PRIMARY_MODEL) -> float:
    p = PRICES_USD_PER_MTOK.get(model)
    if not p:
        return 0.0
    billable_in = usage.input_tokens + usage.cache_creation_input_tokens
    # Cache-read input tokens on Anthropic are billed at 10% of normal input (approx).
    billable_in += usage.cache_read_input_tokens * 0.1
    return (billable_in * p["input"] + usage.output_tokens * p["output"]) / 1_000_000


# Pattern for MCQ extraction - letters only, no numbers
ANSWER_PAT_MCQ = re.compile(r"(?i)(?:final\s*answer|the\s*answer\s*is|answer\s*:)\s*[:=]*\s*\(?([A-JZ])\)?")
# Pattern for numeric/mixed extraction (used by extract_numeric for MATH tasks)
ANSWER_PAT_NUMERIC = re.compile(r"(?i)(?:final\s*answer|the\s*answer\s*is|answer\s*:)\s*[:=]*\s*\(?([A-JZ]|[0-9]+(?:\.[0-9]+)?)\)?")


def extract_multiple_choice(text: str, valid_choices: list[str], choice_texts: list[str] | None = None) -> str:
    """Extract a letter A-J (or similar) from a free-form answer.

    Walks backwards from the end so that the *last* stated answer wins
    (helps with rambling refiners that change their mind).

    Args:
        text: Model's response text
        valid_choices: List of valid letter choices (e.g., ["A", "B", "C"])
        choice_texts: Optional list of actual choice texts (e.g., ["Peyton", "Isolde"]).
                      If provided and model responds with choice text instead of letter,
                      maps it back to the letter.
    """
    if not text:
        return ""
    # Try explicit "answer is X" patterns first, last match wins.
    # Use MCQ-only pattern that excludes numeric captures to avoid extracting
    # "5" when the model says "considering 5 factors... Final answer: 5" on MuSR
    matches = list(ANSWER_PAT_MCQ.finditer(text))
    if matches:
        candidate = matches[-1].group(1).upper().strip()
        if candidate in valid_choices:
            return candidate

    # If choice_texts provided, check if model responded with choice text instead of letter.
    # E.g., "Final answer: Isolde" instead of "Final answer: B"
    if choice_texts:
        # Look for "Final answer: <choice_text>" or "answer is <choice_text>" patterns
        for i, choice_text in enumerate(choice_texts):
            if not choice_text:
                continue
            # Escape special regex chars in choice text
            escaped = re.escape(str(choice_text))
            # Match at end of text (after "answer:" or similar)
            pattern = rf"(?i)(?:final\s*answer|the\s*answer\s*is|answer\s*:)\s*[:=]*\s*{escaped}\b"
            if re.search(pattern, text):
                return valid_choices[i] if i < len(valid_choices) else ""

    # Boxed LaTeX \boxed{A} fallback.
    m = re.search(r"\\boxed\{\s*([A-J])\s*\}", text)
    if m and m.group(1) in valid_choices:
        return m.group(1)
    # Last lone capital letter in A-J as last resort.
    for ch in reversed(text):
        if ch.isupper() and ch in valid_choices:
            return ch
    return ""


def extract_numeric(text: str) -> str:
    """For MATH-500-style numeric answers; prefer last \\boxed or 'answer is'."""
    if not text:
        return ""
    m = list(re.finditer(r"\\boxed\{([^}]+)\}", text))
    if m:
        return m[-1].group(1).strip()
    m = list(re.finditer(r"(?i)(?:final\s*answer|the\s*answer\s*is|answer\s*:)\s*[:=]*\s*(-?\d+(?:[./\s]\d+)?(?:\.\d+)?)", text))
    if m:
        return m[-1].group(1).strip()
    # last standalone number
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    return nums[-1] if nums else ""
