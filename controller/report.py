"""Local, read-only decision review. No API calls or gameplay policy here."""
from collections import Counter
from html import escape
import json
from pathlib import Path


def read_decisions(run_path):
    run_path = Path(run_path)
    manifest = json.loads((run_path / "manifest.json").read_text())
    decisions = {}
    skipped = 0
    with (run_path / "events.jsonl").open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            kind = event.get("event")
            if kind not in {"request", "result", "applied", "discarded"}:
                continue
            payload = event.get("job", {}) if kind == "request" else event.get("result", {}) if kind == "result" else event
            request_id = payload.get("request_id")
            if request_id is None:
                continue
            row = decisions.setdefault(request_id, {"id": request_id, "disposition": "no application recorded"})
            if kind == "request":
                row["snapshot"] = payload.get("snapshot", {})
            elif kind == "result":
                row["result"] = payload
                row.setdefault("snapshot", payload.get("snapshot", {}))
            else:
                row["disposition"] = "applied" if kind == "applied" else "discarded: " + str(event.get("reason", "unknown"))
                row["execution"] = event
    return manifest, list(decisions.values()), skipped


def _json(value):
    return escape(json.dumps(value, indent=2, ensure_ascii=False))


def _label(value):
    return escape(str(value if value is not None else "unavailable").replace("_", " "))


def write_report(run_path):
    """Write a self-contained page; tolerate old runs without diagnostics."""
    run_path = Path(run_path)
    manifest, decisions, skipped = read_decisions(run_path)
    counts = Counter(row.get("execution", {}).get("action") for row in decisions if row["disposition"] == "applied")
    counts.pop(None, None)
    rows = []
    for row in decisions:
        result = row.get("result", {})
        snapshot = row.get("snapshot", {})
        state = snapshot.get("state", {})
        diagnostics = result.get("diagnostics") or {}
        duration = None
        if result.get("started") is not None and result.get("received") is not None:
            duration = round((result["received"] - result["started"]) * 1000)
        selected = _label(result.get("action")) + " / " + _label(result.get("strength"))
        alternatives = sorted((result.get("probabilities") or {}).items(), key=lambda item: item[1], reverse=True)[:3]
        details = {"diagnostics": diagnostics or "Unavailable (older run, mock, or missing answer)",
                   "diagnostic_errors": result.get("diagnostic_errors"),
                   "action_confidence": result.get("confidence"),
                   "action_probabilities": result.get("probabilities"),
                   "strength_confidence": result.get("strength_confidence"),
                   "strength_probabilities": result.get("strength_probabilities"),
                   "response_outcome": result.get("outcome"), "error": result.get("error"),
                   "model": result.get("model"),
                   "input_tokens": result.get("input_tokens"), "output_tokens": result.get("output_tokens"),
                   "execution_record": row.get("execution")}
        rows.append(f'''<tr><td>#{_label(row['id'])}<small>Frame {_label(snapshot.get('frame'))}</small></td>
<td>{_label(state.get('opponent', {}).get('character'))}<small>Distance {_label(state.get('distance'))}</small></td>
<td>{_label(diagnostics.get('main_threat', {}).get('choice'))}<small>Opening: {_label(diagnostics.get('opening', {}).get('choice'))}</small></td>
<td>{selected}<small>{_label(row['disposition'])} · {_label(duration)} ms</small>
<details><summary>State, answers and execution</summary><h3>Exact state supplied</h3><pre>{_json(state)}</pre>
<h3>Answers and execution</h3><pre>{_json(details)}</pre></details>
<small>Top choices: {escape(', '.join(f'{name}: {value:.0%}' for name, value in alternatives))}</small></td></tr>''')
    status = "Completed run" if manifest.get("complete") else "Incomplete run: records may be missing"
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jev decision review</title><style>
body{{font:16px system-ui,sans-serif;margin:32px auto;padding:0 20px;max-width:1250px;background:#f5f7fa;color:#142238}}
h1{{margin-bottom:8px}}p{{line-height:1.5}}small{{display:block;color:#485970;margin-top:6px}}
table{{border-collapse:collapse;width:100%;background:white}}th,td{{text-align:left;vertical-align:top;padding:14px;border-bottom:1px solid #dbe2ea}}
th{{background:#e7eef7}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:450px;overflow:auto;background:#edf1f5;padding:14px}}
summary{{cursor:pointer;color:#174c92;margin:12px 0}}input{{font:inherit;padding:10px;width:min(90%,500px);margin:10px 0 20px}}
details{{max-width:650px}}@media(max-width:700px){{body{{margin:16px auto}}th,td{{padding:8px;font-size:13px}}}}
</style><h1>Jev decision review</h1>
<p>{_label(manifest.get('run_id', run_path.name))} · Mode: {_label(manifest.get('mode'))} · {status}<br>
Prompt: {_label(manifest.get('question_version'))} · {len(decisions)} requests · {sum(counts.values())} applied</p>
<p>Threat and opening are independent Jev classifications of the same snapshot. They do not explain its internal reasoning, feed the action answer, or override it. Probabilities compare choices; they are not chances of winning or landing a hit. Applied means controller inputs were started, not that the move connected.</p>
<p>Applied actions: {escape(', '.join(f'{name} {count}' for name, count in counts.most_common()) or 'none')}.</p>
<details><summary>Exact questions sent with each request</summary><pre>{_json(manifest.get('questions', {'next_action': manifest.get('question')}))}</pre></details>
<label for="filter">Filter decisions</label><br><input id="filter" placeholder="Try sweep, Ken, discarded, or incoming projectile">
<table><thead><tr><th>Request</th><th>Situation</th><th>Jev's assessment</th><th>Choice and execution</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p>{skipped} unreadable event lines skipped. No new model calls were made to generate this report.</p>
<script>document.getElementById('filter').addEventListener('input', function() {{
const query = this.value.toLowerCase(); document.querySelectorAll('tbody tr').forEach(row => {{
row.hidden = !row.textContent.toLowerCase().includes(query); }}); }});</script></html>'''
    path = run_path / "decisions.html"
    temporary = path.with_suffix(".html.tmp")
    temporary.write_text(page)
    temporary.replace(path)
    return path


def latest_completed_play(runs_path):
    for path in sorted(Path(runs_path).glob("*/manifest.json"), reverse=True):
        try:
            manifest = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if manifest.get("mode") == "play" and manifest.get("complete") and (path.parent / "events.jsonl").exists():
            return path.parent
    raise ValueError("No completed Play session found. Close the game to finish its records first.")
