"""Behavior coverage for simulated character streaming in live mutation DiffCards."""

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


def test_live_diff_types_characters_progressively_and_respects_manual_scroll():
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(UI_JS))},'utf8');
const start=src.indexOf('const _assistantDiffStreamAnimations');
const end=src.indexOf('function _assistantMutationEventKeyFromToolCall',start);
if(start<0||end<0) throw new Error('diff stream animation helpers not found');

class Classes{{
  constructor(names){{this.names=new Set(names||[]);}}
  contains(name){{return this.names.has(name);}}
  add(name){{this.names.add(name);}}
  remove(name){{this.names.delete(name);}}
}}
class Code{{
  constructor(text){{this.textContent=text;this.animationCalls=0;}}
  animate(){{this.animationCalls++;return {{cancel(){{}}}};}}
}}
class Gutter{{
  constructor(text){{this.textContent=text;}}
}}
class Style{{
  constructor(){{this.values={{}};}}
  setProperty(name,value){{this.values[name]=String(value);}}
}}
class CodeContainer{{
  constructor(rows){{
    this.children=[];
    for(const row of rows)this.appendChild(row);
  }}
  appendChild(row){{
    if(row.parentElement&&row.parentElement!==this)row.parentElement.removeChild(row);
    if(!this.children.includes(row))this.children.push(row);
    row.parentElement=this;
    return row;
  }}
  removeChild(row){{
    const index=this.children.indexOf(row);
    if(index>=0)this.children.splice(index,1);
    if(row.parentElement===this)row.parentElement=null;
    return row;
  }}
}}
class Counter{{
  constructor(kind,value){{
    this.kind=kind;
    this.textContent=(kind==='added'?'+':'-')+value;
    this.classList=new Classes([`assistant-modified-file-stat-${{kind}}`]);
    this.animationCalls=0;
  }}
  animate(){{this.animationCalls++;return {{cancel(){{}}}};}}
}}
class Row{{
  constructor(index){{
    this.hidden=false;
    const text=index===0?'--- a/src/live.js'
      :index===1?'@@ -10,2 +10,3 @@'
      :index===2?'39 unmodified lines'
      :index===3?'x'.repeat(2000)
      :index===5?'@@ -1000,21 +2000,17 @@'
      :`line-${{index}}-content-abcdefghijk`;
    this.code=new Code(text);
    this.oldGutter=new Gutter(index>=6?String(1000+index):String(10+index));
    this.newGutter=new Gutter(index>=6?String(2000+index):String(10+index));
    this.parentElement=null;
    this.classList=new Classes([
      'assistant-code-diff-line',
      ...(index===0?['assistant-code-diff-meta']:[]),
      ...([1,5].includes(index)?['assistant-code-diff-hunk']:[]),
      ...(index===2?['assistant-code-diff-gap']:[]),
      ...(index%2?['assistant-code-diff-added']:[]),
    ]);
  }}
  get textContent(){{return (this.classList.contains('assistant-code-diff-added')?'+':' ')+this.code.textContent;}}
  querySelector(selector){{
    if(selector==='.assistant-code-diff-code')return this.code;
    if(selector==='.assistant-code-diff-old')return this.oldGutter;
    if(selector==='.assistant-code-diff-new')return this.newGutter;
    return null;
  }}
  remove(){{if(this.parentElement)this.parentElement.removeChild(this);}}
}}
class Owner{{
  constructor(){{this.attrs={{'data-mutation-event-key':'patch-1'}};}}
  getAttribute(name){{return this.attrs[name]||null;}}
}}
class Card{{
  constructor(owner){{
    this.owner=owner;this.attrs={{'data-modified-file-path':'src/live.js'}};this.pres=[];
    this.stats=[new Counter('added',12),new Counter('removed',7)];
  }}
  getAttribute(name){{return this.attrs[name]||null;}}
  querySelectorAll(selector){{
    if(selector==='.assistant-code-diff[data-diff-stream="1"]')return this.pres;
    if(selector==='.assistant-modified-file-stat-added,.assistant-modified-file-stat-removed')return this.stats;
    return [];
  }}
  closest(selector){{return selector.includes('data-mutation-event-key')?this.owner:null;}}
}}
class Pre{{
  constructor(card,count){{
    this.card=card;
    this.rows=Array.from({{length:count}},(_,i)=>new Row(i));
    this.code=new CodeContainer(this.rows);
    this.attrs={{'data-diff-stream':'1'}};
    this.style=new Style();
    this.classList=new Classes(['assistant-code-diff']);
    this.scrollTop=0;this.scrollLeft=0;this.clientHeight=48;this.hidden=false;
    this.listeners={{}};
  }}
  get scrollHeight(){{return this.code.children.length*16;}}
  getAttribute(name){{return Object.prototype.hasOwnProperty.call(this.attrs,name)?this.attrs[name]:null;}}
  setAttribute(name,value){{this.attrs[name]=String(value);}}
  removeAttribute(name){{delete this.attrs[name];}}
  querySelectorAll(selector){{return selector==='.assistant-code-diff-line'?this.code.children.slice():[];}}
  closest(selector){{
    if(selector==='.assistant-modified-file-card') return this.card;
    if(selector.includes('data-mutation-event-key')) return this.card.owner;
    return null;
  }}
  addEventListener(name,fn){{this.listeners[name]=fn;}}
  removeEventListener(name,fn){{if(this.listeners[name]===fn)delete this.listeners[name];}}
  userScroll(top){{this.scrollTop=top;if(this.listeners.scroll)this.listeners.scroll();}}
}}

