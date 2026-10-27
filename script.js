// ============ 真实 bash 后端 API（后端不可用时自动退回模拟） ============
const API = {
  // 命令超时两档，和后端 exec_timeout_for() 保持一致。
  // 之前这里写死 20 秒，而后端跑 30 秒 —— 慢命令必然是前端先放弃，
  // 用户看到"无法连接后端"，实际上命令在后端跑得好好的。
  // 这个错配的教训：前后端的超时口径必须来自同一个定义。
  T_FAST: 15000, T_SLOW: 300000,
  // 判断走哪一档。宁可判慢（多等一会儿），也别判快导致提前放弃。
  slowCmd(c){
    return /apt|dpkg|pip|make|gcc|g\+\+|cmake|clang|ninja|curl|wget|git|tar|unzip|zip|gzip|7z|dd\s+if|rsync|sleep|tail\s+-f|watch|vim|nano|convert|npm|yarn|go\s+(get|build)/i.test(c||"");
  },
  async exec(cmd,cwd,sid){
    const slow=this.slowCmd(cmd);
    // 后端超时 + 8 秒宽限，让后端有机会先把结果送回来
    const wait=this.slowCmd(cmd)?(300000+8000):(15000+8000);
    try{
      const r=await fetch("/api/exec",{method:"POST",
        headers:Object.assign({"Content-Type":"application/json"}, authHeaders()),
        body:JSON.stringify({cmd,cwd,sid}),signal:AbortSignal.timeout(wait)});
      if(!r.ok){
        let msg="";
        try{ const j=await r.json(); msg=j.err||""; }catch(_){}
        // 把 HTTP 状态码抛出去，前端能精确区分"未登录/被拦截/限流"，
        // 而不是一律报"服务未启动"。用户要的就是这种可诊断的错误码。
        throw {status:r.status, msg:msg};
      }
      const j=await r.json();
      return {out:j.out||"",err:j.err||"",cwd:j.cwd,slow:slow};
    }catch(e){
      // 超时和网络故障要区分开。原来一律报"无法连接后端"，
      // 你会以为是服务挂了，其实只是命令还在跑。
      if(e&&e.name==="TimeoutError"){
        return {out:"",err:(slow?"bash: 命令超过 "+(wait/1000|0)+" 秒仍未结束，已中断。\n":"bash: 命令超过 15 秒仍未结束。\n")
          +(slow?"如果确实需要更久，把命令拆成几步跑。\n":""),cwd:cwd,slow:slow};
      }
      // 后端明确回了非 2xx：带上状态码，方便定位（401 失效 / 403 拦截 / 429 限流）
      if(e&&typeof e==="object"&&e.status){
        const hint = e.status===401?"（登录已失效，请刷新页面重新进入）"
                   : e.status===403?"（请求被安全策略拦截："+(e.msg||"跨站/CSRF")+"）"
                   : e.status===429?"（请求过于频繁，稍后再试）"
                   : "";
        return {out:"",err:"bash: 后端返回 "+e.status+hint+"\n",cwd:cwd,slow:slow};
      }
      return {out:"",err:"bash: 无法连接后端（服务未启动）\n",cwd:cwd,slow:slow};
    }
  },
  async identity(sid){
    try{const r=await fetch("/api/identity"+(sid?("?sid="+encodeURIComponent(sid)):""),{headers:authHeaders(),signal:AbortSignal.timeout(5000)});
      if(!r.ok)throw 0;return await r.json();}
    catch(e){return {user:"user",host:"ubuntu",home:"/home/user",cwd:"/home/user"};}
  },
  async pslist(){ try{const r=await fetch("/api/ps",{headers:authHeaders(),signal:AbortSignal.timeout(5000)});if(!r.ok)throw 0;return (await r.json()).rows;}catch(e){return null;} },
  async logs(type){ try{const r=await fetch("/api/logs?type="+encodeURIComponent(type),{headers:authHeaders(),signal:AbortSignal.timeout(5000)});if(!r.ok)throw 0;return (await r.json()).out;}catch(e){return null;} },
  // 安全审计：谁登录成功/失败、谁在试提权、谁被限流，带 IP 和 UA。
  // type=audit 时后端返回的是 rows 数组，不是 out 字符串。
  async audit(n){
    try{
      const r=await fetch("/api/logs?type=audit&n="+(n||60),{headers:authHeaders(),signal:AbortSignal.timeout(6000)});
      if(!r.ok) throw 0;
      return (await r.json()).rows;
    }catch(e){ return null; }
  },
  async disks(){ try{const r=await fetch("/api/disks",{headers:authHeaders(),signal:AbortSignal.timeout(5000)});if(!r.ok)throw 0;return (await r.json()).rows;}catch(e){return null;} }
};
window.API = API;

