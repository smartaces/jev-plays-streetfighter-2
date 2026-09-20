"use strict";
const videoState = {run:null, frame:null, episode:null, captured:0, held:[], phase:null, error:null, received:0};
let videoBusy = false;

function videoMatches() {
  return isLive() && live && videoState.run === live.run_id;
}

function livePad() {
  if (!videoMatches() || Date.now()-videoState.received>3000) return;
  document.querySelectorAll("[data-button]").forEach(node=>node.classList.toggle("held",videoState.held.includes(node.dataset.button)));
  text("input-phase",label(videoState.phase));
  text("input-caption",`Video frame ${videoState.frame} · ${videoState.held.join(" + ") || "buttons released"}. Recent input changes are listed below.`);
}

function renderVideo() {
  const online=isLive() && live && !live.finished && Date.now()-live.sent_at*1000<3000 && Date.now()-connectedAt<3000;
  const matches=videoMatches(), fresh=matches && (live.paused || Date.now()-videoState.captured*1000<1500);
  $("game-video").hidden=!matches;
  $("video-placeholder").hidden=!!(online && fresh);
  $("video-stage").classList.toggle("stale",!!matches && !(online && fresh));
  text("video-status",!isLive()?"REPLAY":!online?"OFFLINE":!matches?"CONNECTING":!fresh?"STALE":live.paused?"PAUSED":"LIVE");
  text("video-placeholder-title",!isLive()?"This run has no recorded video":!online?"No live game connected":videoState.error || "Waiting for game video");
  text("video-placeholder-detail",!isLive()?"Video is not recorded. Explore the saved proximity map, decisions and inputs on the right.":!online?"Open Play with Dashboard.command to watch the game here. Saved sessions remain available for review.":"The emulator runs locally. Frames will appear here as soon as they arrive.");
  text("video-stamp",matches?`Live game · frame ${videoState.frame} · fight ${(videoState.episode ?? 0)+1}`:!isLive()?"Saved state replay · no video recording":"Waiting for a live frame");
  const audio=live?.summary?.audio;
  text("video-audio",!isLive()?"Replay is silent.":audio?.enabled?`Sound on this Mac · ${audio.muted?"muted":"on"}`:"Local audio unavailable or disabled.");
  $("mute").disabled=!online || !audio?.enabled || !!pendingCommand;
  text("mute",audio?.muted?"Unmute":"Mute");
  $("video-fullscreen").disabled=!matches;
  livePad();
}

async function fetchVideo() {
  const started=performance.now();
  if (!videoBusy && isLive() && live && !live.finished && Date.now()-live.sent_at*1000<3000 && !document.hidden) {
    videoBusy=true;
    const run=live.run_id;
    try {
      const response=await fetch(`/api/video?run=${encodeURIComponent(run)}`,{cache:"no-store",signal:AbortSignal.timeout(1500)});
      if (response.status===200 && response.headers.get("X-Run-Id")===run) {
        const blob=await response.blob(), bitmap=await createImageBitmap(blob);
        try {
          // A session change during decode must never show the other run's video.
          if (isLive() && live?.run_id===run) {
            const canvas=$("game-video");
            if(canvas.width!==bitmap.width || canvas.height!==bitmap.height){canvas.width=bitmap.width;canvas.height=bitmap.height;}
            const context=canvas.getContext("2d");context.imageSmoothingEnabled=false;context.drawImage(bitmap,0,0);
            Object.assign(videoState,{run,frame:Number(response.headers.get("X-Game-Frame")),episode:Number(response.headers.get("X-Game-Episode")),
              captured:Number(response.headers.get("X-Frame-Captured")),held:(response.headers.get("X-Buttons")||"").split(",").filter(Boolean),
              phase:response.headers.get("X-Input-Phase"),error:null,received:Date.now()});
          }
        } finally { bitmap.close(); }
      }
    } catch (_) { videoState.error="Video connection interrupted"; }
    finally {videoBusy=false;renderVideo();}
  }
  const interval=live?.paused?250:1000/30;
  setTimeout(fetchVideo,Math.max(4,interval-(performance.now()-started)));
}

$("mute").onclick=()=>command("mute");
async function toggleFullscreen() {
  try {if(document.fullscreenElement)await document.exitFullscreen();else await $("video-stage").requestFullscreen();}
  catch(_){text("video-stamp","Full-screen display is unavailable in this browser.");}
}
$("video-fullscreen").onclick=toggleFullscreen;
$("video-exit-fullscreen").onclick=toggleFullscreen;
document.addEventListener("keydown",event=>{if(event.key==="Escape" && document.fullscreenElement)document.exitFullscreen().catch(()=>{});});
document.addEventListener("fullscreenchange",()=>text("video-fullscreen",document.fullscreenElement?"Exit full screen ↙":"Full screen ↗"));
renderVideo();fetchVideo();