let now=0;
let nextFrameId=1;
const frames=[];
global.performance={{now:()=>now}};
global.requestAnimationFrame=(cb)=>{{frames.push({{id:nextFrameId,cb}});return nextFrameId++;}};
global.cancelAnimationFrame=(id)=>{{const hit=frames.find(frame=>frame.id===id);if(hit)hit.cancelled=true;}};
global.setTimeout=(cb)=>{{frames.push({{id:nextFrameId,cb}});return nextFrameId++;}};
global.clearTimeout=global.cancelAnimationFrame;
let reducedMotion=false;
global.window={{matchMedia:()=>({{matches:reducedMotion}})}};
global.document={{hidden:false}};
let outerScrollCalls=0;
global.scrollIfPinned=()=>{{outerScrollCalls++;}};
function runFrame(ts){{
  now=ts;
  const frame=frames.shift();
  if(frame&&!frame.cancelled) frame.cb(ts);
}}

eval(src.slice(start,end));
function typedCharacters(target){{
  return target.rows.reduce((total,row)=>total+(row.parentElement===target.code?row.code.textContent.length:0),0);
}}
function revealedUnits(target){{return Number(target.getAttribute('data-diff-stream-revealed')||0);}}
function hasPartialLine(target){{
  return target.rows.some(row=>row.parentElement===target.code&&!row.classList.contains('assistant-code-diff-hunk')&&row.code.textContent.length>0&&row.code.textContent.length<row.code._expectedText.length);
}}
function counterValue(counter){{return Number(String(counter.textContent).replace(/^[+-]/,''));}}
const owner=new Owner();
const card=new Card(owner);
let pre=new Pre(card,40);
for(const row of pre.rows) row.code._expectedText=row.code.textContent;
card.pres=[pre];
const expectedTotal=_assistantDiffStreamLineSet(pre).total;
const expectedRenderedTotal=pre.rows.reduce((total,row)=>total+row.code._expectedText.length,0);
const root={{
  querySelectorAll:(selector)=>selector==='.assistant-modified-file-card'?[card]:[],
  contains:(node)=>node===owner,
}};

_activateAssistantDiffStreaming(root);
const initial=revealedUnits(pre);
const initialText=pre.rows[2].code.textContent;
const initialMeta=pre.rows[0].code.textContent;
const initialHunk=pre.rows[1].code.textContent;
const initialAdded=counterValue(card.stats[0]);
const initialRemoved=counterValue(card.stats[1]);
const initialGap=pre.rows[2].code.textContent;
const initialMountedRows=pre.code.children.length;
const initialSecondHunkMounted=pre.rows[5].parentElement===pre.code;
const initialOldDigits=pre.getAttribute('data-diff-old-digits');
runFrame(40);
const earlyHunk=pre.rows[1].code.textContent;
runFrame(413);
const midwayGap=pre.rows[2].code.textContent;
const midway=revealedUnits(pre);
const midwayHasPartialLine=hasPartialLine(pre);
const midwayAdded=counterValue(card.stats[0]);
const midwayRemoved=counterValue(card.stats[1]);
const gapAnimationCallsBeforeReplacement=pre.rows[2].code.animationCalls;
const followedTop=pre.scrollTop;
const midwayMountedRows=pre.code.children.length;

const replacement=new Pre(card,40);
for(const row of replacement.rows) row.code._expectedText=row.code.textContent;
card.stats=[new Counter('added',12),new Counter('removed',7)];
replacement.scrollLeft=27;
card.pres=[replacement];
_activateAssistantDiffStreaming(root);
const replacementRevealed=revealedUnits(replacement);
const replacementAdded=counterValue(card.stats[0]);
const replacementRemoved=counterValue(card.stats[1]);
const replacementScrollLeft=replacement.scrollLeft;
pre=replacement;
const originalHighlight=pre.rows[3].classList.contains('assistant-code-diff-added');

