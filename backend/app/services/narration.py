"""Keep chat-template control tokens out of player-facing narration."""

import re
from collections.abc import AsyncIterator

from app.llm.base import LLMProvider, ModelUnavailable

ROLE_MARKERS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>", "<|eot_id|>")


class NarrationGuard:
    def __init__(self) -> None:
        self.pending = ""
        self.stopped = False

    def feed(self, piece: str) -> str:
        if self.stopped:
            return ""
        self.pending += piece
        positions = [self.pending.find(marker) for marker in ROLE_MARKERS]
        positions = [position for position in positions if position >= 0]
        if positions:
            before = self.pending[:min(positions)]
            self.pending = ""
            self.stopped = True
            return before
        hold = max(
            (size for marker in ROLE_MARKERS for size in range(1, len(marker))
             if self.pending.endswith(marker[:size])),
            default=0,
        )
        if hold:
            visible, self.pending = self.pending[:-hold], self.pending[-hold:]
            return visible
        visible, self.pending = self.pending, ""
        return visible

    def finish(self) -> str:
        if self.stopped:
            return ""
        visible, self.pending = self.pending, ""
        return visible


def clean_history_narration(text: str) -> str:
    """Use the last assistant passage if an older saved turn contains a transcript."""
    if "<|im_start|>" not in text:
        return text.split("<|im_end|>", 1)[0].strip()
    sections = re.split(r"<\|im_start\|>(assistant|user|system)\b", text)
    assistant_passages = [sections[index + 1].split("<|im_end|>", 1)[0].strip()
                          for index in range(1, len(sections) - 1, 2)
                          if sections[index] == "assistant"]
    return (assistant_passages[-1] if assistant_passages else sections[0].split("<|im_end|>", 1)[0]).strip()


def _repeats_history(prefix: str, messages: list[dict[str, str]]) -> bool:
    sample = prefix.strip()[:80]
    return len(sample) >= 80 and any(
        message["content"].strip().startswith(sample)
        for message in messages if message["role"] == "assistant"
    )


async def stream_narration(provider: LLMProvider, messages: list[dict[str, str]],
                           max_tokens: int, temperature: float) -> AsyncIterator[str]:
    """Guard split role markers and retry once when a model echoes an old passage."""
    for attempt in range(2):
        request_messages = messages if attempt == 0 else [
            *messages[:-1],
            {**messages[-1], "content": messages[-1]["content"] +
             "\n\nRespond only to this latest action. Do not repeat an earlier scene or write role labels."},
        ]
        guard = NarrationGuard()
        prefix = ""
        released = False
        repeated = False
        stream = provider.stream_chat(request_messages, max_tokens=max_tokens, temperature=temperature)
        try:
            async for raw in stream:
                piece = guard.feed(raw)
                if not released:
                    prefix += piece
                    if len(prefix.strip()) >= 100 or guard.stopped:
                        if _repeats_history(prefix, messages):
                            repeated = True
                            break
                        released = True
                        if prefix:
                            yield prefix
                        prefix = ""
                elif piece:
                    yield piece
                if guard.stopped:
                    break
        finally:
            close = getattr(stream, "aclose", None)
            if close:
                await close()
        if repeated:
            continue
        tail = guard.finish()
        if not released:
            prefix += tail
            if _repeats_history(prefix, messages):
                continue
            if not prefix.strip() and attempt == 0:
                continue
            if prefix:
                yield prefix
        elif tail:
            yield tail
        return
    raise ModelUnavailable("The model repeated an earlier scene. Try regenerating or selecting another model.")
