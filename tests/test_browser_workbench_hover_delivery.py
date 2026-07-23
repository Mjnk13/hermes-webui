import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _js_function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def _run_node_json(program: str):
    result = subprocess.run(
        ["node", "-e", program],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_iframe_hover_delivery_cannot_be_starved_by_continuous_pointer_events():
    bridge_source = _read("api/browser_workbench.py")
    scheduler = _js_function(bridge_source, "scheduleBrowserWorkbenchHoverPost")
    program = "\n".join(
        [
            "let hoverTimer=0;",
            "let pendingHoverSelection=null;",
            "let selectionMode=true;",
            "const timers=[];",
            "const posts=[];",
            "const setTimeout=(callback,delay)=>{timers.push({callback,delay});return timers.length;};",
            "const post=(payload)=>posts.push(payload);",
            scheduler,
            "scheduleBrowserWorkbenchHoverPost({selector:'#first'});",
            "scheduleBrowserWorkbenchHoverPost({selector:'#second'});",
            "scheduleBrowserWorkbenchHoverPost({selector:'#latest'});",
            "timers[0].callback();",
            "console.log(JSON.stringify({timerCount:timers.length,delay:timers[0].delay,posts}));",
        ]
    )

    result = _run_node_json(program)
    assert result == {
        "timerCount": 1,
        "delay": 60,
        "posts": [{"type": "hover", "selection": {"selector": "#latest"}}],
    }