pre.userScroll(0);
runFrame(820);
const manualTopAfterMoreRows=pre.scrollTop;
runFrame(2000);
runFrame(5000);
const finalMountedRows=pre.code.children.length;
const finalOldDigits=pre.getAttribute('data-diff-old-digits');
const finalNewDigits=pre.getAttribute('data-diff-new-digits');
const finalAdded=counterValue(card.stats[0]);
const finalRemoved=counterValue(card.stats[1]);
const finalGap=pre.rows[2].code.textContent;
const counterAnimationCalls=card.stats[0].animationCalls+card.stats[1].animationCalls;
const gapAnimationCalls=gapAnimationCallsBeforeReplacement+pre.rows[2].code.animationCalls;
card.pres=[];
_activateAssistantDiffStreaming(root);
const afterShrink=new Pre(card,40);
for(const row of afterShrink.rows) row.code._expectedText=row.code.textContent;
card.pres=[afterShrink];
_activateAssistantDiffStreaming(root);
const afterShrinkRevealed=revealedUnits(afterShrink);
_resetAssistantDiffStreaming();
const afterReset=new Pre(card,40);
for(const row of afterReset.rows) row.code._expectedText=row.code.textContent;
card.pres=[afterReset];
_activateAssistantDiffStreaming(root);
const afterResetRevealed=revealedUnits(afterReset);
_resetAssistantDiffStreaming();
reducedMotion=true;
card.stats=[new Counter('added',12),new Counter('removed',7)];
const reduced=new Pre(card,40);
for(const row of reduced.rows) row.code._expectedText=row.code.textContent;
card.pres=[reduced];
_activateAssistantDiffStreaming(root);
const reducedMotionRevealed=typedCharacters(reduced);
const reducedMotionBusy=reduced.getAttribute('aria-busy');
const reducedMotionAdded=counterValue(card.stats[0]);
const reducedMotionRemoved=counterValue(card.stats[1]);
const reducedMotionAnimations=card.stats[0].animationCalls+card.stats[1].animationCalls+reduced.rows[2].code.animationCalls;

reducedMotion=false;
const visibilityOwner=new Owner();
const visibilityCard=new Card(visibilityOwner);
visibilityCard.attrs['data-modified-file-path']='src/visibility.js';
const visibilityPre=new Pre(visibilityCard,12);
visibilityCard.pres=[visibilityPre];
const visibilityTotal=_assistantDiffStreamLineSet(visibilityPre).total;
const visibilityRoot={{
  querySelectorAll:(selector)=>selector==='.assistant-modified-file-card'?[visibilityCard]:[],
  contains:(node)=>node===visibilityOwner,
}};
_activateAssistantDiffStreaming(visibilityRoot);
const visibilityInitial=revealedUnits(visibilityPre);
_finishAssistantDiffStreamingForVisibilityExit();
const visibilityFinished=revealedUnits(visibilityPre);
const visibilityRemountOwner=new Owner();
const visibilityRemountCard=new Card(visibilityRemountOwner);
visibilityRemountCard.attrs['data-modified-file-path']='src/visibility.js';
const visibilityRemountPre=new Pre(visibilityRemountCard,12);
visibilityRemountCard.pres=[visibilityRemountPre];
const visibilityRemountRoot={{
  querySelectorAll:(selector)=>selector==='.assistant-modified-file-card'?[visibilityRemountCard]:[],
  contains:(node)=>node===visibilityRemountOwner,
}};
_activateAssistantDiffStreaming(visibilityRemountRoot);
const visibilityRemountRevealed=revealedUnits(visibilityRemountPre);

const remountOwner=new Owner();
const remountCard=new Card(remountOwner);
remountCard.attrs['data-modified-file-path']='src/remount.js';
const remountPre=new Pre(remountCard,14);
remountCard.pres=[remountPre];
const remountTotal=_assistantDiffStreamLineSet(remountPre).total;
let remountRoot={{
  querySelectorAll:(selector)=>selector==='.assistant-modified-file-card'?[remountCard]:[],
  contains:(node)=>node===remountOwner,
}};
_activateAssistantDiffStreaming(remountRoot);
const remountInitial=revealedUnits(remountPre);
const replacementOwner=new Owner();
const replacementCard=new Card(replacementOwner);
replacementCard.attrs['data-modified-file-path']='src/remount.js';
const remountedPre=new Pre(replacementCard,14);
replacementCard.pres=[remountedPre];
remountRoot={{
  querySelectorAll:(selector)=>selector==='.assistant-modified-file-card'?[replacementCard]:[],
  contains:(node)=>node===replacementOwner,
}};
_activateAssistantDiffStreaming(remountRoot);
const remountedRevealed=revealedUnits(remountedPre);

