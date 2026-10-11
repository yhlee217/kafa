#!/usr/bin/env python3
"""Stop hook — 멈추려 할 때 다음 작업 하나를 이어서 하게 한다 (연속 3회까지).
마지막 답이 '완료'면 바로 멈춘다. 입력/카운터 처리에 실패하면 막지 않는다."""
import json
import os
import re
import sys
import tempfile

MAX_CONTINUES = 3
DONE = "완료"
TAIL_WINDOWS = (256 * 1024, 4 * 1024 * 1024)
REASON = (
    "방금 작업 결과를 기준으로 다음에 할 가능성이 가장 높은 작업 하나를 골라 바로 진행해. "
    "남은 작업이 없거나, 파괴적이거나, 사용자 확인이 필요하면 진행하지 말고 '완료'라고만 답해."
)


def counter_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id) or "default"
    return os.path.join(tempfile.gettempdir(), f"claude_auto_continue_{safe}.count")


def read_count(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_count(path, count):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(count))
        return True
    except OSError:
        return False


def is_done(text):
    t = text.strip().strip("'\"`*“”‘’ ").rstrip(".!。 ").strip("'\"`*“”‘’ ")
    return t == DONE


def entry_text(raw):
    try:
        entry = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(entry, dict) or entry.get("isSidechain"):
        return None, None
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        texts = [content]
    else:
        texts = [c.get("text", "") for c in content or []
                 if isinstance(c, dict) and c.get("type") == "text"]
    if entry.get("type") == "assistant" and texts:
        return "assistant", "".join(texts)
    if entry.get("type") == "user" and texts:
        return "turn", None
    return None, None


def last_reply(data):
    message = data.get("last_assistant_message")
    if isinstance(message, str):
        return message
    path = data.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        size = os.path.getsize(path)
        for window in TAIL_WINDOWS:
            start = max(0, size - window)
            with open(path, "rb") as f:
                f.seek(start)
                lines = f.read().split(b"\n")
            if start > 0:
                lines = lines[1:]
            for raw in reversed(lines):
                kind, text = entry_text(raw)
                if kind == "assistant":
                    return text
                if kind == "turn":
                    return None
            if start == 0:
                return None
    except OSError:
        return None
    return None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0

    reply = last_reply(data)
    if reply is not None and is_done(reply):
        return 0

    path = counter_path(str(data.get("session_id") or ""))
    if not data.get("stop_hook_active"):
        if not write_count(path, 0):
            return 0

    count = read_count(path)
    if count >= MAX_CONTINUES:
        return 0
    if not write_count(path, count + 1):
        return 0

    sys.stdout.buffer.write(json.dumps(
        {"decision": "block", "reason": REASON}, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