const $=(s)=>document.querySelector(s);
const $$=(s)=>document.querySelectorAll(s);
// ============ 鉴权头：从 sessionStorage 取登录令牌，作为 Bearer 发给后端 ============
// 关键修复：原先 /api/exec 等只靠登录时下发的 HttpOnly Cookie 鉴权。
// 一旦 Cookie 过期/被浏览器清掉/老 WebView 不存 fetch 的 Set-Cookie，
// 后端返回 401，前端只能报"服务未启动"——其实服务活得好好的。
// 现在前端显式带令牌，Cookie 失效也不影响命令/文件后端。后端 _token_from_request
// 已支持 Authorization: Bearer（server.py:475）。
function authHeaders(){
  let t=null;
  try{ t=sessionStorage.getItem("lsim_token"); }catch(e){}
  return t ? {"Authorization":"Bearer "+t} : {};
}
const state={ windows:[], z:100, wid:1, runningApps:new Set(), userApps:new Set() };
// 应用商店「软件」分类里的应用：默认不在桌面启动器显示，
// 需用户下载安装后才出现（由 state.userApps 控制）。镜像类不在此列。
const STORE_APPS=["Firefox","文本编辑器","终端","计算器","系统监视器","截图","图像查看器","磁盘","磁盘使用分析器","日历","天气","帮助"];

// 舞台坐标系（4:3 #stage 内部尺寸）
function getSW(){ return (($("#stage")||{}).clientWidth)||window.innerWidth; }
function getSH(){ return (($("#stage")||{}).clientHeight)||window.innerHeight; }

// 工作区：Dock 右侧、顶栏下方的可用区域（真 GNOME 里窗口不会跑到 Dock 底下）
const WORK = ()=>{
  const cs=getComputedStyle(document.documentElement);
  const dash=parseInt(cs.getPropertyValue("--dash-w"))||64;
  const top=parseInt(cs.getPropertyValue("--topbar-h"))||30;
  const sw=getSW(), sh=getSH();
  const L=8+dash+8, T=top, R=8, B=8;
  return {sw,sh,L,T,right:Math.max(L+80,sw-R),bottom:Math.max(T+120,sh-B)};
};

// 把本地文件上传到沙箱 userfiles 目录
async function uploadFiles(fileList){
  const files=[...fileList];
  if(!files.length)return;
  let ok=0, fail=0;
  for(const f of files){
    try{
      const b64=await new Promise((res,rej)=>{ const r=new FileReader(); r.onload=()=>res((r.result||"").split(",")[1]||""); r.onerror=rej; r.readAsDataURL(f); });
      const r=await fetch("/api/upload",{method:"POST",
        headers:Object.assign({"Content-Type":"application/json"}, authHeaders()),
        body:JSON.stringify({name:f.name,data:b64})});
      const j=await r.json();
      if(j.ok)ok++; else fail++;
    }catch(e){ fail++; }
  }
  alert(`已上传 ${ok} 个文件${fail?`（失败 ${fail} 个）`:""} 到「我的文件」\n可在 文件 → 我的文件 中查看`);
}

// 从电脑桌面直接拖拽文件进网页
(function(){
  const stop=(e)=>{e.preventDefault();e.stopPropagation();};
  ["dragenter","dragover","dragleave","drop"].forEach(t=>document.addEventListener(t,stop,false));
  let overlay=null;
  document.addEventListener("dragenter",(e)=>{ if(!e.dataTransfer||!e.dataTransfer.types||!e.dataTransfer.types.includes("Files"))return;
    if(!overlay){ overlay=document.createElement("div"); overlay.id="dropOverlay";
      overlay.innerHTML="<div class='do-box'>⬇️ 松开即可上传到沙箱</div>"; document.body.appendChild(overlay);} });
  document.addEventListener("dragleave",(e)=>{ if(e.relatedTarget===null&&overlay){overlay.remove();overlay=null;} });
  document.addEventListener("drop",(e)=>{ if(overlay){overlay.remove();overlay=null;} if(e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files.length)uploadFiles(e.dataTransfer.files); });
})();

