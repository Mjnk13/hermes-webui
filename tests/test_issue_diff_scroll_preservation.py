"""Regression coverage for DiffCard scroll state across live DOM rebuilds."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_JS = ROOT / "static" / "ui.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _run_node(script: str):
    assert NODE
    result = subprocess.run(
        [NODE, "-e", script],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    params = source.index("(", start)
    depth = 0
    close = -1
    for idx in range(params, len(source)):
        if source[idx] == "(":
            depth += 1
        elif source[idx] == ")":
            depth -= 1
            if depth == 0:
                close = idx
                break
    brace = source.index("{", close)
    depth = 0
    for idx in range(brace, len(source)):
        if source[idx] == "{":
            depth += 1
        elif source[idx] == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"{name} did not close")


def test_diff_scroll_and_expanded_view_survive_rebuild():
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(UI_JS))},'utf8');
function extractFunc(name){{
  const start=src.indexOf('function '+name);
  if(start<0) throw new Error(name+' not found');
  const params=src.indexOf('(',start);
  let depth=0,close=-1;
  for(let i=params;i<src.length;i++){{
    if(src[i]==='(') depth++;
    else if(src[i]===')'&&--depth===0){{close=i;break;}}
  }}
  const brace=src.indexOf('{{',close);
  depth=0;
  for(let i=brace;i<src.length;i++){{
    if(src[i]==='{{') depth++;
    else if(src[i]==='}}'&&--depth===0) return src.slice(start,i+1);
  }}
  throw new Error(name+' did not close');
}}
class Classes{{
  constructor(names){{this.names=new Set(names);}}
  contains(name){{return this.names.has(name);}}
}}
class AttrNode{{
  constructor(attrs={{}}){{this.attrs={{...attrs}};}}
  getAttribute(name){{return Object.prototype.hasOwnProperty.call(this.attrs,name)?this.attrs[name]:null;}}
  setAttribute(name,value){{this.attrs[name]=String(value);}}
}}
function makeTree(expanded,fullTop,fullLeft){{
  const row=new AttrNode({{'data-mutation-event-key':'call:patch-1'}});
  const file=new AttrNode({{'data-modified-file-path':'src/long.js'}});
  const more={{hidden:expanded}};
  const less={{hidden:!expanded}};
  const wrap=new AttrNode({{'data-expanded':expanded?'1':'0'}});
  const makePre=(variant,top,left,hidden)=>({{
    classList:new Classes(['assistant-code-diff',variant]),
    scrollTop:top,
    scrollLeft:left,
    scrollHeight:1400,
    clientHeight:360,
    scrollWidth:980,
    clientWidth:420,
    hidden,
    closest(selector){{
      if(selector.includes('data-mutation-event-key')) return row;
      if(selector==='.assistant-modified-file-card') return file;
      if(selector==='.assistant-modified-diff-wrap') return wrap;
      return null;
    }},
  }});
  const short=makePre('is-truncated',0,0,expanded);
  const full=makePre('is-full',fullTop,fullLeft,!expanded);
  wrap.querySelector=(selector)=>({{
    '.assistant-code-diff.is-truncated':short,
    '.assistant-code-diff.is-full':full,
    '[data-diff-action="more"]':more,
    '[data-diff-action="less"]':less,
  }})[selector]||null;
  return {{wrap,short,full,more,less,root:{{
    querySelectorAll(selector){{
      if(selector==='.assistant-code-diff') return [short,full];
      return [];
    }},
  }}}};
}}
global.S={{session:{{session_id:'sid-1'}}}};
global._worklogDetailDisclosureSelector='.no-worklog-details';
global._worklogDetailDisclosureKeyForElement=()=>'';
global._worklogDetailScrollableBody=()=>null;
global._worklogDetailDisclosureIsOpen=()=>false;
global._setWorklogDetailDisclosureOpen=()=>{{}};
eval(extractFunc('_assistantDiffScrollKeyForElement'));
eval(extractFunc('_setAssistantDiffExpandedState'));
eval(extractFunc('_captureWorklogDetailDisclosureState'));
eval(extractFunc('_restoreWorklogDetailDisclosureState'));

const before=makeTree(true,612,47);
const state=_captureWorklogDetailDisclosureState(before.root);
const after=makeTree(false,0,0);
_restoreWorklogDetailDisclosureState(after.root,state);
process.stdout.write(JSON.stringify({{
  expanded:after.wrap.getAttribute('data-expanded'),
  shortHidden:after.short.hidden,
  fullHidden:after.full.hidden,
  moreHidden:after.more.hidden,
  lessHidden:after.less.hidden,
  scrollTop:after.full.scrollTop,
  scrollLeft:after.full.scrollLeft,
}}));
"""
    assert _run_node(script) == {
        "expanded": "1",
        "shortHidden": True,
        "fullHidden": False,
        "moreHidden": True,
        "lessHidden": False,
        "scrollTop": 612,
        "scrollLeft": 47,
    }


def test_all_live_diff_replacement_paths_preserve_nested_scroll_state():
    source = UI_JS.read_text(encoding="utf-8")
    flush = _function_body(source, "_flushDeferredLiveAssistantModifiedDiffs")
    append = _function_body(source, "_appendLiveModifiedFilesCard")
    compact = _function_body(source, "renderLiveAnchorActivityScene")
    transparent = _function_body(source, "_refreshTransparentLiveRow")

    assert "_replaceAssistantModifiedFilesHtmlPreservingState(" in flush
    assert "_replaceAssistantModifiedFilesHtmlPreservingState(" in append
    assert "_captureWorklogDetailDisclosureState(blocks)" in compact
    assert "_restoreWorklogDetailDisclosureState(blocks, liveDisclosureState)" in compact
    assert "_captureWorklogDetailDisclosureState(existing)" in transparent
    assert "_restoreWorklogDetailDisclosureState(existing,nestedDisclosureState)" in transparent
