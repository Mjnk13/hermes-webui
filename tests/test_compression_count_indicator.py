"""Compression count must survive backend hydration and reach the context meter."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api.gateway_chat import _gateway_stream_usage
from api.models import Session
from api.routes import _session_compression_count_for_display


ROOT = Path(__file__).resolve().parents[1]
UI_JS = ROOT / "static" / "ui.js"
STREAMING_PY = ROOT / "api" / "streaming.py"
NODE = shutil.which("node")


def test_session_compact_exposes_persisted_compression_count(tmp_path, monkeypatch):
    import api.models as models

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    session = Session(
        session_id="compression-count",
        workspace=str(tmp_path),
        compression_count=3,
    )

    assert session.compression_count == 3
    assert session.compact()["compression_count"] == 3
    session.save()
    loaded = Session.load("compression-count")
    assert loaded is not None
    assert loaded.compression_count == 3


def test_gateway_usage_normalizes_cli_compressions_field():
    usage = _gateway_stream_usage({
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "context_max": 4096,
            "context_used": 1200,
            "compressions": 4,
        }
    })

    assert usage["context_length"] == 4096
    assert usage["last_prompt_tokens"] == 1200
    assert usage["compression_count"] == 4


def test_legacy_session_count_is_backfilled_from_compression_snapshot_lineage(tmp_path, monkeypatch):
    import api.models as models

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    root = Session(
        session_id="root-snapshot",
        workspace=str(tmp_path),
        pre_compression_snapshot=True,
    )
    root.save()
    second = Session(
        session_id="second-snapshot",
        workspace=str(tmp_path),
        pre_compression_snapshot=True,
        parent_session_id=root.session_id,
    )
    second.save()
    tip = Session(
        session_id="visible-tip",
        workspace=str(tmp_path),
        parent_session_id=second.session_id,
    )

    assert tip.compression_count == 0
    assert _session_compression_count_for_display(tip) == 2


def test_legacy_manual_anchor_backfills_at_least_one_compression():
    session = Session(
        session_id="legacy-manual-compression",
        compression_count=0,
        compression_anchor_mode="manual",
    )

    assert _session_compression_count_for_display(session) == 1


def test_direct_stream_usage_reads_and_persists_compressor_count():
    source = STREAMING_PY.read_text(encoding="utf-8")

    assert "_usage['compression_count'] = getattr(_cc, 'compression_count', 0) or 0" in source
    assert "s.compression_count = max(" in source
    assert "usage['compression_count'] = getattr(_cc, 'compression_count', 0) or 0" in source


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_context_indicator_shows_compression_count_and_preserves_it_across_usage_merges():
    script = f"""
const fs=require('fs');
const source=fs.readFileSync({json.dumps(str(UI_JS))},'utf8');
function block(name,next){{
  const start=source.indexOf(`function ${{name}}`);
  const end=source.indexOf(next,start);
  if(start<0||end<0)throw new Error(`${{name}} block missing`);
  return source.slice(start,end);
}}
class Classes{{
  constructor(){{this.values=new Set();}}
  toggle(name,on){{if(on)this.values.add(name);else this.values.delete(name);}}
  remove(name){{this.values.delete(name);}}
}}
class Node{{
  constructor(){{this.textContent='';this.style={{}};this.attrs={{}};this.classList=new Classes();}}
  setAttribute(name,value){{this.attrs[name]=String(value);}}
  removeAttribute(name){{delete this.attrs[name];}}
}}
const ids=['ctxIndicatorWrap','ctxIndicator','ctxRingValue','ctxPercent','ctxCompressionCount','ctxTooltipUsage','ctxTooltipTokens','ctxTooltipThreshold','ctxTooltipCompressions','ctxTooltipCost','ctxTooltipCompress','ctxCompressBtn'];
const nodes=Object.fromEntries(ids.map(id=>[id,new Node()]));
global.window={{_composerControlVisibility:{{}}}};
global.S={{session:{{compression_count:0}}}};
global.$=(id)=>nodes[id]||null;
global.t=(key)=>key;
global._fmtTokens=(value)=>String(value);
global._setCtxCompressButton=()=>{{}};
let mobileState=null;
global._syncMobileCtxDisplay=(state)=>{{mobileState=state;}};
eval(block('_mergeUsageForCtxIndicator','// Context usage indicator'));
eval(block('_syncCtxIndicator','// ── Touch support'));

const merged=_mergeUsageForCtxIndicator(
  {{last_prompt_tokens:1200,context_length:4096}},
  {{compression_count:4}}
);
const staleMerged=_mergeUsageForCtxIndicator(
  {{compression_count:1}},
  {{compression_count:4}}
);
_syncCtxIndicator(merged);
const shown={{
  count:merged.compression_count,
  badge:nodes.ctxCompressionCount.textContent,
  badgeDisplay:nodes.ctxCompressionCount.style.display,
  tooltip:nodes.ctxTooltipCompressions.textContent,
  tooltipDisplay:nodes.ctxTooltipCompressions.style.display,
  mobile:mobileState.compressionText,
  sessionCount:S.session.compression_count,
}};
S.session.compression_count=0;
_syncCtxIndicator({{last_prompt_tokens:1200,context_length:4096,compression_count:0}});
const hidden={{
  badgeDisplay:nodes.ctxCompressionCount.style.display,
  tooltipDisplay:nodes.ctxTooltipCompressions.style.display,
  mobile:mobileState.compressionText,
}};
S.session.compression_count=6;
_syncCtxIndicator({{last_prompt_tokens:1200,context_length:4096,compression_count:0}});
const staleUsage={{
  badge:nodes.ctxCompressionCount.textContent,
  tooltip:nodes.ctxTooltipCompressions.textContent,
  sessionCount:S.session.compression_count,
}};
process.stdout.write(JSON.stringify({{shown,hidden,staleUsage,staleMergedCount:staleMerged.compression_count}}));
"""
    result = subprocess.run(
        [NODE, "-e", script],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["staleMergedCount"] == 4

    assert payload["shown"] == {
        "count": 4,
        "badge": "🗜️ 4",
        "badgeDisplay": "",
        "tooltip": "Compressions: 4",
        "tooltipDisplay": "",
        "mobile": "Compressions: 4",
        "sessionCount": 4,
    }
    assert payload["hidden"] == {
        "badgeDisplay": "none",
        "tooltipDisplay": "none",
        "mobile": "",
    }
    assert payload["staleUsage"] == {
        "badge": "🗜️ 6",
        "tooltip": "Compressions: 6",
        "sessionCount": 6,
    }