// ============ 时钟 ============
function pad(n){return String(n).padStart(2,"0");}
function updateClocks(){
  const d=new Date();
  const time=`${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const wd=["日","一","二","三","四","五","六"][d.getDay()];
  $("#tbTime")&&($("#tbTime").textContent=time);
  $("#tbDate")&&($("#tbDate").textContent=`${d.getMonth()+1}月${d.getDate()}日 周${wd}`);
  $("#gdmClock")&&($("#gdmClock").textContent=time);
  $("#gdmDate")&&($("#gdmDate").textContent=`${d.getMonth()+1}月${d.getDate()}日 周${wd}`);
  $("#cpDate")&&($("#cpDate").textContent=`${d.getFullYear()}年${d.getMonth()+1}月${d.getDate()}日 周${wd}`);
  const cp=$("#cpCal");
  if(cp&&!cp.dataset.built){ buildCal(cp,d); cp.dataset.built="1"; }
}
function buildCal(el,d){
  const y=d.getFullYear(),m=d.getMonth();
  const first=new Date(y,m,1).getDay();
  const days=new Date(y,m+1,0).getDate();
  let html=`<div class="ch">日</div><div class="ch">一</div><div class="ch">二</div><div class="ch">三</div><div class="ch">四</div><div class="ch">五</div><div class="ch">六</div>`;
  for(let i=0;i<first;i++)html+=`<div class="cd other"></div>`;
  for(let i=1;i<=days;i++)html+=`<div class="cd${i===d.getDate()?" today":""}>${i}</div>`;
  el.innerHTML=html;
}
updateClocks(); setInterval(updateClocks,1000);

// ============ 登录流程 ============
// 登录态必须持久化：手机浏览器一旦因内存压力回收标签页/整页重载，
// 之前没有 sessionStorage 就会弹回 GDM 登录界面，用户以为"虚拟机崩了"。
// 存 JSON：{t:时间戳, on:true, w:[窗口名]}。on=已登录，w 用于恢复上次开着的窗口。
const SKEY="gnome.sim.session";
function readSession(){
  try{ return JSON.parse(sessionStorage.getItem(SKEY)||"null"); }catch(e){ return null; }
}
function saveSession(){
  try{ sessionStorage.setItem(SKEY,JSON.stringify({t:Date.now(),on:true,w:state.windows.map(r=>r.appName)})); }catch(e){}
}
$("#gdmUser").addEventListener("click",()=>{ $("#gdmUser").classList.add("hidden"); $("#gdmLogin").classList.remove("hidden"); setTimeout(()=>$("#gdmPass").focus(),50); });
function doLogin(){
  const p=$("#gdmPass").value;
  if(!p){ $("#gdmMsg").textContent="请输入密码"; return; }
  // 进度条：让用户直观看到"正在进入"，而不是点了没反应
  const prog=$("#gdmProg"), bar=$("#gdmProgBar"), txt=$("#gdmProgTxt");
  if(prog){ prog.style.display="block"; }
  if(bar){ bar.className="prog-bar busy"; bar.style.width="28%"; }
  if(txt){ txt.textContent="正在校验身份…"; }
  $("#gdmEnter").disabled=true;
  setTimeout(()=>{ if(bar) bar.style.width="66%"; if(txt) txt.textContent="正在加载桌面环境…"; },220);
  setTimeout(()=>{ if(bar) bar.style.width="100%"; if(txt) txt.textContent="即将进入…"; },480);
  setTimeout(enterDesktop,720);
}
// 进入桌面时拉取当前用户已装的软件，并入启动器（单用户隔离，仅自己装的才出现）
async function loadUserApps(){
  try{
    const r=await fetch("/api/store/mine",{headers:authHeaders()});
    if(!r.ok) return;
    const j=await r.json();
    state.userApps.clear();
    (j.items||[]).forEach(it=>{ if(it.type==="software" && it.appKey) state.userApps.add(it.appKey); });
  }catch(e){}
}
function enterDesktop(){
  // 先把锁屏收掉、桌面亮出来。这一步必须在任何可能抛异常的渲染之前，
  // 否则一旦某个渲染函数在老手机上出错，用户看到的就是"黑屏"。
  $("#gdm").classList.add("hidden"); $("#desktop").classList.remove("hidden");
  (async()=>{
    try{
      await loadUserApps();          // 先拿已装软件清单，再渲染启动器
      renderDash(); renderDesktopIcons(); renderOverview(); updateClocks();
    }catch(e){
      // 单个渲染模块失败不能拖垮整个桌面（壁纸/图标栏仍在），
      // 把错误留在控制台而不是让页面变黑。
      try{ console.error("桌面渲染异常:", e); }catch(_){}
    }
    const prev=readSession();        // 先读旧值再写新值，否则窗口列表会被覆盖丢失
    saveSession();
    try{ restoreOpenApps(prev); }catch(e){}
  })();
}
// 应用商店安装/卸载软件后刷新启动器（图标出现/消失）
window.addEventListener("store:appinstalled", e=>{
  const k=e&&e.detail&&e.detail.appKey; if(k) state.userApps.add(k);
  try{ renderOverview(); }catch(_){}
});
window.addEventListener("store:appuninstalled", e=>{
  const k=e&&e.detail&&e.detail.appKey; if(k) state.userApps.delete(k);
  try{ renderOverview(); }catch(_){}
});
// 崩溃/重载后把上次开着的应用重新拉起来
function restoreOpenApps(prev){
  const list=(prev && Array.isArray(prev.w)) ? prev.w : [];
  if(!list.length) return;
  list.forEach((name,i)=>setTimeout(()=>{ try{ openApp(name); }catch(e){} }, 260+i*220));
}
$("#gdmEnter").addEventListener("click",doLogin);
$("#gdmPass").addEventListener("keydown",e=>{ if(e.key==="Enter")doLogin(); });

