from types import SimpleNamespace

from api.streaming import _bind_agent_terminal_input_callback


def test_terminal_input_binding_uses_current_webui_request_bridge():
    calls = []

    def current_bridge(question, choices, **metadata):
        calls.append((question, choices, metadata))
        return "Yes"

    stale_bridge = lambda *_args, **_kwargs: "stale"  # noqa: E731
    agent = SimpleNamespace(
        clarify_callback=current_bridge,
        terminal_input_callback=stale_bridge,
    )

    assert _bind_agent_terminal_input_callback(agent) is True
    answer = agent.terminal_input_callback(
        "Terminal confirmation required",
        ["Yes", "No"],
        process_id="proc_live_1",
        prompt="Proceed? (y/N)",
    )

    assert answer == "Yes"
    assert calls == [
        (
            "Terminal confirmation required",
            ["Yes", "No"],
            {"process_id": "proc_live_1", "prompt": "Proceed? (y/N)"},
        )
    ]


def test_terminal_input_binding_clears_callback_from_finished_request():
    agent = SimpleNamespace(
        clarify_callback=None,
        terminal_input_callback=lambda *_args, **_kwargs: "stale",
    )

    assert _bind_agent_terminal_input_callback(agent) is False
    assert agent.terminal_input_callback is None