process.stdout.write(JSON.stringify({{
  initial,
  initialText,
  initialMeta,
  initialHunk,
  initialAdded,
  initialRemoved,
  initialGap,
  initialMountedRows,
  initialSecondHunkMounted,
  initialOldDigits,
  earlyHunk,
  midwayGap,
  midway,
  midwayHasPartialLine,
  midwayAdded,
  midwayRemoved,
  midwayMountedRows,
  final:typedCharacters(pre),
  expectedTotal,
  expectedRenderedTotal,
  followedTop,
  replacementRevealed,
  replacementAdded,
  replacementRemoved,
  replacementScrollLeft,
  manualTopAfterMoreRows,
  busy:pre.getAttribute('aria-busy'),
  complete:pre.getAttribute('data-diff-stream-complete'),
  originalHighlight,
  outerScrollCalls,
  afterShrinkRevealed,
  afterResetRevealed,
  reducedMotionRevealed,
  reducedMotionBusy,
  reducedMotionAdded,
  reducedMotionRemoved,
  reducedMotionAnimations,
  finalAdded,
  finalRemoved,
  finalGap,
  finalMountedRows,
  finalOldDigits,
  finalNewDigits,
  counterAnimationCalls,
  gapAnimationCalls,
  visibilityInitial,
  visibilityTotal,
  visibilityFinished,
  visibilityRemountRevealed,
  remountInitial,
  remountTotal,
  remountedRevealed,
}}));
"""
    result = _run_node(script)

    assert result["initial"] == 1
    assert result["initialText"] == ""
    assert result["initialMeta"] == "--- a/src/live.js"
    assert result["initialHunk"].startswith("@@ -")
    assert result["initialHunk"] != "@@ -10,2 +10,3 @@"
    assert result["earlyHunk"] != result["initialHunk"]
    assert result["initialSecondHunkMounted"] is False
    assert 0 < result["initialMountedRows"] < result["midwayMountedRows"]
    assert result["midwayMountedRows"] < result["finalMountedRows"]
    assert result["finalMountedRows"] == 40
    assert result["initialOldDigits"] == "3"
    assert result["finalOldDigits"] == "4"
    assert result["finalNewDigits"] == "4"
    assert result["initialAdded"] == 0
    assert result["initialRemoved"] == 0
    assert result["initialGap"] == ""
    assert 1 <= int(result["midwayGap"].split()[0]) <= 39
    assert 1 < result["midway"] < result["expectedTotal"]
    assert result["midwayHasPartialLine"] is True
    assert 0 < result["midwayAdded"] < 12
    assert 0 < result["midwayRemoved"] < 7
    assert result["final"] == result["expectedRenderedTotal"]
    assert result["expectedTotal"] < result["expectedRenderedTotal"]
    assert result["followedTop"] > 0
    assert result["replacementRevealed"] == result["midway"]
    assert result["replacementAdded"] == result["midwayAdded"]
    assert result["replacementRemoved"] == result["midwayRemoved"]
    assert result["replacementScrollLeft"] == 27
    assert result["manualTopAfterMoreRows"] == 0
    assert result["busy"] is None
    assert result["complete"] == "1"
    assert result["originalHighlight"] is True
    assert result["outerScrollCalls"] > 0
    assert result["afterShrinkRevealed"] == result["expectedTotal"]
    assert result["afterResetRevealed"] == result["expectedTotal"]
    assert result["reducedMotionRevealed"] == result["expectedRenderedTotal"]
    assert result["reducedMotionBusy"] is None
    assert result["reducedMotionAdded"] == 12
    assert result["reducedMotionRemoved"] == 7
    assert result["reducedMotionAnimations"] == 0
    assert result["finalAdded"] == 12
    assert result["finalRemoved"] == 7
    assert result["finalGap"] == "39 unmodified lines"
    assert result["counterAnimationCalls"] > 0
    assert result["gapAnimationCalls"] > 0
    assert result["visibilityInitial"] == 1
    assert result["visibilityFinished"] == result["visibilityTotal"]
    assert result["visibilityRemountRevealed"] == result["visibilityTotal"]
    assert result["remountInitial"] == 1
    assert result["remountedRevealed"] == result["remountTotal"]