// 同一会话内刷新/重载：直接回桌面并恢复窗口，不再要求重新登录
(function autoResume(){
  const s=readSession();
  if(s && s.on){ setTimeout(enterDesktop,0); return; }
  // 支持 /?app=v86 深链：自动登录并直接打开对应窗口
  // 支持 /?app=虚拟机&sys=tinycore：来自 /boot.html 引导界面，打开后自动选系统并启动
  const m=/[?&]app=([^&]+)/.exec(location.search);
  if(m && APP_REGISTRY[decodeURIComponent(m[1])]){
    const nm=decodeURIComponent(m[1]);
    const sm=/[?&]sys=([^&]+)/.exec(location.search);
    // 深链场景没有输入密码，直接进桌面（本地模拟器，不涉及真实鉴权）
    setTimeout(()=>{ enterDesktop(); setTimeout(()=>openApp(nm),420); },0);
    if(sm && nm==="虚拟机"){
      const sysKey=decodeURIComponent(sm[1]);
      // 等窗口渲染 + __v86win 就绪后，选中对应系统并自动启动
      setTimeout(()=>{
        try{
          const sel=document.querySelector("#v86Sys");
          if(sel && sel.querySelector("option[value='"+sysKey+"']")) sel.value=sysKey;
          if(window.__v86win && window.__v86win.start) window.__v86win.start();
        }catch(e){}
      },900);
    }
    return;
  }
  // 走到这里 = 没有 session 记录，但本页已经加载出来了。
  // 后端对 "/" 和 "/index.html" 都做了鉴权：未授权会被 302 到 /login。
  // 既然能加载出本页，就说明已经通过鉴权（cookie 有效）——无论是用
  // ?key= 链接直接进来的，还是手机清了 sessionStorage 后重新打开的。
  // 旧逻辑在这里什么都不做，于是停在近黑的 GDM 锁屏上，用户以为"黑屏卡死"。
  // 现在直接进桌面；GDM 仅在点 🔒 主动锁屏时才实时显示。
  setTimeout(enterDesktop,0);
})();

