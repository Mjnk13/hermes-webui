"""Regression coverage for once-only provider stream finalization."""

from pathlib import Path

from api.streaming import (
    _TurnOutcomeState,
    _current_turn_result_output_summary,
)


ROOT = Path(__file__).resolve().parents[1]


def test_completed_output_cannot_be_overwritten_by_late_failure():
    outcome = _TurnOutcomeState('stream-1', 'session-1')
    outcome.mark_text('A complete answer.')

    assert outcome.has_usable_output is True
    assert outcome.finalize('completed', reason='done') is True
    assert outcome.finalize('failed', reason='late-close', failure_type='no_response') is False
    assert outcome.payload()['turn_status'] == 'completed'


def test_whitespace_and_metadata_only_chunks_remain_empty():
    outcome = _TurnOutcomeState('stream-2', 'session-2')
    outcome.mark_text('  \n\t')
    outcome.mark_reasoning('   ')

    assert outcome.has_usable_output is False
    assert outcome.payload()['accumulated_text_length'] == 4


def test_reasoning_and_tool_activity_are_usable_output():
    reasoning = _TurnOutcomeState('stream-r', 'session-r')
    reasoning.mark_reasoning('Checked the constraints.')
    assert reasoning.has_usable_output is True

    tool = _TurnOutcomeState('stream-t', 'session-t')
    tool.mark_tool('call-1')
    tool.mark_tool('call-1', event_type='tool_complete')
    assert tool.has_usable_output is True
    assert tool.payload()['tool_event_count'] == 1


def test_result_summary_is_current_turn_scoped_and_counts_tool_only_output():
    previous = [
        {'role': 'user', 'content': 'old question'},
        {'role': 'assistant', 'content': 'old answer'},
    ]
    current = previous + [
        {'role': 'user', 'content': 'run the check'},
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call-1',
                    'type': 'function',
                    'function': {'name': 'check', 'arguments': '{}'},
                }
            ],
        },
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'ok'},
    ]

    summary = _current_turn_result_output_summary(current, previous, 'run the check')

    assert summary['assistant_text'] is False
    assert summary['tool_output'] is True
    assert summary['tool_event_count'] >= 1


def test_result_summary_does_not_reuse_prior_assistant_output():
    previous = [
        {'role': 'user', 'content': 'old question'},
        {'role': 'assistant', 'content': 'old answer'},
    ]
    current = previous + [{'role': 'user', 'content': 'new empty turn'}]

    summary = _current_turn_result_output_summary(current, previous, 'new empty turn')

    assert summary == {
        'assistant_text': False,
        'reasoning': False,
        'tool_output': False,
        'tool_event_count': 0,
        'finish_reason': None,
    }


def test_result_summary_counts_reasoning_only_current_output():
    previous = [{'role': 'user', 'content': 'old'}]
    current = previous + [
        {'role': 'user', 'content': 'think'},
        {'role': 'assistant', 'content': '', 'reasoning': 'I checked the constraints.'},
    ]

    summary = _current_turn_result_output_summary(current, previous, 'think')

    assert summary['assistant_text'] is False
    assert summary['reasoning'] is True


def test_frontend_ignores_late_application_error_after_done():
    source = (ROOT / 'static/messages.js').read_text(encoding='utf-8')
    start = source.index("source.addEventListener('apperror'")
    end = source.index("source.addEventListener('warning'", start)
    block = source[start:end]

    assert "if(_streamFinalized) return;" in block
    assert block.index("if(_streamFinalized) return;") < block.index("_streamFinalized=true;")
    assert "status:d.turn_status||d.status||d.type||'error'" in block
