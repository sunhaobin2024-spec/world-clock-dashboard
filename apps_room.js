// ============================================================
// cloudsystem 聊天室（对齐微信基础能力）：
// 群聊 · 私聊 · 好友申请 · 图片/视频/文件 · 语音消息 · 位置共享 · 历史记录
// 语音/视频通话（WebRTC）计划下一版
// ============================================================
(function(){
const NICK_KEY = "room.nick";
const REG = (typeof APP_REGISTRY !== "undefined") ? APP_REGISTRY : (window.APP_REGISTRY = {});

REG["聊天室"] = {
  icon:"聊天室", dash:true, width:520, height:600,
  render: () => `
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;background:var(--panel);color:var(--text);font-family:system-ui,'Microsoft YaHei',sans-serif;font-size:14px">
      <div style="display:flex;align-items:center;gap:6px;padding:8px 10px;border-bottom:1px solid rgba(128,128,128,.18)">
        <b style="font-size:13.5px">💬 聊天室</b>
        <button class="crTab on" data-v="group" style="padding:4px 12px;border:0;border-radius:8px;background:#22c1dc;color:#fff;font-size:12px;cursor:pointer">群聊</button>
        <button class="crTab" data-v="dm" style="padding:4px 12px;border:0;border-radius:8px;background:rgba(128,128,128,.14);color:inherit;font-size:12px;cursor:pointer">私聊</button>
        <button class="crTab" data-v="friends" style="padding:4px 12px;border:0;border-radius:8px;background:rgba(128,128,128,.14);color:inherit;font-size:12px;cursor:pointer">好友</button>
        <span style="margin-left:auto;font-size:11px;opacity:.6">昵称</span>
        <input id="crNick" maxlength="24" placeholder="设置" style="width:84px;padding:4px 8px;border:1px solid rgba(128,128,128,.3);border-radius:8px;background:transparent;color:inherit;font-size:11.5px">
      </div>
      <div id="crBody" style="flex:1;overflow:hidden;display:flex;flex-direction:column"></div>
    </div>`,
  onMount(body, win){
    const $ = s => body.querySelector(s);
    const esc = s => String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
    const H = Object.assign({"Content-Type":"application/json"}, authHeaders());
    let nick = "", timer = null, timerDM = null;
    const DEVID = (()=>{ try{ let d=localStorage.getItem("room.devid");
      if(!d){ d="d"+Date.now().toString(36)+Math.random().toString(36).slice(2,10); localStorage.setItem("room.devid",d); }
      return d; }catch(e){ return "d"+Math.random().toString(36).slice(2,12); } })();
    try { nick = localStorage.getItem(NICK_KEY) || ""; } catch(e){}

    function bubbleTo(box, m, mine){
      const d = document.createElement("div");
      if(m.id) d.dataset.mid = m.id;
      d.style.cssText = "margin:8px 0;text-align:"+(mine?"right":"left");
      let inner = "";
      if(m.file && m.file.kind === "img")
        inner += `<a href="${esc(m.file.url)}" target="_blank"><img src="${esc(m.file.url)}" style="max-width:220px;max-height:180px;border-radius:10px;display:block;margin:4px 0"></a>`;
      else if(m.file && m.file.kind === "video")
        inner += `<video src="${esc(m.file.url)}" controls style="max-width:240px;border-radius:10px;display:block;margin:4px 0"></video>`;
      else if(m.file && m.file.kind === "audio")
        inner += `<div style="display:inline-block;padding:6px 10px;border-radius:12px;background:rgba(128,128,128,.15);margin:4px 0"><b style="font-size:12px">🎤 语音</b><br><audio src="${esc(m.file.url)}" controls style="max-width:220px;height:36px"></audio></div>`;
      else if(m.file && m.file.kind === "file")
        inner += `<a href="${esc(m.file.url)}" target="_blank" style="display:inline-block;padding:6px 10px;border-radius:8px;background:rgba(128,128,128,.15);margin:4px 0">📎 ${esc(m.file.name)}（${Math.round((m.file.size||0)/1024)} KB）</a>`;
      if(m.loc)
        inner += `<div style="display:inline-block;padding:8px 12px;border-radius:10px;background:rgba(34,193,220,.15);margin:4px 0"><b>📍 位置</b><br>纬 ${m.loc.lat.toFixed(5)} · 经 ${m.loc.lon.toFixed(5)}<br><a href="https://uri.amap.com/marker?position=${m.loc.lat},${m.loc.lon}" target="_blank" style="color:#2b8fd8">高德</a> · <a href="https://www.openstreetmap.org/?mlat=${m.loc.lat}&mlon=${m.loc.lon}#map=16/${m.loc.lat}/${m.loc.lon}" target="_blank" style="color:#2b8fd8">OSM</a></div>`;
      if(m.recalled)
        inner += `<div style="display:inline-block;padding:6px 12px;border-radius:12px;background:rgba(128,128,128,.12);font-size:12px;opacity:.6">你撤回了一条消息</div>`;
      else if(m.text) inner += `<div style="display:inline-block;max-width:75%;padding:8px 12px;border-radius:14px;background:${mine?"rgba(34,193,220,.22)":"rgba(128,128,128,.14)"};white-space:pre-wrap;word-break:break-word">${esc(m.text)}</div>`;
      d.innerHTML = `<div style="font-size:10.5px;opacity:.55;margin-bottom:2px">${esc(m.nick||m.from||"?")} · ${esc(m.ts)}</div>${inner}`;
      box.appendChild(d);
      box.scrollTop = box.scrollHeight;
    }
    function locErr(err){
      const M={1:"定位被拒绝：请允许本站使用位置（浏览器地址栏锁图标 → 位置 → 允许）",
               2:"定位不可用：请检查手机系统定位服务是否开启",
               3:"定位超时：信号弱，再试一次"};
      return M[err.code] || ("定位失败："+(err.message||"未知错误"));
    }
    function getLoc(cb){
      if(location.protocol!=="https:"){ alert("定位需要 HTTPS。请把网址改为 https:// 开头再打开"); return; }
      if(!navigator.geolocation){ alert("此浏览器不支持定位"); return; }
      navigator.geolocation.getCurrentPosition(
        pos=>cb({lat:pos.coords.latitude, lon:pos.coords.longitude, acc:Math.round(pos.coords.accuracy)}),
        err=>alert(locErr(err)), {enableHighAccuracy:true, timeout:15000});
    }
    async function uploadFile(file, up){
      const CHUNK=4*1024*1024, total=Math.ceil(file.size/CHUNK)||1;
      for(let i=0;i<total;i++){
        const b64 = await new Promise((res,rej)=>{ const r=new FileReader(); r.onload=()=>res((r.result||"").split(",")[1]||""); r.onerror=rej; r.readAsDataURL(file.slice(i*CHUNK,(i+1)*CHUNK)); });
        const rr = await fetch("/api/upload", {method:"POST", headers:H, body:JSON.stringify({name:file.name, data:b64, dir:"room", append:i>0, offset:i*CHUNK})});
        const jj = await rr.json();
        if(!jj.ok) throw new Error("分片 "+(i+1)+" 上传失败");
        if(up) up.textContent = `上传 ${file.name}：${Math.round((i+1)/total*100)}%`;
      }
    }

    // ---------- 语音消息（按住说话，MediaRecorder）----------
    let rec=null, recChunks=[], recFile=null, recTimerIv=null, voiceDone=null;
    async function recStart(statusEl){
      if(!navigator.mediaDevices || !window.MediaRecorder){ alert("此浏览器不支持录音"); return false; }
      try{
        const stream = await navigator.mediaDevices.getUserMedia({audio:true});
        const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" :
                     (MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "");
        rec = new MediaRecorder(stream, mime?{mimeType:mime}:undefined);
        recChunks = [];
        rec.ondataavailable = e => { if(e.data.size) recChunks.push(e.data); };
        rec.onstop = () => {
          stream.getTracks().forEach(t=>t.stop());
          const blob = new Blob(recChunks, {type: mime||"audio/webm"});
          const ext = (mime||"").includes("mp4") ? "m4a" : "webm";
          recFile = new File([blob], "voice-"+Date.now()+"."+ext, {type: mime||"audio/webm"});
          // 录音数据在 onstop 才就绪（异步）——在此触发上传，而不是 recStop 返回时
          if(voiceDone){ const cb=voiceDone; voiceDone=null; cb(recFile); }
        };
        rec.start();
        let sec = 0;
        statusEl.textContent = "🎤 录音中 0 秒（松开发送）";
        recTimerIv = setInterval(()=>{ sec++; statusEl.textContent = "🎤 录音中 "+sec+" 秒（松开发送）"; if(sec>=60){ recStop(statusEl); } }, 1000);
        return true;
      }catch(e){
        alert("录音失败："+(e.message||"麦克风权限被拒绝——请在浏览器里允许本站使用麦克风"));
        return false;
      }
    }
    function recStop(statusEl){
      if(!rec) return false;
      clearInterval(recTimerIv);
      try{ rec.stop(); }catch(e){ return false; }
      rec = null;
      statusEl.textContent = "";
      return true;
    }


    // ---------- 长按/右键长按 撤回自己的消息（2 分钟内）----------
    function bindRecall(box, apiPath, peerName){
      let pressT = null;
      const tryRecall = async el => {
        const dv = el.closest("[data-mid]");
        if(!dv) return;
        const m = (box.__recent||[]).find(x=>x.id === +dv.dataset.mid);
        if(!m || m.recalled) return;
        if(!confirm("撤回这条消息？")) return;
        const r = await fetch(apiPath, {method:"POST", headers:H,
          body: JSON.stringify(peerName?{id:m.id, nick, peer:peerName}:{id:m.id, nick})});
        const j = await r.json();
        if(!j.ok) alert(j.err||"撤回失败");
      };
      box.addEventListener("contextmenu", e=>{ e.preventDefault(); tryRecall(e.target); });
      box.addEventListener("touchstart", e=>{
        pressT = setTimeout(()=>{ pressT=null; tryRecall(e.target); }, 600);
      }, {passive:true});
      ["touchend","touchmove","touchcancel"].forEach(ev=>
        box.addEventListener(ev, ()=>{ if(pressT){ clearTimeout(pressT); pressT=null; } }, {passive:true}));
    }
    // ---------- 群聊 ----------
    let lastRoom = 0;
    function viewGroup(){
      clearInterval(timerDM);
      $("#crBody").innerHTML = `
        <div id="crMsgs" style="flex:1;overflow-y:auto;padding:10px 12px"></div>
        <div id="crUp" style="display:none;padding:4px 12px;font-size:12px;opacity:.7"></div>
        <div style="display:flex;gap:6px;align-items:flex-end;padding:8px 10px;border-top:1px solid rgba(128,128,128,.18)">
          <button id="crImg" title="发图片" style="padding:7px 9px;border:0;border-radius:8px;background:rgba(128,128,128,.14);color:inherit;font-size:15px;cursor:pointer">🖼️</button>
          <button id="crVid" title="发视频" style="padding:7px 9px;border:0;border-radius:8px;background:rgba(128,128,128,.14);color:inherit;font-size:15px;cursor:pointer">🎬</button>
          <button id="crLoc" title="共享位置" style="padding:7px 9px;border:0;border-radius:8px;background:rgba(128,128,128,.14);color:inherit;font-size:15px;cursor:pointer">📍</button>
          <button id="crMic" style="flex:1;padding:8px 6px;border:0;border-radius:10px;background:rgba(128,128,128,.14);color:inherit;font-size:13px;cursor:pointer;user-select:none">🎤 按住说话</button>
          <textarea id="crIn" rows="1" placeholder="输入消息…" style="flex:2;resize:none;border:1px solid rgba(128,128,128,.3);border-radius:10px;padding:8px 10px;background:transparent;color:inherit;font-size:13.5px;font-family:inherit;max-height:90px"></textarea>
          <button id="crGo" style="padding:8px 16px;border:0;border-radius:10px;background:linear-gradient(180deg,#22c1dc,#0e7490);color:#fff;font-size:13px;cursor:pointer">发送</button>
        </div>
        <input type="file" id="crFile" accept="image/*" style="display:none">
        <input type="file" id="crFileV" accept="video/*" style="display:none">`;
      const msgs=$("#crMsgs"), mic=$("#crMic"), up=$("#crUp");
      const pull = async first => {
        try{
          const r=await fetch("/api/room/msgs",{method:"POST",headers:H,body:JSON.stringify({since:lastRoom})});
          const j=await r.json();
          (j.msgs||[]).forEach(m=>{ bubbleTo(msgs,m,m.nick===nick); lastRoom=Math.max(lastRoom,m.id||0);
            box.__recent = (box.__recent||[]).concat(m).slice(-120); });
          if(first) msgs.scrollTop=msgs.scrollHeight;
        }catch(e){}
      };
      const doSend = async () => {
        const inp=$("#crIn"), text=inp.value.trim();
        if(!text){ return; }
        if(!nick){ alert("先在顶部设置昵称"); return; }
        inp.value="";
        try{ const r=await fetch("/api/room/send",{method:"POST",headers:H,body:JSON.stringify({nick,text,devid:DEVID})});
          if(!(await r.json()).ok) inp.value=text; else await pull(false);
        }catch(e){ inp.value=text; }
      };
      $("#crGo").onclick=doSend;
      $("#crIn").addEventListener("keydown",e=>{ if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){ e.preventDefault(); doSend(); } });
      $("#crImg").onclick=()=>$("#crFile").click();
      $("#crVid").onclick=()=>$("#crFileV").click();
      $("#crFile").onchange=async()=>{ const f=$("#crFile").files[0]; if(!f) return;
        if(f.size>200*1024*1024){ alert("上限 200MB"); return; }
        up.style.display="block";
        try{ await uploadFile(f,up);
          const r=await fetch("/api/room/send",{method:"POST",headers:H,body:JSON.stringify({nick,text:"",file:{name:f.name},devid:DEVID})});
          if((await r.json()).ok) await pull(false);
        }catch(e){ alert(e.message); } up.style.display="none"; $("#crFile").value="";
      };
      $("#crFileV").onchange=async()=>{ const f=$("#crFileV").files[0]; if(!f) return;
        if(f.size>200*1024*1024){ alert("上限 200MB"); return; }
        up.style.display="block";
        try{ await uploadFile(f,up);
          const r=await fetch("/api/room/send",{method:"POST",headers:H,body:JSON.stringify({nick,text:"",file:{name:f.name},devid:DEVID})});
          if((await r.json()).ok) await pull(false);
        }catch(e){ alert(e.message); } up.style.display="none"; $("#crFileV").value="";
      };
      $("#crLoc").onclick=()=>{ if(!nick){ alert("先设置昵称"); return; }
        getLoc(async loc=>{ try{
          const r=await fetch("/api/room/send",{method:"POST",headers:H,body:JSON.stringify({nick,text:"",loc,devid:DEVID})});
          if((await r.json()).ok) await pull(false);
        }catch(e){ alert(e.message); } }); };
      mic.addEventListener("touchstart", async e=>{ e.preventDefault(); recFile=null;
        if(await recStart(up)) mic.textContent = "🎤 松开发送"; }, {passive:false});
      const sendVoice = async f => {
        if(!f || f.size < 800){ alert("说话时间太短，再试一次"); return; }
        up.style.display = "block";
        try{ await uploadFile(f, up);
          const r = await fetch("/api/room/send",{method:"POST",headers:H,body:JSON.stringify({nick,text:"",file:{name:f.name},devid:DEVID})});
          if((await r.json()).ok) await pull(false);
        }catch(err){ alert(err.message); }
        up.style.display = "none";
      };
      mic.addEventListener("touchend", e=>{ e.preventDefault();
        voiceDone = sendVoice; recStop(up); mic.textContent="🎤 按住说话"; }, {passive:false});
      let mDown=false;
      mic.addEventListener("mousedown", async ()=>{ if(mDown) return; mDown=true;
        if(await recStart(up)) mic.textContent="🎤 松开发送"; });
      mic.addEventListener("mouseup", ()=>{ if(!mDown) return; mDown=false;
        voiceDone = sendVoice; recStop(up); mic.textContent="🎤 按住说话"; });
      bindRecall(msgs, "/api/room/recall", null);
      pull(true);
      clearInterval(timer); timer=setInterval(()=>pull(false),3000);
    }

    // ---------- 好友 ----------
    function viewFriends(){
      clearInterval(timer); clearInterval(timerDM);
      if(!nick){
        $("#crBody").innerHTML='<div style="flex:1;display:flex;align-items:center;justify-content:center;opacity:.6">先在顶部设置昵称，才能加好友和私聊</div>';
        return;
      }
      $("#crBody").innerHTML = '<div id="frBody" style="flex:1;overflow-y:auto;padding:10px 12px">加载中…</div>';
      renderFriends();
    }
    async function renderFriends(){
      const box=$("#frBody");
      try{
        const [u,rq,fl]=await Promise.all([
          fetch("/api/friend",{method:"POST",headers:H,body:JSON.stringify({action:"users",nick,devid:DEVID})}).then(r=>r.json()),
          fetch("/api/friend",{method:"POST",headers:H,body:JSON.stringify({action:"requests",nick,devid:DEVID})}).then(r=>r.json()),
          fetch("/api/friend",{method:"POST",headers:H,body:JSON.stringify({action:"list",nick,devid:DEVID})}).then(r=>r.json())
        ]);
        box.innerHTML=`
          ${(rq.got||[]).length?`<div style="font-weight:700;margin-bottom:6px">📨 收到的好友申请</div>`+
            rq.got.map(g=>`<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-top:1px solid rgba(128,128,128,.1)">
              <b>${esc(g.nick)}</b><span style="font-size:11px;opacity:.5">${esc(g.grp)} · ${esc(g.ts)}</span>
              <button data-acc="${g.id}" style="margin-left:auto;padding:4px 12px;border:0;border-radius:8px;background:#22b573;color:#fff;font-size:12px;cursor:pointer">同意</button></div>`).join(""):""}
          <div style="font-weight:700;margin:10px 0 6px">👥 我的好友（点开私聊）</div>
          ${(fl.friends||[]).map(f=>`<div style="display:flex;align-items:center;padding:7px 0;border-top:1px solid rgba(128,128,128,.1)">
              <span style="width:8px;height:8px;border-radius:50%;background:${f.online?'#22b573':'rgba(128,128,128,.4)'};margin-right:6px;flex:none" title="${f.online?'在线':'离线'}"></span>
              <b style="cursor:pointer" data-dm="${esc(f.nick)}">${esc(f.nick)}</b>
              <span style="font-size:11px;opacity:.5;margin-left:8px">${f.online?'在线':'离线'}</span>
              <button data-dm="${esc(f.nick)}" style="margin-left:auto;padding:4px 12px;border:0;border-radius:8px;background:#22c1dc;color:#fff;font-size:12px;cursor:pointer">私聊</button></div>`).join("")
            ||'<div style="opacity:.6;font-size:12.5px">还没有好友，从下方添加</div>'}
          <div style="font-weight:700;margin:12px 0 6px">➕ 可添加的用户（发言过的）</div>
          ${(u.users||[]).map(x=>`<div style="display:flex;align-items:center;padding:6px 0;border-top:1px solid rgba(128,128,128,.08)">
              <b>${esc(x.nick)}</b><span style="font-size:11px;opacity:.5;margin-left:8px">${esc(x.grp)} · ${esc(x.seen)}</span>
              <button data-add="${esc(x.nick)}" style="margin-left:auto;padding:4px 12px;border:0;border-radius:8px;background:rgba(128,128,128,.2);color:inherit;font-size:12px;cursor:pointer">+ 加好友</button></div>`).join("")
            ||'<div style="opacity:.6;font-size:12.5px">还没有其他用户发言——让另一台设备在群聊里说句话</div>'}
          ${(rq.mine||[]).length?`<div style="font-weight:700;margin:12px 0 6px">📤 我发出的申请</div>`+
            rq.mine.map(x=>`<div style="padding:4px 0;font-size:12.5px">→ ${esc(x.nick)}：${x.status==="accepted"?"已同意":x.status==="pending"?"等待对方处理":"已处理"} <span style="opacity:.5">${esc(x.ts)}</span></div>`).join(""):""}`;
        box.querySelectorAll("[data-add]").forEach(b=>b.onclick=async()=>{
          const r=await fetch("/api/friend",{method:"POST",headers:H,body:JSON.stringify({action:"request",nick,to:b.dataset.add,devid:DEVID})});
          const j=await r.json(); alert(j.msg||j.err||""); renderFriends();
        });
        box.querySelectorAll("[data-acc]").forEach(b=>b.onclick=async()=>{
          const r=await fetch("/api/friend",{method:"POST",headers:H,body:JSON.stringify({action:"accept",nick,id:+b.dataset.acc,devid:DEVID})});
          const j=await r.json(); alert(j.msg||j.err||""); renderFriends();
        });
        box.querySelectorAll("[data-dm]").forEach(bt=>bt.onclick=()=>viewDM(bt.dataset.dm));
      }catch(e){ box.textContent="加载失败："+e.message; }
    }

    // ---------- 私聊 ----------
    let dmPeer=null, lastDM=0;
    function stopDM(){ clearInterval(timerDM); timerDM=null; dmPeer=null; }
    function viewDM(peer){
      clearInterval(timer);
      dmPeer=peer; lastDM=0;
      $("#crBody").innerHTML=`
        <div style="display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid rgba(128,128,128,.15)">
          <button id="dmBack" style="padding:3px 10px;border:0;border-radius:8px;background:rgba(128,128,128,.16);color:inherit;font-size:12px;cursor:pointer">← 好友</button>
          <b style="font-size:13px">与 ${esc(peer)} 私聊</b>
          <button id="dmCallA" title="语音通话" style="margin-left:auto;padding:4px 10px;border:0;border-radius:8px;background:rgba(34,181,115,.2);color:inherit;font-size:13px;cursor:pointer">📞</button>
          <button id="dmCallV" title="视频通话" style="padding:4px 10px;border:0;border-radius:8px;background:rgba(34,193,220,.2);color:inherit;font-size:13px;cursor:pointer">🎥</button>
          </div>
        <div id="dmMsgs" style="flex:1;overflow-y:auto;padding:10px 12px"></div>
        <div id="dmUp" style="display:none;padding:4px 12px;font-size:12px;opacity:.7"></div>
        <div style="display:flex;gap:6px;align-items:flex-end;padding:8px 10px;border-top:1px solid rgba(128,128,128,.18)">
          <button id="dmImg" style="padding:7px 9px;border:0;border-radius:8px;background:rgba(128,128,128,.14);color:inherit;font-size:15px;cursor:pointer">📎</button>
          <button id="dmLoc" style="padding:7px 9px;border:0;border-radius:8px;background:rgba(128,128,128,.14);color:inherit;font-size:15px;cursor:pointer">📍</button>
          <button id="dmMic" style="flex:1;padding:8px 6px;border:0;border-radius:10px;background:rgba(128,128,128,.14);color:inherit;font-size:13px;cursor:pointer;user-select:none">🎤 按住说话</button>
          <textarea id="dmIn" rows="1" placeholder="私聊消息…" style="flex:2;resize:none;border:1px solid rgba(128,128,128,.3);border-radius:10px;padding:8px 10px;background:transparent;color:inherit;font-size:13.5px;font-family:inherit"></textarea>
          <button id="dmGo" style="padding:8px 16px;border:0;border-radius:10px;background:linear-gradient(180deg,#22c1dc,#0e7490);color:#fff;font-size:13px;cursor:pointer">发送</button>
        </div>
        <input type="file" id="dmFile" style="display:none">`;
      const box=$("#dmMsgs"), mic=$("#dmMic"), up=$("#dmUp");
      const pull=async first=>{
        try{
          const r=await fetch("/api/dm/msgs",{method:"POST",headers:H,body:JSON.stringify({nick,peer,since:lastDM})});
          const j=await r.json();
          (j.msgs||[]).forEach(m=>{ bubbleTo(box,m,m.from===nick); lastDM=Math.max(lastDM,m.id||0);
            box.__recent = (box.__recent||[]).concat(m).slice(-120); });
          if(first) box.scrollTop=box.scrollHeight;
        }catch(e){}
      };
      const send=async payload=>{
        const r=await fetch("/api/dm/send",{method:"POST",headers:H,body:JSON.stringify(Object.assign({nick,to:dmPeer},payload))});
        return r.json();
      };
      $("#dmBack").onclick=()=>{ stopDM(); viewFriends(); };
      $("#dmCallA").onclick=()=>startCall(peer,"audio");
      $("#dmCallV").onclick=()=>startCall(peer,"video");
      $("#dmGo").onclick=async()=>{ const i=$("#dmIn"),t=i.value.trim(); if(!t) return; i.value="";
        const j=await send({text:t}); if(!j.ok) alert(j.err||"失败"); await pull(false); };
      $("#dmIn").addEventListener("keydown",e=>{ if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){ e.preventDefault(); $("#dmGo").onclick(); } });
      $("#dmImg").onclick=()=>$("#dmFile").click();
      $("#dmFile").onchange=async()=>{ const f=$("#dmFile").files[0]; if(!f) return;
        up.style.display="block";
        try{ await uploadFile(f,up);
          const j=await send({text:"",file:{name:f.name}});
          if(!j.ok) alert(j.err||"失败"); await pull(false);
        }catch(e){ alert(e.message); } up.style.display="none"; $("#dmFile").value="";
      };
      $("#dmLoc").onclick=()=>getLoc(async loc=>{ const j=await send({text:"",loc}); if(!j.ok) alert(j.err||"失败"); await pull(false); });
      mic.addEventListener("touchstart", async e=>{ e.preventDefault(); recFile=null;
        if(await recStart(up)) mic.textContent="🎤 松开发送"; }, {passive:false});
      const sendVoiceDM = async f => {
        if(!f || f.size < 800){ alert("说话时间太短，再试一次"); return; }
        up.style.display = "block";
        try{ await uploadFile(f, up);
          const j = await send({text:"", file:{name:f.name}});
          if(!j.ok) alert(j.err||"失败"); await pull(false);
        }catch(err){ alert(err.message); }
        up.style.display = "none";
      };
      mic.addEventListener("touchend", e=>{ e.preventDefault();
        voiceDone = sendVoiceDM; recStop(up); mic.textContent="🎤 按住说话"; }, {passive:false});
      let mDown=false;
      mic.addEventListener("mousedown", async ()=>{ if(mDown) return; mDown=true;
        if(await recStart(up)) mic.textContent="🎤 松开发送"; });
      mic.addEventListener("mouseup", ()=>{ if(!mDown) return; mDown=false;
        voiceDone = sendVoiceDM; recStop(up); mic.textContent="🎤 按住说话"; });
      bindRecall(box, "/api/dm/recall", dmPeer);
      pull(true);
      timerDM=setInterval(()=>pull(false),3000);
    }

    // ---------- 音视频通话（WebRTC，信令轮询中转）----------
    let pc=null, localStream=null, callPeer=null, callRole=null, callPollT=null, appliedIce=0, inRing=false;
    const RTC_CFG = {iceServers:[{urls:["stun:stun.l.google.com:19302","stun:stun1.l.google.com:19302","stun:stun.miwifi.com:3478"]}]};
    function callOverlay(kind, status){
      removeOverlay();
      const ov=document.createElement("div");
      ov.id="callOv";
      ov.style.cssText="position:absolute;inset:0;z-index:50;background:rgba(10,14,20,.92);color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px";
      ov.innerHTML = `
        ${kind==="video"?'<video id="callRemote" autoplay playsinline style="max-width:100%;max-height:58%;border-radius:12px;background:#000"></video>':"<div style='font-size:40px'>📞</div>"}
        <div id="callStatus" style="font-size:14px">${esc(status)}</div>
        ${kind==="video"?'<video id="callLocal" autoplay playsinline muted style="position:absolute;right:12px;top:12px;width:110px;border-radius:10px;background:#000"></video>':""}
        <button id="callHang" style="padding:12px 34px;border:0;border-radius:999px;background:#e0483e;color:#fff;font-size:15px;cursor:pointer">挂断</button>`;
      $("#crBody").appendChild(ov);
      $("#callHang").onclick = endCall;
    }
    function removeOverlay(){ const o=$("#callOv"); if(o) o.remove(); }
    function setCallStatus(t){ const e=$("#callStatus"); if(e) e.textContent=t; }
    function bindMedia(){
      const lv=$("#callLocal"); if(lv && localStream) lv.srcObject=localStream;
      pc.ontrack = e=>{ const rv=$("#callRemote"); if(rv) rv.srcObject=e.streams[0]; };
    }
    async function mkPC(){
      pc = new RTCPeerConnection(RTC_CFG);
      localStream.getTracks().forEach(t=>pc.addTrack(t, localStream));
      pc.onicecandidate = e=>{ if(e.candidate)
        fetch("/api/call",{method:"POST",headers:H,body:JSON.stringify({action:"ice",nick,peer:callPeer,cand:JSON.stringify(e.candidate)})}).catch(()=>{}); };
      bindMedia();
    }
    async function startCall(peer, kind){
      if(location.protocol!=="https:"){ alert("通话需要 HTTPS（网址以 https:// 开头）"); return; }
      if(pc){ alert("已在通话中"); return; }
      callPeer=peer; callRole="caller"; appliedIce=0;
      try{ localStream = await navigator.mediaDevices.getUserMedia(kind==="video"?{audio:true,video:{facingMode:"user",width:{ideal:640}}}:{audio:true}); }
      catch(e){ alert("无法获取"+(kind==="video"?"摄像头/麦克风":"麦克风")+"权限："+(e.message||"")); return; }
      callOverlay(kind, "正在呼叫 "+peer+" …");
      await mkPC();
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const r = await fetch("/api/call",{method:"POST",headers:H,body:JSON.stringify({action:"invite",nick,peer,kind,offer:JSON.stringify(pc.localDescription)})});
      const j = await r.json();
      if(!j.ok){ alert(j.err||"呼叫失败"); endCall(); return; }
      startCallPoll();
    }
    async function acceptCall(info){
      callPeer=info.peer; callRole="callee"; appliedIce=0;
      try{ localStream = await navigator.mediaDevices.getUserMedia(info.kind==="video"?{audio:true,video:{facingMode:"user",width:{ideal:640}}}:{audio:true}); }
      catch(e){ await fetch("/api/call",{method:"POST",headers:H,body:JSON.stringify({action:"end",nick,peer:info.peer})}).catch(()=>{}); alert("无法获取设备权限"); return; }
      callOverlay(info.kind, "接通中…");
      await mkPC();
      await pc.setRemoteDescription(new RTCSessionDescription(JSON.parse(info.offer)));
      const ans = await pc.createAnswer();
      await pc.setLocalDescription(ans);
      await fetch("/api/call",{method:"POST",headers:H,body:JSON.stringify({action:"answer",nick,peer:info.peer,answer:JSON.stringify(pc.localDescription)})});
      applyIce(info.ice||[]);
      startCallPoll();
    }
    function applyIce(list){
      if(!pc) return;
      for(; appliedIce < list.length; appliedIce++){
        try{ pc.addIceCandidate(new RTCIceCandidate(JSON.parse(list[appliedIce]))).catch(()=>{}); }catch(e){}
      }
    }
    function startCallPoll(){
      clearInterval(callPollT);
      callPollT = setInterval(async()=>{
        try{
          const j = await (await fetch("/api/call",{method:"POST",headers:H,body:JSON.stringify({action:"poll",nick})})).json();
          const c = j.call;
          if(!c){ if(callRole==="caller"){ setCallStatus("对方已挂断"); setTimeout(cleanupCall, 1200); } return; }
          if(c.state==="active" && callRole==="caller" && c.answer && pc && pc.signalingState!=="stable"){
            await pc.setRemoteDescription(new RTCSessionDescription(JSON.parse(c.answer)));
            setCallStatus("已接通");
          }
          if(pc) applyIce(c.ice||[]);
          if(callRole==="callee" && c.state==="active") setCallStatus("已接通");
        }catch(e){}
      }, 1500);
    }
    async function endCall(){
      try{ if(callPeer) await fetch("/api/call",{method:"POST",headers:H,body:JSON.stringify({action:"end",nick,peer:callPeer})}); }catch(e){}
      cleanupCall();
    }
    function cleanupCall(){
      clearInterval(callPollT); callPollT=null;
      try{ if(localStream) localStream.getTracks().forEach(t=>t.stop()); }catch(e){}
      try{ if(pc) pc.close(); }catch(e){}
      pc=null; localStream=null; callPeer=null; callRole=null; appliedIce=0;
      removeOverlay();
    }
    // 全局来电监听（任何视图都能接到）
    setInterval(async()=>{
      try{
        if(pc || !nickOk || !nick) return;
        const j = await (await fetch("/api/call",{method:"POST",headers:H,body:JSON.stringify({action:"poll",nick})})).json();
        const c = j.call;
        if(c && c.state==="ringing" && c.role==="callee" && !inRing){
          inRing = true;
          const acc = confirm(c.peer+" 请求"+(c.kind==="video"?"视频":"语音")+"通话，接听吗？");
          inRing = false;
          if(acc){ if(!nickOk) return; viewDM(c.peer); setTimeout(()=>acceptCall(c), 300); }
          else fetch("/api/call",{method:"POST",headers:H,body:JSON.stringify({action:"end",nick,peer:c.peer})}).catch(()=>{});
        }
      }catch(e){}
    }, 2500);

    // ---------- 视图切换 ----------
    body.querySelectorAll(".crTab").forEach(b=>{
      b.onclick=()=>{
        body.querySelectorAll(".crTab").forEach(x=>{x.classList.remove("on");x.style.background="rgba(128,128,128,.14)";});
        b.classList.add("on"); b.style.background="#22c1dc";
        const v=b.dataset.v;
        if(v==="group") viewGroup();
        else viewFriends();
      };
    });

    try { nick = localStorage.getItem(NICK_KEY) || ""; } catch(e){}
    let nickOk = false;   // 昵称是否已过审
    const stLb = document.createElement("span");
    stLb.style.cssText = "font-size:11px;margin-left:4px";
    $("#crNick").after(stLb);
    function setStLb(st){
      stLb.textContent = st==="approved" ? "✅" : st==="pending" ? "⏳审核中" : st==="rejected" ? "❌被拒" : "";
    }
    async function submitNick(){
      const v=$("#crNick").value.trim().slice(0,24);
      if(!v){ nick=""; nickOk=false; setStLb(""); return; }
      try{
        const r=await fetch("/api/nick",{method:"POST",headers:H,body:JSON.stringify({action:"set",nick:v})});
        const j=await r.json();
        if(j.ok){
          setStLb(j.status);
          if(j.status==="approved"){ nick=v; try{ localStorage.setItem(NICK_KEY,v); }catch(e){} nickOk=true; }
          else if(j.status==="pending"){
            nickOk=false;
            const poll=setInterval(async()=>{
              try{
                const r2=await fetch("/api/nick",{method:"POST",headers:H,body:JSON.stringify({action:"check",nick:v})});
                const j2=await r2.json();
                if(j2.status==="approved"){ clearInterval(poll);
                  nick=v; try{ localStorage.setItem(NICK_KEY,v); }catch(e){} nickOk=true; setStLb("✅");
                  alert("你的新昵称「"+v+"」已通过审核，现在可以用了 ✔");
                }else if(j2.status==="rejected"){ clearInterval(poll); setStLb("❌"); alert("昵称「"+v+"」未通过审核，请换一个"); }
              }catch(e){}
            },4000);
          }
          else nickOk=false;
        }else{ setStLb("❌"); alert(j.err||"提交失败"); }
      }catch(e){}
    }
    let nickDeb=null;
    $("#crNick").oninput = () => {
      clearTimeout(nickDeb);
      nickDeb=setTimeout(submitNick, 900);
    };
    $("#crNick").addEventListener("keydown", e=>{ if(e.key==="Enter"){ e.preventDefault(); clearTimeout(nickDeb); submitNick(); } });
    // 初始：保证一定有一个立即可用的昵称——
    // 本地有且已过审 → 直接用；否则按网络找回过审名 → 找不到就随机分发（系统名免审）。
    (async()=>{
      if(nick){
        try{
          const r=await fetch("/api/nick",{method:"POST",headers:H,body:JSON.stringify({action:"check",nick})});
          const j=await r.json();
          if(j.status==="approved"){ nickOk=true; setStLb("✅"); return; }
        }catch(e){}
      }
      let got=null;
      try{
        const r=await fetch("/api/nick",{method:"POST",headers:H,body:JSON.stringify({action:"recall",devid:DEVID})});
        const j=await r.json();
        if(j.ok && j.nick) got=j.nick;
      }catch(e){}
      if(!got){
        try{
          const r=await fetch("/api/nick",{method:"POST",headers:H,body:JSON.stringify({action:"auto",devid:DEVID})});
          const j=await r.json();
          if(j.ok && j.nick) got=j.nick;
        }catch(e){}
      }
      if(got){
        nick=got; $("#crNick").value=nick; nickOk=true; setStLb("✅");
        try{ localStorage.setItem(NICK_KEY,nick); }catch(e){}
        const st=document.createElement("div");
        st.style.cssText="padding:6px 12px;font-size:12px;background:rgba(34,193,220,.12);color:inherit";
        st.textContent="已为你准备昵称：「"+nick+"」（顶部可改，改名需审核）";
        $("#crBody").prepend(st);
        setTimeout(()=>st.remove(), 8000);
      }
    })();

    win.__cr = { nick:()=>nick, nickOk:()=>nickOk, devid:DEVID, viewGroup, viewFriends, viewDM };
    viewGroup();
    const beat=()=>{ if(nick) fetch("/api/friend",{method:"POST",headers:H,body:JSON.stringify({action:"seen",nick,devid:DEVID})}).catch(()=>{}); };
    beat(); setInterval(beat, 30000);
    win.addEventListener("closed", ()=>{ clearInterval(timer); stopDM(); });
  }
};
})();