// ============ Dash ============
function renderDash(){
  const dash=$("#dash"), ov=$("#overviewDash");
  dash.innerHTML=""; ov.innerHTML="";
  const apps=Object.keys(APP_REGISTRY).filter(a=>APP_REGISTRY[a].dash);
  apps.forEach(name=>{
    const ic=document.createElement("div");
    ic.className="dash-icon"+(state.runningApps.has(name)?" run":"");
    ic.innerHTML=window.ICON(APP_REGISTRY[name].icon,28); ic.title=name;
    ic.onclick=()=>openApp(name);
    dash.appendChild(ic);
  });
  apps.forEach(name=>{
    const ic=document.createElement("div");ic.className="dash-icon";ic.innerHTML=window.ICON(APP_REGISTRY[name].icon,28);ic.title=name;
    ic.onclick=()=>openApp(name); ov.appendChild(ic);
  });
}
function refreshDashRun(){
  const dash=$("#dash");
  const apps=Object.keys(APP_REGISTRY).filter(a=>APP_REGISTRY[a].dash);
  [...dash.children].forEach((ic,idx)=>{ if(apps[idx]) ic.classList.toggle("run",state.runningApps.has(apps[idx])); });
}

// ============ 桌面图标 ============
// 手机上"双击"常被浏览器当成双击缩放而吞掉 dblclick，故触摸设备改为点一下直接打开；
// 鼠标仍保留 GNOME 语义：单击选中、双击打开。
const COARSE = !!(window.matchMedia && window.matchMedia("(pointer:coarse)").matches);
function renderDesktopIcons(){
  const wrap=$("#desktopIcons"); wrap.innerHTML="";
  // 全部为窗口内应用，不再有整页跳转 —— 低内存手机同时驻留两个页面会被系统杀掉
      const icons=[["主目录","主目录","文件"],["回收站","回收站","文件"],["此电脑","系统盘","磁盘"],
                   ["小游戏","小游戏","小游戏"],
                   ["虚拟机引导","电源","虚拟机引导"],["虚拟机","虚拟机","虚拟机"],
                   ["更新记录","更新记录","更新记录"],["问题反馈","问题反馈","问题反馈"]];
  icons.forEach(([lb,icKey,app])=>{
    const d=document.createElement("div");d.className="desktop-icon";
    d.innerHTML=`<div class="ic">${window.ICON(icKey,42)}</div><div class="lb">${lb}</div>`;
    let last=0;
    const open=()=>{
      const now=Date.now();
      if(now-last<450) return;              // 防止一次双击触发两次
      last=now;
      $$(".desktop-icon").forEach(x=>x.classList.remove("sel")); d.classList.add("sel");
      openApp(app);
    };
    d.addEventListener("click",(e)=>{
      e.stopPropagation();
      if(COARSE){ open(); return; }         // 触摸：点一下直接打开
      $$(".desktop-icon").forEach(x=>x.classList.remove("sel")); d.classList.add("sel"); // 鼠标：单击选中
    });
    d.addEventListener("dblclick",(e)=>{ e.stopPropagation(); if(!COARSE) open(); });    // 鼠标：双击打开
    wrap.appendChild(d);
  });
}

// ============ Overview ============
function renderOverview(){
  const grid=$("#overviewGrid"); grid.innerHTML="";
  Object.keys(APP_REGISTRY).forEach(name=>{
    // 商店软件未下载则不在启动器显示，下载后才出现（单用户隔离）
    if(STORE_APPS.includes(name) && !state.userApps.has(name)) return;
    const a=document.createElement("div");a.className="ov-app";a.dataset.name=name;
    a.innerHTML=`<div class="oic">${window.ICON(APP_REGISTRY[name].icon,40)}</div><div class="olb">${name}</div>`;
    a.onclick=()=>{ openApp(name); closeOverview(); };
    grid.appendChild(a);
  });
}
function openOverview(){ $("#overview").classList.remove("hidden"); $("#ovSearch").value=""; filterOverview(""); setTimeout(()=>$("#ovSearch").focus(),50); }
function closeOverview(){ $("#overview").classList.add("hidden"); }
function filterOverview(q){ $$(".ov-app").forEach(a=>{ a.style.display=a.dataset.name.includes(q)?"":"none"; }); }
$("#activitiesBtn").addEventListener("click",openOverview);
$("#ovSearch").addEventListener("input",e=>filterOverview(e.target.value));
$("#overview").addEventListener("click",e=>{ if(e.target.id==="overview")closeOverview(); });

