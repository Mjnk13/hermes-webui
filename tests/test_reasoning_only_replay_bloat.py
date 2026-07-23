"""Regression coverage for non-partial reasoning-only replay bloat."""

from api.streaming import (
    _merge_display_messages_after_agent_result,
    _restore_display_reasoning_metadata,
)


def _settle_compacted_turn(previous_display, previous_context, prompt, answer):
    compacted_result = [
        {"role": "user", "content": prompt, "timestamp": 10},
        {"role": "assistant", "content": answer, "timestamp": 11},
    ]
    restored_result = _restore_display_reasoning_metadata(
        previous_display,
        compacted_result,
    )
    return _merge_display_messages_after_agent_result(
        previous_display,
        previous_context,
        restored_result,
        prompt,
    )


def test_non_partial_reasoning_only_replay_does_not_move_new_answer_off_tail():
    reasoning_only = {
        "id": 5090,
        "role": "assistant",
        "content": "",
        "reasoning": "historic incomplete reasoning",
        "reasoning_content": "historic incomplete reasoning",
        "finish_reason": "incomplete",
        "timestamp": 2,
        "_db_persisted": True,
    }
    previous_display = [
        {"role": "user", "content": "old question", "timestamp": 0},
        {"role": "assistant", "content": "old answer", "timestamp": 1},
        reasoning_only,
    ]
    previous_context = previous_display[:2]

    merged = _settle_compacted_turn(
        previous_display,
        previous_context,
        "new question",
        "new answer",
    )

    assert merged[-1]["content"] == "new answer"
    assert sum(
        1
        for message in merged
        if message.get("id") == reasoning_only["id"]
    ) == 1


def test_non_partial_reasoning_only_replay_stays_bounded_across_turns():
    reasoning_only = {
        "id": 5090,
        "role": "assistant",
        "content": "",
        "reasoning": "historic incomplete reasoning",
        "finish_reason": "incomplete",
        "timestamp": 2,
        "_db_persisted": True,
    }
    display = [
        {"role": "user", "content": "old question", "timestamp": 0},
        {"role": "assistant", "content": "old answer", "timestamp": 1},
        reasoning_only,
    ]
    context = display[:2]

    for turn in range(5):
        prompt = f"question {turn}"
        answer = f"answer {turn}"
        display = _settle_compacted_turn(display, context, prompt, answer)
        context = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ]
        assert display[-1]["content"] == answer

    assert sum(
        1
        for message in display
        if message.get("id") == reasoning_only["id"]
    ) == 1


def test_distinct_reasoning_snapshots_with_a_legacy_shared_id_are_preserved():
    previous_display = [
        {"role": "user", "content": "old question", "timestamp": 0},
        {"role": "assistant", "content": "old answer", "timestamp": 1},
        {
            "id": 5090,
            "role": "assistant",
            "content": "",
            "reasoning": "first incomplete segment",
            "finish_reason": "incomplete",
            "timestamp": 2,
        },
        {
            "id": 5090,
            "role": "assistant",
            "content": "",
            "reasoning": "second incomplete segment",
            "finish_reason": "incomplete",
            "timestamp": 2,
        },
    ]

    merged = _settle_compacted_turn(
        previous_display,
        previous_display[:2],
        "new question",
        "new answer",
    )

    reasoning = [
        message.get("reasoning")
        for message in merged
        if message.get("id") == 5090
    ]
    assert reasoning == [
        "first incomplete segment",
        "second incomplete segment",
    ]
    assert merged[-1]["content"] == "new answer"
