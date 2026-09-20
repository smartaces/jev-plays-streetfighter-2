"use strict";
const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value ?? "—"; };
const label = value => value == null ? "unavailable" : String(value).replaceAll("_", " ");
const num = value => typeof value === "number" && Number.isFinite(value);
const show = value => num(value) ? Math.round(value).toLocaleString() : "—";
const ms = value => num(value) ? `${Math.round(value)} ms` : "—";
const json = value => JSON.stringify(value ?? "Unavailable in this recording", null, 2);
const characters = ["Ryu", "E. Honda", "Blanka", "Guile", "Ken", "Chun-Li", "Zangief", "Dhalsim", "M. Bison", "Sagat", "Balrog", "Vega"];
const statuses = {512:"neutral or walking",514:"crouching",516:"jump motion",520:"guard posture",522:"attack motion",524:"special motion",526:"hit or block reaction"};
let live = null, replay = null, runs = [], selected = null, stateView = "snapshot", allChoices = false;
let playing = false, position = 0, previousTick = performance.now(), token = null, pendingCommand = null, other = null;
let connectedAt = 0, loadRevision = 0, compareRevision = 0, lastInspector = "", commandMessage = "";

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
function isLive() { return $("run").value === "live"; }
function normalise(raw) {
  if (!raw) return {};
  if (raw.player) return raw;
  const fighter = who => ({character:characters[raw[who+"_character_id"]] || "Unknown", health:raw[who==="player"?"health":"enemy_health"], full_health:176,
    x:raw[who+"_x"],y:raw[who+"_y"],state:statuses[raw[who+"_status"]] || "unknown",
    ground_action_ready:who==="player" && statuses[raw.player_status] && num(raw.player_y)?raw.player_y===192 && [512,514].includes(raw.player_status):null});
  const projectile = who => ({active:raw[who+"_projectile_flags"]===257?true:[0,256].includes(raw[who+"_projectile_flags"])?false:null, x:raw[who+"_projectile_x"], y:raw[who+"_projectile_y"],vx_per_frame:raw[who+"_projectile_vx"]/256});
  return {player:fighter("player"),opponent:fighter("opponent"),distance:Math.abs(raw.player_x-raw.opponent_x),projectiles:{player:projectile("player"),opponent:projectile("opponent")}};
}
function atOrBefore(items, time, key = item => item.time) {
  let low=0,high=items.length;
  while(low<high){const mid=(low+high)>>1;if(key(items[mid])<=time)low=mid+1;else high=mid;}
  return low ? items[low-1] : undefined;
}
function replayTime() { return (replay?.frames[0]?.time || 0)+position; }
function currentFrame() { return atOrBefore(replay?.frames || [],replayTime()); }
function rowTime(row) { return row.snapshot?.captured ?? 0; }
function getRow() {
  if(isLive()) return [...(live?.rows || [])].reverse().find(row=>row.result) || live?.rows?.at(-1);
  return selected || atOrBefore(replay?.rows || [],replayTime(),rowTime);
}
function make(tag, content, className) { const node=document.createElement(tag);if(content!=null)node.textContent=content;if(className)node.className=className;return node; }
function health(prefix, fighter) {
  text(prefix+"-name",fighter.character || "Unknown");
  text(prefix+"-hp",num(fighter.health)?`${Math.max(0,fighter.health)} / ${fighter.full_health || 176}`:"—");
  $(prefix+"-bar").style.width=num(fighter.health)?`${Math.max(0,Math.min(100,fighter.health/(fighter.full_health||176)*100))}%`:"0%";
  const body=fighter.body_position || (num(fighter.y)?fighter.y<192?"airborne":fighter.y===192?"grounded":"unknown":"unknown");
  text(prefix+"-state",`${label(body)} · ${label(fighter.state)}`);
}
function drawArena(state) {
  const c=$("arena"), ctx=c.getContext("2d"), w=c.width,h=c.height;
  ctx.clearRect(0,0,w,h);ctx.strokeStyle="#20303c";ctx.lineWidth=1;
  for(let x=30;x<w;x+=50){ctx.beginPath();ctx.moveTo(x,25);ctx.lineTo(x,h-40);ctx.stroke();}
  for(let y=40;y<h-30;y+=50){ctx.beginPath();ctx.moveTo(25,y);ctx.lineTo(w-25,y);ctx.stroke();}
  ctx.strokeStyle="#49606c";ctx.beginPath();ctx.moveTo(25,h-55);ctx.lineTo(w-25,h-55);ctx.stroke();
  const a=state.player || {}, b=state.opponent || {};
  if(!num(a.x)||!num(b.x)){ctx.fillStyle="#a1b2ba";ctx.font="16px system-ui";ctx.textAlign="center";ctx.fillText("Coordinates unavailable in this snapshot",w/2,h/2);return;}
  const points=[a.x,b.x];for(const p of Object.values(state.projectiles || {}))if(p.active && num(p.x))points.push(p.x);
  const centre=(Math.min(...points)+Math.max(...points))/2, span=Math.max(360,Math.max(...points)-Math.min(...points)+120);
  const x=v=>50+(v-(centre-span/2))/span*(w-100),y=v=>h-55-Math.min(200,Math.max(0,192-v));
  ctx.setLineDash([5,5]);ctx.strokeStyle="#738477";ctx.beginPath();ctx.moveTo(x(a.x),h-24);ctx.lineTo(x(b.x),h-24);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle="#a5bcb1";ctx.font="14px monospace";ctx.textAlign="center";ctx.fillText(`${show(state.distance)} horizontal units`,w/2,h-8);
  for(const [f,color,side] of [[a,"#b8e976",-1],[b,"#80bbef",1]]) {
    const px=x(f.x),py=y(num(f.y)?f.y:192),offset=Math.abs(x(a.x)-x(b.x))<70?side*36:0;
    ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(px,h-55);ctx.lineTo(px,py);ctx.stroke();
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(px,py-16,12,0,Math.PI*2);ctx.fill();
    ctx.globalAlpha=.25;ctx.fillRect(px-15,py-2,30,6);ctx.globalAlpha=1;
    ctx.font="bold 18px system-ui";ctx.fillText(f.character || "Unknown",px+offset,py-43);
    ctx.font="12px monospace";ctx.fillText(`x ${show(f.x)} · y ${show(f.y)}`,px+offset,py-64);
    if(num(f.vx_per_frame)&&Math.abs(f.vx_per_frame)>.1){const end=px+Math.sign(f.vx_per_frame)*35;ctx.beginPath();ctx.moveTo(px,py-16);ctx.lineTo(end,py-16);ctx.lineTo(end-Math.sign(f.vx_per_frame)*7,py-22);ctx.stroke();}
  }
  for(const [who,p] of Object.entries(state.projectiles || {})) if(p.active && num(p.x)) {
    const px=x(p.x),py=y(num(p.y)?p.y:150);ctx.fillStyle=who==="player"?"#b8e976":"#80bbef";ctx.beginPath();ctx.ellipse(px,py-10,14,7,0,0,Math.PI*2);ctx.fill();
    ctx.font="12px monospace";ctx.fillText("projectile",px,py-27);
  }
}
function renderProbabilities(row) {
  const values=Object.entries(row?.result?.probabilities || {}).sort((a,b)=>b[1]-a[1]);
  $("probabilities").replaceChildren();
  for(const [name,value] of (allChoices?values:values.slice(0,5))) {
    const card=make("div",null,"probability"+(name===row.result.action?" chosen":""));
    const caption=make("div",null,"prob-label");caption.append(make("span",label(name)),make("span",`${(value*100).toFixed(1)}%`));
    const track=make("div",null,"prob-track"),bar=make("i");bar.style.width=`${Math.max(0,Math.min(100,value*100))}%`;track.append(bar);card.append(caption,track);$("probabilities").append(card);
  }
  if(!values.length)$("probabilities").append(make("p","No probabilities recorded for this response.","muted"));
}
function mix(rows) { const out={};for(const row of rows || [])if(row.disposition==="applied"){const name=row.execution?.action || row.result?.action;if(name)out[name]=(out[name]||0)+1;}return out; }
function compare() {
  const source=isLive()?live:replay, summary=source?.summary || {}, current=mix(source?.rows);
  $("action-mix").replaceChildren();
  if(isLive()){text("comparison","Live action mix covers the latest 12 requests. Open the saved run after stopping for a full comparison.");}
  else if(other){const previous=mix(other.rows),total=Object.values(current).reduce((a,b)=>a+b,0),oldTotal=Object.values(previous).reduce((a,b)=>a+b,0);
    text("comparison",`Current: ${show(summary.active_seconds)}s · ${total} applied · ${source?.rounds?.length || 0} rounds · ${ms(summary.request_latency?.median_ms)} median. Comparison: ${show(other.summary.active_seconds)}s · ${oldTotal} applied · ${other.rounds.length} rounds · ${ms(other.summary.request_latency?.median_ms)} median. Percentages are shares of applied inputs, not success rates.`);
    for(const name of [...new Set([...Object.keys(current),...Object.keys(previous)])].sort((a,b)=>(current[b]||0)-(current[a]||0))) $("action-mix").append(make("span",`${label(name)}: ${total?Math.round((current[name]||0)/total*100):0}% / ${oldTotal?Math.round((previous[name]||0)/oldTotal*100):0}%`));return;
  } else text("comparison","Applied inputs in this session. Choose another run to compare action shares; different opponents and durations affect the result.");
  for(const [name,count] of Object.entries(current).sort((a,b)=>b[1]-a[1]))$("action-mix").append(make("span",`${label(name)} · ${count}`));
}
function render() {
  document.body.classList.toggle("live-session",isLive());
  const source=isLive()?live:replay, row=getRow(), snapshot=row?.snapshot || {}, result=row?.result || {}, manifest=source?.manifest || {}, summary=source?.summary || {};
  const frame=isLive()?null:currentFrame();
  const state=normalise(stateView==="snapshot"?(snapshot.observation || snapshot.state):(isLive()?live?.observation:frame?.state));
  const healthState=isLive()?normalise(live?.observation):state;
  health("p1",healthState.player || {});health("p2",healthState.opponent || {});drawArena(state);
  text("source-badge",`${isLive()?"LIVE":"REPLAY"} / ${String(manifest.mode || "—").toUpperCase()}`);
  const fight=isLive()?live?.episode:stateView==="snapshot"?snapshot.episode:frame?.episode;
  text("episode",`FIGHT ${num(fight)?fight+1:"—"}`);
  text("scene-title",stateView==="snapshot"?"Decision snapshot":"Game state");
  text("scene-stamp",stateView==="snapshot"?(row?`Request #${row.id} · captured frame ${snapshot.frame ?? "—"} · final recorded outcome below`:"Waiting for Jev’s first decision. Resume the game to begin."):`${isLive()?"Current controller":"Recorded"} frame ${isLive()?live?.frame ?? "—":frame?.frame ?? "—"} · decision #${row?.id ?? "—"} has its own earlier snapshot`);
  text("distance",num(state.distance)?`${state.distance} units`:"Unknown");
  const ready=state.player?.ground_action_ready;text("readiness",ready===true?"Ground action ready":state.player?.air_attack_ready_estimate?"Air attack window":ready===false?"Not ground-ready":"Unknown");
  const p=state.projectiles?.opponent;text("projectile",p?.active===false?"None active":p?.active===true?(p.incoming===true?"Incoming":"Active"):"Unknown");
  text("request",`#${row?.id ?? "—"}`);text("action",result.action?label(result.action):"Waiting for a choice");text("strength",label(result.strength));text("disposition",row?.disposition || "No decision");
  text("effective-strength",row?.execution?.strength?`Controller strength: ${label(row.execution.strength)} · applied frame ${row.execution.frame}`:result.error || "");
  const coaching=snapshot.state?.coaching, tactics=snapshot.state?.recent_tactics, opponentProfile=snapshot.state?.opponent_profile;
  text("coach-cue",coaching?label(coaching.cue):"No coaching in this snapshot");
  text("coach-preference",coaching?label(coaching.preference):"—");
  text("coach-hint",coaching?.hint || "This request predates coaching or coaching was disabled.");
  text("coach-pace",tactics?`${tactics.fireball_attempts} fireball attempts / ${tactics.window_seconds}s · ${label(tactics.fireball_pace)} · ${tactics.projectiles_spawned} projectiles observed`:"");
  const approach=tactics?.approach;
  text("coach-approach",approach?`${approach.attempts_since_attack} approaches since the last attack within ${tactics.window_seconds}s · ${approach.distance_closed} distance units closed · ${label(approach.feedback)}`:"");
  text("coach-opponent",opponentProfile?`${opponentProfile.character}: ${opponentProfile.has_projectile===true?"projectile available":opponentProfile.has_projectile===false?"no launched projectile":"projectile capability unknown"} · ${opponentProfile.tip}`:"");
  text("threat",label(result.diagnostics?.main_threat?.choice));text("opening",label(result.diagnostics?.opening?.choice));renderProbabilities(row);
  text("requests",summary.jobs_dispatched ?? source?.rows?.length ?? "—");text("latency",ms(summary.request_latency?.median_ms));text("age",ms(row?.execution?.age_ms));text("elapsed",num(summary.active_seconds)?`${show(summary.active_seconds)}s`:"—");
  text("budget",`Session limits: ${manifest.settings?.duration ?? "—"} active seconds / ${manifest.settings?.max_requests ?? "—"} requests`);
  text("tokens",`Reported tokens: ${show(summary.reported_input_tokens)} in / ${show(summary.reported_output_tokens)} out · ${summary.attempts_without_usage ?? "—"} attempts without usage`);
  text("prompt-version",`${manifest.question_version || "Legacy prompt"} · ${manifest.context_version || "Legacy context"}`);
  const inputView=isLive()?"now":stateView;
  const target=inputView==="snapshot"?(row?.execution?.time ?? result.received ?? snapshot.captured):isLive()?Infinity:replayTime();
  const buttonSource=source?.buttons || [];
  let steps=buttonSource.filter(step=>step.time<=target),held=inputView==="now"&&isLive()?(live?.held || []):(steps.at(-1)?.held || []);
  let inputCaption=isLive()?`Now: ${held.join(" + ") || "released"} · input sequence ${live?.execution?.input_sequence_active?"active":"idle"}`:"Buttons at the replay cursor. Older logs may lack request IDs.";
  if(inputView==="snapshot") {
    const appliedAt=row?.disposition==="applied"?row.execution?.time:null;
    const nextApplied=(source?.rows || []).filter(r=>r.disposition==="applied" && r.execution?.time>appliedAt).map(r=>r.execution.time).sort((a,b)=>a-b)[0] ?? Infinity;
    const linked=buttonSource.filter(step=>step.request_id===row?.id);
    const interval=num(appliedAt)?buttonSource.filter(step=>step.time>=appliedAt && step.time<nextApplied):[];
    steps=linked.length?linked:interval;held=steps[0]?.held || (num(appliedAt)?atOrBefore(buttonSource,appliedAt)?.held || []:[]);
    inputCaption=num(appliedAt)?`First recorded input below; strip shows later input changes ${linked.length?`linked to request #${row.id}`:"in the application interval (older log, no request IDs)"}. The observation above predates these inputs.`:"No applied inputs for this request. A discarded choice does not press buttons.";
  }
  document.querySelectorAll("[data-button]").forEach(node=>node.classList.toggle("held",held.includes(node.dataset.button)));
  text("input-phase",inputView==="now"&&isLive()?label(live?.execution?.phase):inputView==="snapshot"?"recorded input sequence":"recorded buttons");
  text("input-caption",inputCaption);
  $("button-history").replaceChildren();for(const step of (inputView==="snapshot"?steps.slice(0,12):steps.slice(-9).reverse())){const node=make("div",step.held?.join(" + ") || "release","button-step");node.append(make("small",`F${step.frame ?? "—"}${step.request_id?` · #${step.request_id}`:""}`));$("button-history").append(node);}
  $("rounds").replaceChildren();
  for(const round of source?.rounds || []) {
    const won=round.enemy_health<0 && round.health>=0,lost=round.health<0 && round.enemy_health>=0;
    const outcome=won?"Ryu wins · KO":lost?"Ryu loses · KO":`${label(round.reason)} · HP ${round.health ?? "?"}:${round.enemy_health ?? "?"}`;
    const node=make("div",null,"round-row"+(lost?" loss":""));
    node.append(make("span",`Round ${round.round ?? "—"} · ${round.opponent || "Opponent"}`),make("b",outcome));$("rounds").append(node);
  }
  if(!source?.rounds?.length)$("rounds").append(make("p","No completed rounds","muted"));
  const inspectorKey=`${manifest.run_id}/${row?.id}/${row?.disposition}`;
  if(lastInspector!==inspectorKey){lastInspector=inspectorKey;text("exact-state",json(snapshot.state));text("raw-state",json(snapshot.observation || snapshot.state));text("exact-answer",json({result,execution:row?.execution,disposition:row?.disposition}));text("questions",json(manifest.questions || manifest.question));}
  $("snapshot-tab").setAttribute("aria-pressed",stateView==="snapshot");$("now-tab").setAttribute("aria-pressed",stateView==="now");
  for(const id of ["seek","back","play","next","next-round","speed"])$(id).disabled=isLive() || !replay?.frames.length;
  text("play",playing?"Pause replay":"Play replay");text("playback-time",isLive()?"LIVE":`${position.toFixed(1)}s / ${Number($("seek").max).toFixed(1)}s`);$("seek").value=position;
  const fresh=isLive() && live && Date.now()-live.sent_at*1000<3000 && Date.now()-connectedAt<3000 && !live.finished;
  $("pause").disabled=!fresh || live.paused || !!pendingCommand;$("resume").disabled=!fresh || !live.paused || live.can_resume===false || !!pendingCommand;$("stop").disabled=!fresh || !!pendingCommand;
  text("connection",fresh?`${live.mode.toUpperCase()} · ${live.paused?"PAUSED":"CONNECTED"}`:isLive()?"NO LIVE CONNECTION":"LOCAL REPLAY");$("connection").classList.toggle("on",!!fresh);
  text("notice",commandMessage || (isLive()?(fresh?`${live.reason} · Browser interaction does not pause this dashboard session.`:"Controller offline or feed stale. Last received data is retained. Use Dashboard.command to review saved runs."):`${manifest.run_id || "Select a saved session"} · ${manifest.complete?"Completed":"Incomplete / may still be running"} · ${source?.skipped || 0} unreadable event lines · Replay makes no API calls.`));compare();
  if(typeof renderVideo==="function")renderVideo();
}
async function refreshRuns() {
  runs=await api("/api/runs");const value=$("run").value,compareValue=$("compare-run").value;
  $("run").replaceChildren(new Option("Live controller","live"));$("compare-run").replaceChildren(new Option("Choose another run",""));
  for(const run of runs){const title=`${run.id} · ${run.mode || "?"} · ${run.complete?"complete":"incomplete"}`;$("run").add(new Option(title,run.id));$("compare-run").add(new Option(title,run.id));}
  $("run").value=value;$("compare-run").value=compareValue;
}
async function loadRun() {
  const revision=++loadRevision;playing=false;selected=null;commandMessage="";position=0;lastInspector="";
  if(isLive()){render();return;}
  text("notice","Loading saved session…");
  try{const data=await api(`/api/runs/${encodeURIComponent($("run").value)}`);if(revision!==loadRevision)return;replay=data;
    $("seek").max=Math.max(0,(data.frames.at(-1)?.time || 0)-(data.frames[0]?.time || 0));$("seek").step="0.05";
    position=Math.max(0,(data.rows[0]?.snapshot?.captured || data.frames[0]?.time || 0)-(data.frames[0]?.time || 0));render();
  }catch(error){text("notice",error.message);}
}
async function poll() {
  try{const data=await api("/api/live");live=data.live;token=data.token;connectedAt=Date.now();
    if(pendingCommand && live?.ack?.id===pendingCommand.id){commandMessage=`${live.ack.accepted?"Confirmed":"Not applied"}: ${live.ack.reason}`;pendingCommand=null;}
  }catch(_){/* Keep last received state, visibly marked stale. */}
  if(pendingCommand && Date.now()-pendingCommand.sent>5000){commandMessage="No controller acknowledgement received. Check the launcher terminal before retrying.";pendingCommand=null;}
  if(isLive())render();setTimeout(poll,250);
}
async function command(name) {
  if(!live || pendingCommand)return;
  const id=crypto.randomUUID();pendingCommand={id,sent:Date.now()};commandMessage=`Waiting for controller to confirm ${name}…`;render();
  try{await api("/api/control",{method:"POST",headers:{"Content-Type":"application/json","X-Dashboard-Token":token},body:JSON.stringify({id,command:name,run_id:live.run_id})});}
  catch(error){pendingCommand=null;commandMessage=error.message;render();}
}
function jumpDecision(direction) {
  if(!replay?.rows.length)return;const row=getRow(),index=replay.rows.findIndex(item=>item.id===row?.id),next=replay.rows[Math.max(0,Math.min(replay.rows.length-1,index+direction))];
  selected=next;position=Math.max(0,rowTime(next)-(replay.frames[0]?.time || 0));playing=false;stateView="snapshot";render();
}
$("run").addEventListener("change",loadRun);
$("refresh").addEventListener("click",async()=>{try{await refreshRuns();await loadRun();}catch(error){text("notice",error.message);}});
$("snapshot-tab").onclick=()=>{stateView="snapshot";render();};$("now-tab").onclick=()=>{stateView="now";render();};
$("all-choices").onclick=()=>{allChoices=!allChoices;text("all-choices",allChoices?"Top five":"Show all");render();};
for(const name of ["pause","resume","stop"])$(name).onclick=()=>command(name);
$("seek").oninput=()=>{position=Number($("seek").value);selected=null;playing=false;render();};
$("play").onclick=()=>{if(position>=Number($("seek").max))position=0;playing=!playing;selected=null;stateView="now";render();};
$("back").onclick=()=>jumpDecision(-1);$("next").onclick=()=>jumpDecision(1);
$("next-round").onclick=()=>{const next=replay?.events.find(event=>event.event==="round_started"&&event.time>replayTime()+.05);if(next){position=next.time-replay.frames[0].time;selected=null;playing=false;render();}};
$("compare-run").onchange=async()=>{const revision=++compareRevision;other=null;if($("compare-run").value){try{const data=await api(`/api/runs/${encodeURIComponent($("compare-run").value)}`);if(revision!==compareRevision)return;other=data;}catch(error){text("comparison",error.message);return;}}compare();};
setInterval(()=>{const now=performance.now(),elapsed=(now-previousTick)/1000;previousTick=now;if(playing&&!isLive()){position=Math.min(Number($("seek").max),position+elapsed*Number($("speed").value));if(position>=Number($("seek").max))playing=false;render();}},100);
(async()=>{try{const data=await api("/api/live");live=data.live;token=data.token;connectedAt=Date.now();await refreshRuns();if(!live && !data.live_available){const recent=runs.find(run=>run.mode==="play"&&run.complete)||runs[0];if(recent)$("run").value=recent.id;}await loadRun();}catch(error){text("notice",error.message);}poll();})();