// ============ 窗口管理 ============
function openApp(name){
  const def=APP_REGISTRY[name]; if(!def)return;
  // 单例应用：已开则聚焦已有窗口，避免手机连点造成多开、关不干净、左侧启动器一直亮「运行中」
  if(!def.multi && state.windows.some(w=>w.appName===name)){
    const ex=state.windows.filter(w=>w.appName===name).pop();
    if(ex){ focusWin(ex); return; }
  }
  state.runningApps.add(name); refreshDashRun();
  const win=document.createElement("div"); win.className="window focused";
  const id=state.wid++;
  const SW=getSW(), SH=getSH(), wa=WORK();
  const w=Math.min(def.width||780, wa.right-wa.L), h=Math.min(def.height||520, wa.bottom-wa.T);
  const x=Math.min(Math.max(wa.L,(wa.sw-w)/2+(id%5)*22-44), wa.right-w);
  const y=Math.min(Math.max(wa.T+4,56+(id%5)*18), wa.bottom-h);
  win.style.cssText=`left:${x}px;top:${y}px;width:${w}px;height:${h}px;z-index:${++state.z}`;
  win.innerHTML=`
    <div class="window-headerbar">
      <div class="window-title"><span class="wt-ic">${window.ICON(def.icon,18)}</span>${name}</div>
      <div class="window-controls">
        <button class="wc min"><span class="dot"></span></button>
        <button class="wc max"><span class="dot"></span></button>
        <button class="wc close"><span class="dot"></span></button>
      </div>
    </div>
    <div class="window-body"></div>`;
  $("#windows").appendChild(win);
  const body=win.querySelector(".window-body");
  body.innerHTML=def.render();
  if(def.onMount) def.onMount(body,win);

  const rec={id,appName:name,el:win,body,minimized:false};
  state.windows.forEach(w2=>w2.el.classList.remove("focused"));
  state.windows.push(rec);
  $("#appTitle").textContent=name;
  saveSession();

  win.querySelector(".min").onclick=(e)=>{e.stopPropagation();win.classList.add("hidden");rec.minimized=true;};
  win.querySelector(".max").onclick=(e)=>{e.stopPropagation();win.classList.toggle("maximized");
    // 最大化/还原会改变可用区域，通知窗口内应用重排（如 v86 需要重算缩放）
    win.dispatchEvent(new Event("winresize")); setTimeout(()=>win.dispatchEvent(new Event("winresize")),260);};
  win.querySelector(".close").onclick=(e)=>{e.stopPropagation();closeWin(rec);};
  win.querySelector(".window-body").onclick=()=>focusWin(rec);
  const hb=win.querySelector(".window-headerbar");
  hb.addEventListener("pointerdown",(e)=>{
    if(e.target.closest(".window-controls"))return;
    if(e.button!==undefined && e.button!==0)return;
    e.preventDefault();
    focusWin(rec);
    const sx=e.clientX, sy=e.clientY, ox=parseFloat(win.style.left)||0, oy=parseFloat(win.style.top)||0;
    win.classList.remove("maximized");
    const wa=WORK();
    const mxL=wa.L, mxR=Math.max(wa.L, wa.right-win.offsetWidth);
    const mxT=wa.T, mxB=Math.max(wa.T, wa.bottom-win.offsetHeight);
    try{ hb.setPointerCapture(e.pointerId); }catch(_){}
    function mv(ev){
      win.style.left=Math.min(Math.max(mxL, ox+ev.clientX-sx), mxR)+"px";
      win.style.top=Math.min(Math.max(mxT, oy+ev.clientY-sy), mxB)+"px";
    }
    function up(ev){
      hb.removeEventListener("pointermove",mv);
      hb.removeEventListener("pointerup",up);
      hb.removeEventListener("pointercancel",up);
      try{ hb.releasePointerCapture(ev.pointerId); }catch(_){}
    }
    hb.addEventListener("pointermove",mv);
    hb.addEventListener("pointerup",up);
    hb.addEventListener("pointercancel",up);
  });
  focusWin(rec);
  setTimeout(()=>{ const __f=body.querySelector("input,textarea"); if(__f)__f.focus(); },30);
}
function focusWin(rec){ state.windows.forEach(w=>w.el.classList.remove("focused")); rec.el.classList.remove("hidden"); rec.minimized=false; rec.el.classList.add("focused"); rec.el.style.zIndex=++state.z; $("#appTitle").textContent=rec.appName; }
function closeWin(rec){
  // 先派发 closed：让应用在这里释放重资源（如 v86 的 WASM 线性内存），
  // 再移除 DOM。低内存手机必须做，否则关掉窗口内存也不还给系统。
  try{ rec.el.dispatchEvent(new Event("closed")); }catch(e){}
  rec.el.remove(); state.windows=state.windows.filter(w=>w.id!==rec.id);
  if(!state.windows.some(w=>w.appName===rec.appName))state.runningApps.delete(rec.appName);
  refreshDashRun();
  saveSession();
  $("#appTitle").textContent=state.windows.length?state.windows[state.windows.length-1].appName:"";
}

