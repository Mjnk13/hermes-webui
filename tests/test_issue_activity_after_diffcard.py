"""Regression coverage for realtime activity continuing after a DiffCard."""

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


def test_compact_worklog_keeps_rendering_activity_after_diffcard_failure():
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(UI_JS))},'utf8');
function extractFunc(name){{
  const start=src.indexOf('function '+name);
  if(start<0) throw new Error(name+' not found');
  const params=src.indexOf('(',start);
  let depth=0, close=-1;
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
class FakeNode{{
  constructor(label=''){{this.label=label;this.children=[];this.attrs={{}};this._html='';}}
  appendChild(node){{this.children.push(node);node.parentElement=this;return node;}}
  setAttribute(name,value){{this.attrs[name]=String(value);}}
  getAttribute(name){{return this.attrs[name]??null;}}
  set innerHTML(value){{this._html=String(value);if(value==='')this.children=[];}}
  get innerHTML(){{return this._html;}}
}}
global.window={{}};
global.document={{createElement:()=>new FakeNode('tool-group')}};
global.console={{warn:()=>{{}}}};
global._toolWorklogListEl=(group)=>group.list;
global._assistantAnchorSceneMutationItemsFromRow=(row)=>row.mutation?[{{path:'broken.js'}}]:[];
global._mergeLiveAssistantModifiedItems=(items)=>items;
global._assistantModifiedFilesNode=()=>{{throw new Error('malformed diff preview');}};
global._anchorSceneToolCallFromRow=(row)=>({{name:row.tool&&row.tool.name||'tool',done:false}});
global.buildToolCard=(tc)=>new FakeNode('tool:'+tc.name);
global._activityStatusNode=(opts)=>new FakeNode('status:'+opts.label);
global._anchorSceneNodeForRow=(row)=>new FakeNode(row.role+':'+(row.tool&&row.tool.name||row.text||''));
global._syncToolCallGroupSummary=()=>{{}};
global._transparentToolStatus=()=> 'Running';
global._decorateTransparentEventRow=(node)=>{{node.label='transparent:'+node.label;return node;}};
eval(extractFunc('_anchorSceneReportRenderError'));
eval(extractFunc('_anchorSceneFallbackNodeForRenderError'));
eval(extractFunc('_renderAnchorSceneRowsIntoWorklog'));
eval(extractFunc('_anchorSceneTransparentNodeForRow'));

const group={{list:new FakeNode('list')}};
const rows=[
  {{row_id:'patch-1',role:'tool',source_event_type:'tool_complete',mutation:true,tool:{{name:'patch'}}}},
  {{row_id:'read-1',role:'tool',source_event_type:'tool',tool:{{name:'read_file'}}}},
  {{row_id:'thinking-1',role:'thinking',source_event_type:'activity_status',text:'Thinking'}},
  {{row_id:'search-1',role:'tool',source_event_type:'tool',tool:{{name:'search'}}}},
];
const rendered=_renderAnchorSceneRowsIntoWorklog(group,rows,{{live:true}});
const transparentNode=_anchorSceneTransparentNodeForRow(rows[0],{{live:true,settled:false,streamId:'stream-1',sessionId:'sid-1'}});
function flatten(node,out=[]){{
  if(node.label&&node.label!=='list'&&node.label!=='tool-group') out.push(node.label);
  for(const child of node.children||[]) flatten(child,out);
  return out;
}}
const fallback=group.list.children[0].children[0];
process.stdout.write(JSON.stringify({{
  rendered,
  labels:flatten(group.list),
  fallback:fallback.getAttribute('data-anchor-render-fallback'),
  laterSearchVisible:flatten(group.list).includes('tool:search'),
  transparentLabel:transparentNode&&transparentNode.label,
  transparentFallback:transparentNode&&transparentNode.getAttribute('data-anchor-render-fallback'),
}}));
"""
    result = _run_node(script)

    assert result == {
        "rendered": True,
        "labels": ["tool:patch", "tool:read_file", "thinking:Thinking", "tool:search"],
        "fallback": "1",
        "laterSearchVisible": True,
        "transparentLabel": "transparent:tool:patch",
        "transparentFallback": "1",
    }


def test_compact_worklog_renderer_is_fail_soft_per_activity_row():
    source = UI_JS.read_text(encoding="utf-8")
    start = source.index("function _renderAnchorSceneRowsIntoWorklog")
    end = source.index("function _liveProcessedWorklogAnchorScore", start)
    body = source[start:end]

    assert "for(const row of rows)" in body
    assert "catch(error)" in body
    assert "_anchorSceneFallbackNodeForRenderError(row,opts,error)" in body
    assert body.index("catch(error)") < body.index("if(!node) continue;")
