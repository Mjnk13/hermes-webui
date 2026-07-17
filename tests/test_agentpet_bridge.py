import threading


def test_agentpet_notify_is_best_effort(monkeypatch, tmp_path):
    import api.streaming as streaming

    calls = []
    helper = tmp_path / "agentpet"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(streaming, "_AGENTPET_BINARY_CACHE", str(helper))
    monkeypatch.setattr(streaming, "_AGENTPET_LAST_STATE", {})
    monkeypatch.setattr(streaming, "_AGENTPET_LAST_DETAILS", {})

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

    monkeypatch.setattr(streaming.subprocess, "run", fake_run)

    streaming._notify_agentpet(
        "working",
        "Running Hermes WebUI turn",
        session_id="sess-1",
        project="/tmp/project",
    )
    streaming._notify_agentpet(
        "working",
        "Running Hermes WebUI turn",
        session_id="sess-1",
        project="/tmp/project",
    )

    assert len(calls) == 1
    cmd = calls[0][0]
    assert cmd[:5] == [str(helper), "hook", "--agent", "webui", "--event"]
    assert "--session" in cmd
    assert "sess-1" in cmd
    assert "--project" in cmd
    assert "/tmp/project" in cmd


def test_agentpet_long_running_turn_refreshes_last_status(monkeypatch, tmp_path):
    import api.streaming as streaming

    calls = []
    refreshed = threading.Event()
    helper = tmp_path / "agentpet"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(streaming, "_AGENTPET_BINARY_CACHE", str(helper))
    monkeypatch.setattr(streaming, "_AGENTPET_LAST_STATE", {})
    monkeypatch.setattr(streaming, "_AGENTPET_LAST_DETAILS", {})
    monkeypatch.setattr(streaming, "_AGENTPET_HEARTBEAT_SECONDS", 0.01)

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if len(calls) >= 2:
            refreshed.set()

    monkeypatch.setattr(streaming.subprocess, "run", fake_run)

    streaming._notify_agentpet(
        "working",
        "Running a long Hermes WebUI turn",
        session_id="sess-long",
        project="/tmp/long-project",
    )
    stop, thread = streaming._start_agentpet_heartbeat(
        session_id="sess-long",
        project="/tmp/long-project",
    )
    assert stop is not None and thread is not None
    try:
        assert refreshed.wait(1.0), "active WebUI turn did not refresh its AgentPet lease"
    finally:
        stop.set()
        thread.join(timeout=1.0)
    assert not thread.is_alive()

    assert len(calls) >= 2
    heartbeat_cmd = calls[1][0]
    assert heartbeat_cmd[heartbeat_cmd.index("--event") + 1] == "working"
    assert heartbeat_cmd[heartbeat_cmd.index("--session") + 1] == "sess-long"
    assert heartbeat_cmd[heartbeat_cmd.index("--project") + 1] == "/tmp/long-project"
    assert heartbeat_cmd[heartbeat_cmd.index("--message") + 1] == "Running a long Hermes WebUI turn"


def test_agentpet_heartbeat_preserves_waiting_and_does_not_revive_done(monkeypatch, tmp_path):
    import api.streaming as streaming

    calls = []
    helper = tmp_path / "agentpet"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(streaming, "_AGENTPET_BINARY_CACHE", str(helper))
    monkeypatch.setattr(streaming, "_AGENTPET_HEARTBEAT_SECONDS", 60.0)
    monkeypatch.setattr(streaming, "_AGENTPET_LAST_STATE", {"sess-wait": ("waiting", 0.0)})
    monkeypatch.setattr(
        streaming,
        "_AGENTPET_LAST_DETAILS",
        {"sess-wait": ("Command approval required", "/tmp/wait-project")},
    )
    monkeypatch.setattr(streaming.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))

    streaming._heartbeat_agentpet(session_id="sess-wait", project="/tmp/wait-project")
    streaming._notify_agentpet(
        "done",
        "Hermes WebUI turn ended",
        force=True,
        session_id="sess-wait",
        project="/tmp/wait-project",
    )
    streaming._heartbeat_agentpet(session_id="sess-wait", project="/tmp/wait-project")

    assert [cmd[cmd.index("--event") + 1] for cmd in calls] == ["waiting", "done"]
    assert calls[0][calls[0].index("--message") + 1] == "Command approval required"