// ============ 顶栏弹窗 ============
$("#topbarClock").addEventListener("click",(e)=>{
  e.stopPropagation(); toggle("#calendarPopup"); $("#qsPopup").classList.add("hidden"); $("#powerMenu").classList.add("hidden");
});
[...$("#topbarRight").children].forEach(ch=>{
  if(ch.classList.contains("tb-power")){
    ch.addEventListener("click",(e)=>{ e.stopPropagation(); toggle("#powerMenu"); $("#calendarPopup").classList.add("hidden"); $("#qsPopup").classList.add("hidden"); });
  } else if(ch.title==="声音"||ch.title==="网络"||ch.title==="蓝牙"){
    ch.addEventListener("click",(e)=>{ e.stopPropagation(); toggle("#qsPopup"); $("#calendarPopup").classList.add("hidden"); $("#powerMenu").classList.add("hidden"); });
  }
});
function toggle(sel){ const el=$(sel); el.classList.toggle("hidden"); }

$$("#qsPopup .qs-tile").forEach(t=>{
  t.onclick=()=>{
    t.classList.toggle("active");
    const sub=t.querySelector(".qs-sub");
    if(sub){
      if(t.classList.contains("active")){ if(t.dataset.qs!=="dark"&&t.dataset.qs!=="wifi"){sub.textContent="已开启";} sub.classList.add("on"); }
      else { sub.textContent="关闭"; sub.classList.remove("on"); }
    }
    if(t.dataset.qs==="dark"){ document.body.classList.toggle("dark"); $("#qsDarkSub").textContent=t.classList.contains("active")?"开启":"关闭"; }
    if(t.dataset.qs==="wifi"){ sub.textContent=t.classList.contains("active")?"已连接":"关闭"; }
  };
});
$("#qsVol").oninput=e=>$("#qsVolVal").textContent=e.target.value;
$("#qsBri").oninput=e=>$("#qsBriVal").textContent=e.target.value;
$("#qsSettings").onclick=()=>{ $("#qsPopup").classList.add("hidden"); openApp("设置"); };
$("#qsLock").onclick=()=>{ $("#qsPopup").classList.add("hidden"); lockScreen(); };

// 电源 / 用户菜单
$("#powerMenu").addEventListener("click",e=>{
  const it=e.target.closest(".pm-item"); if(!it)return;
  const act=it.dataset.act;
  if(act==="settings"){ $("#powerMenu").classList.add("hidden"); openApp("设置"); }
  else if(act==="restart"||act==="shutdown"){ showPowerAnim(act); }
  else { $("#powerMenu").classList.add("hidden"); lockScreen(); }
});
function lockScreen(){
  $("#desktop").classList.add("hidden");
  $("#gdmLogin").classList.add("hidden");
  $("#gdmUser").classList.remove("hidden");
  $("#gdm").classList.remove("hidden");
  $("#gdmPass").value=""; updateClocks();
  // 主动锁屏才清会话；崩溃/重载导致的恢复不清
  try{ sessionStorage.removeItem(SKEY); }catch(e){}
}
function showPowerAnim(act){
  const m=document.createElement("div");
  m.style.cssText="position:fixed;inset:0;background:#000;color:#555;display:flex;align-items:center;justify-content:center;font-size:18px;z-index:99999";
  m.textContent=act==="shutdown"?"正在关机…":"正在重启…";
  document.body.appendChild(m); setTimeout(()=>m.remove(),2500);
}

// 桌面右键菜单
$("#desktop").addEventListener("contextmenu",e=>{
  if(e.target.closest(".window"))return;
  e.preventDefault();
  const m=$("#ctxMenu"); m.classList.remove("hidden");
  m.style.left=Math.min(e.clientX,window.innerWidth-210)+"px";
  m.style.top=Math.min(e.clientY,window.innerHeight-260)+"px";
});
$("#ctxMenu").addEventListener("click",e=>{
  const it=e.target.closest(".ctx-item"); if(!it)return;
  const act=it.dataset.act;
  if(act==="openTerminal")openApp("终端");
  else if(act==="settings"||act==="display"||act==="changeBg")openApp("设置");
  else if(act==="overview")openOverview();
  else if(act==="newFolder")alert("已在桌面创建“新建文件夹”（模拟）");
  $("#ctxMenu").classList.add("hidden");
});

// 全局点击关闭弹窗
document.addEventListener("click",e=>{
  if(!e.target.closest("#calendarPopup")&&!e.target.closest("#topbarClock"))$("#calendarPopup").classList.add("hidden");
  if(!e.target.closest("#qsPopup")&&!e.target.closest(".tb-ind"))$("#qsPopup").classList.add("hidden");
  if(!e.target.closest("#powerMenu")&&!e.target.closest(".tb-power"))$("#powerMenu").classList.add("hidden");
  if(!e.target.closest("#ctxMenu"))$("#ctxMenu").classList.add("hidden");
});
$("#desktop").addEventListener("click",e=>{
  if(e.target.id==="desktop"||e.target.classList.contains("wallpaper")){
    $("#ctxMenu").classList.add("hidden");
    $$(".desktop-icon").forEach(x=>x.classList.remove("sel"));
  }
});

// 键盘快捷键
document.addEventListener("keydown",e=>{
  if(e.key==="Escape"){ closeOverview(); $("#calendarPopup").classList.add("hidden"); $("#qsPopup").classList.add("hidden"); $("#powerMenu").classList.add("hidden"); $("#ctxMenu").classList.add("hidden"); }
  if(e.key==="Meta"){ e.preventDefault(); $("#overview").classList.toggle("hidden"); if(!$("#overview").classList.contains("hidden"))setTimeout(()=>$("#ovSearch").focus(),50); }
});

// ============ GNOME Alt+Tab 切换器 ============
let switcher=null, switcherApps=[], switcherIdx=0;
function uniqueRunningApps(){
  const seen=new Set(), arr=[];
  state.windows.slice().reverse().forEach(w=>{ if(!seen.has(w.appName)){ seen.add(w.appName); arr.push(w.appName); } });
  return arr;
}
function openSwitcher(){
  switcherApps=uniqueRunningApps();
  if(!switcherApps.length)return;
  switcherIdx=switcherApps.length-1;
  if(switcher)switcher.remove();
  switcher=document.createElement("div"); switcher.className="switcher";
  document.body.appendChild(switcher);
  renderSwitcher();
}
function renderSwitcher(){
  if(!switcher)return;
  switcher.innerHTML=`<div class="sw-row">`+switcherApps.map((n,i)=>
    `<div class="sw-item${i===switcherIdx?" active":""}"><div class="sw-ic">${APP_REGISTRY[n].icon}</div></div>`).join("")+
    `</div><div class="sw-label">${switcherApps[switcherIdx]}</div>`;
}
function closeSwitcher(){
  if(!switcher)return;
  const name=switcherApps[switcherIdx];
  const wins=state.windows.filter(w=>w.appName===name);
  if(wins.length) focusWin(wins[wins.length-1]);
  switcher.remove(); switcher=null;
}
document.addEventListener("keydown",e=>{
  if(e.key==="Tab" && e.altKey){
    e.preventDefault();
    if(e.repeat)return;
    if(!switcher) openSwitcher();
    else { switcherIdx=(switcherIdx+1)%switcherApps.length; renderSwitcher(); }
  }
});
document.addEventListener("keyup",e=>{ if(e.key==="Alt" && switcher) closeSwitcher(); });
