// ============ Ubuntu 24.04 应用注册表 ============
const APP_REGISTRY = {
  "文件": { icon:"文件", dash:true, multi:true, render: () => `
    <div class="files">
      <div class="files-side">
        <div class="files-nav-h">主目录</div>
        <div class="files-nav" data-go="/home/user">${window.ICON('主目录',17)} 主目录</div>
        <div class="files-nav" data-go="/home/user/下载">${window.ICON('下载',17)} 下载</div>
        <div class="files-nav" data-go="/home/user/图片">${window.ICON('图片',17)} 图片</div>
        <div class="files-nav" data-go="/home/user/文档">${window.ICON('文档',17)} 文档</div>
        <div class="files-nav" data-go="/workspace/linux-sim/userfiles">${window.ICON('我的文件',17)} 我的文件</div>
        <div class="files-nav-h">此电脑</div>
        <div class="files-nav" data-go="/">${window.ICON('系统盘',17)} 系统盘 (/)</div>
        <div class="files-nav" data-go="/workspace">${window.ICON('workspace',17)} workspace</div>
        <div class="files-nav" data-go="/etc">${window.ICON('etc',17)} /etc</div>
        <div class="files-nav" data-go="/home">${window.ICON('home',17)} /home</div>
        <div class="files-nav-h">其他位置</div>
        <div class="files-nav" data-go="/tmp">${window.ICON('tmp',17)} /tmp</div>
        <div class="files-nav" data-go="/var">${window.ICON('var',17)} /var</div>
        <div class="files-nav" data-go="/usr">${window.ICON('usr',17)} /usr</div>
      </div>
      <div class="files-main">
        <div class="files-toolbar">
          <button class="ftb-btn" id="ftbBack" title="后退">←</button>
          <button class="ftb-btn" id="ftbFwd" title="前进">→</button>
          <button class="ftb-btn" id="ftbUp" title="上一级">↑</button>
          <button class="ftb-btn" id="ftbReload" title="重新加载">⟳</button>
          <div class="files-pathbar" id="filesPathbar"></div>
        </div>
        <div class="files-list" id="filesList"></div>
        <div class="files-status" id="filesStatus">加载中…</div>
      </div>
    </div>`,
    onMount: (body, win) => {
      const list = body.querySelector("#filesList");
      const status = body.querySelector("#filesStatus");
      const pathbar = body.querySelector("#filesPathbar");
      const history=[]; let hi=-1;
      let realRoot = "/home/user";
      (window.API&&window.API.exec?window.API.exec("echo $HOME"):Promise.resolve({out:"/home/user\n"})).then(r=>{
        realRoot=(r.out||"/home/user").trim()||"/home/user";
        if(!win._curPath) load(realRoot);
      });
      function iconFor(name,type){
        const e=(name.split(".").pop()||"").toLowerCase();
        return window.FILE_ICON(type==="file"?e:type);
      }
      function parseLs(txt){
        const rows=[];
        txt.split("\n").forEach(line=>{
          if(!line.trim())return;
          const m=line.match(/^(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+)$/);
          if(!m)return;
          let name=m[8], target=null;
          const li=name.indexOf(" -> "); if(li>=0){ target=name.slice(li+4); name=name.slice(0,li); }
          const type=m[1][0]==="d"?"dir":(m[1][0]==="l"?"link":"file");
          rows.push({name,type,size:parseInt(m[5]),mtime:m[6]+" "+m[7],target});
        });
        return rows.filter(r=>r.name!=="."&&r.name!=="..");
      }
      function fmt(size){ if(size>=1073741824)return (size/1073741824).toFixed(1)+" GB"; if(size>=1048576)return (size/1048576).toFixed(1)+" MB"; if(size>=1024)return (size/1024).toFixed(1)+" KB"; return size+" B"; }
      async function load(path, pushHist=true){
        list.innerHTML=`<div class="files-cell" style="opacity:.5">加载中…</div>`;
        const safe=("/"+path).replace(/\/+/g,"/");
        const r=await (window.API&&window.API.exec?window.API.exec(`ls -la --time-style=long-iso "${safe}"`):Promise.resolve({err:"无后端"}));
        if(r.err && !r.out){ status.textContent="无法读取："+(r.err||"未知错误"); list.innerHTML=`<div class="files-cell" style="opacity:.5">无法访问该目录</div>`; return; }
        const rows=parseLs(r.out);
        rows.sort((a,b)=> (a.type===b.type? a.name.localeCompare(b.name): (a.type==="dir"?-1:1)));
        list.innerHTML="";
        rows.forEach(it=>{
          const c=document.createElement("div"); c.className="files-cell"; c.dataset.name=it.name; c.dataset.type=it.type;
          c.innerHTML=`<div class="fc-ic">${iconFor(it.name,it.type)}</div><div class="fc-lb">${it.name}</div>`;
          c.onclick=(e)=>{e.stopPropagation();list.querySelectorAll(".files-cell").forEach(x=>x.classList.remove("sel"));c.classList.add("sel");};
          c.ondblclick=async()=>{
            if(it.type==="dir"){ navigate(safe+"/"+it.name); }
            else {
              const txt=await (window.API&&window.API.exec?window.API.exec(`cat "${safe}/${it.name}"`):Promise.resolve({out:""}));
              if(txt.err&&!txt.out){ alert("无法读取文件内容（可能是二进制或无权访问）"); return; }
              window.pendingFile={name:it.name, content:txt.out, path:safe+"/"+it.name};
              openApp("文本编辑器");
            }
          };
          list.appendChild(c);
        });
        status.textContent=`${rows.length} 个项目 · 真实路径 ${safe}`;
        pathbar.innerHTML=""; const parts=safe.split("/").filter(Boolean);
        pathbar.appendChild(seg("/", safe||"/"));
        let acc=""; parts.forEach(p=>{ acc+="/"+p; pathbar.appendChild(seg(p, acc)); });
        function seg(label,pp){ const s=document.createElement("span"); s.className="files-path-seg"; s.textContent=label||"/"; s.onclick=()=>navigate(pp); return s; }
        win._curPath=safe;
        if(pushHist){ history.splice(hi+1); history.push(safe); hi=history.length-1; }
        updateHistBtns();
      }
      function navigate(path){ const p=path.replace(/\/+/g,"/"); load(p); }
      function updateHistBtns(){ body.querySelector("#ftbBack").disabled=hi<=0; body.querySelector("#ftbFwd").disabled=hi>=history.length-1; }
      body.querySelector("#ftbBack").onclick=()=>{ if(hi>0){hi--; load(history[hi],false);} };
      body.querySelector("#ftbFwd").onclick=()=>{ if(hi<history.length-1){hi++; load(history[hi],false);} };
      body.querySelector("#ftbUp").onclick=()=>{ const p=(win._curPath||"/").replace(/\/[^\/]*$/,"")||"/"; navigate(p); };
      body.querySelector("#ftbReload").onclick=()=>load(win._curPath||"/",false);
      body.querySelectorAll(".files-nav").forEach(n=>n.onclick=()=>{ body.querySelectorAll(".files-nav").forEach(x=>x.classList.remove("active")); n.classList.add("active"); navigate(n.dataset.go); });
      if(!win._curPath) load(realRoot); else load(win._curPath);
    }
  },

  "Firefox": { icon:"Firefox", dash:true, render: () => `
    <div class="ff">
      <div class="ff-bar">
        <div class="ff-nav"><button id="ffBack">←</button><button>→</button><button id="ffReload">⟳</button></div>
        <input class="ff-url" id="ffUrl" value="https://www.bing.com">
        <button style="background:#e6e5e4;border-radius:50%;width:30px;height:30px">🔒</button>
      </div>
      <div class="ff-view">
        <div class="ff-home" id="ffHome">
          <div class="ff-logo">🦊</div>
          <div style="font-size:20px;color:#333">Firefox 浏览器</div>
          <div class="ff-search">🔍 <input id="ffSearch" placeholder="搜索或输入网址"></div>
          <div style="display:flex;gap:20px;color:#666;font-size:12px">
            <span>📰 热门网站</span><span>📚 书签</span><span>🕒 历史</span><span>⬇️ 下载</span>
          </div>
        </div>
        <iframe class="ff-frame" id="ffFrame" referrerpolicy="no-referrer"></iframe>
      </div>
    </div>`,
    onMount:(body)=>{
      const frame=body.querySelector("#ffFrame"), home=body.querySelector("#ffHome"), url=body.querySelector("#ffUrl");
      function go(u){
        u=(u||"").trim(); if(!u)return;
        if(!/^https?:\/\//i.test(u)){
          if(/^[\w-]+(\.[\w-]+)+([\/?#].*)?$/.test(u)) u="https://"+u;
          else u="https://www.bing.com/search?q="+encodeURIComponent(u);
        }
        url.value=u;
        home.style.display="none"; frame.style.display="block";
        frame.src="/api/proxy?url="+encodeURIComponent(u);
      }
      body.querySelector("#ffSearch").onkeydown=(e)=>{ if(e.key==="Enter")go(e.target.value); };
      url.onkeydown=(e)=>{ if(e.key==="Enter")go(e.target.value); };
      body.querySelector("#ffReload").onclick=()=>{ if(frame.src&&frame.style.display!=="none")frame.src=frame.src; };
      body.querySelector("#ffBack").onclick=()=>{ home.style.display="flex"; frame.style.display="none"; frame.src="about:blank"; };
    }
  },

  "文本编辑器": { icon:"文本编辑器", dash:true, render: () => `
    <div class="editor">
      <div class="editor-bar">
        <button id="edOpen">打开</button><button id="edSave">保存</button>
        <span id="edName" style="margin-left:auto;color:#999;font-size:12px;align-self:center">未命名文档 1</span>
      </div>
      <textarea class="editor-area" placeholder="在此输入文本…(可双击文件管理器中的文本文件用真实内容打开)"></textarea>
    </div>`,
    onMount:(body,win)=>{
      const area=body.querySelector(".editor-area");
      const nameLbl=body.querySelector("#edName");
      if(window.pendingFile){
        area.value=window.pendingFile.content||"";
        win._filePath=window.pendingFile.path;
        win.querySelector(".window-title").innerHTML=`<span class="wt-ic">📝</span> ${window.pendingFile.name}`;
        nameLbl.textContent=window.pendingFile.path;
        window.pendingFile=null;
      }
      body.querySelector("#edSave").onclick=async()=>{
        let base=win._filePath?win._filePath.split("/").pop():(nameLbl.textContent||"").trim();
        if(!base||base==="未命名文档 1")base="未命名文档.txt";
        if(!/\.[a-z0-9]+$/i.test(base))base+=".txt";
        try{
          const r=await fetch("/api/write",{method:"POST",
            headers:Object.assign({"Content-Type":"application/json"}, (window.authHeaders?window.authHeaders():{})),
            body:JSON.stringify({name:base,content:area.value})});
          const j=await r.json();
          if(j.ok){ nameLbl.textContent="我的文件/"+base; alert("已保存到沙箱：\n"+j.dir+"/"+base); }
          else alert("保存失败："+(j.err||"未知错误"));
        }catch(e){ alert("保存失败：无法连接后端"); }
      };
      body.querySelector("#edOpen").onclick=()=>openApp("文件");
    }
  },

  "终端": { icon:"终端", dash:true, multi:true, render: () => `<div class="term" id="termBody"></div>`,
    onMount:(body,win)=>{
      const term=body.querySelector("#termBody");
      const state={hist:[],hi:0,cwd:"/home/user",home:"/home/user",user:"user",host:"ubuntu",
                   sid:"t"+Date.now().toString(36)+Math.random().toString(36).slice(2,6)};
      const esc=(s)=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
      function addLine(html,cls){const d=document.createElement("div");d.className="term-line"+(cls?" "+cls:"");d.innerHTML=html;term.appendChild(d);term.scrollTop=term.scrollHeight;}
      function pretty(p){ if(p===state.home)return "~"; const h=state.home||""; if(h&&p.startsWith(h+"/"))return "~"+p.slice(h.length); return p||"~"; }
      function ps1(){ return `${state.user}@${state.host}:${pretty(state.cwd)}$`; }
      function newPrompt(){
        const row=document.createElement("div");row.className="term-in";
        row.innerHTML=`<span class="term-prompt">${esc(ps1())}</span>`;
        const inp=document.createElement("input");inp.className="term-input";inp.autofocus=true;
        row.appendChild(inp);term.appendChild(row);term.scrollTop=term.scrollHeight;inp.focus();
        inp.onkeydown=async(e)=>{
          if(e.isComposing) return;   // 移动端输入法组合期间忽略，防回车被重复派发
          if(e.ctrlKey&&e.key.toLowerCase()==="l"){e.preventDefault();term.innerHTML="";newPrompt();return;}
          if(e.ctrlKey&&e.key.toLowerCase()==="c"){e.preventDefault();
            addLine(`<span class="term-prompt">${esc(ps1())}</span> ${esc(inp.value)}^C`);newPrompt();return;}
          if(e.key==="Enter"){
            e.preventDefault();             // 阻止软键盘默认（换行/二次派发）
            if(term._busy) return;          // 防重入：处理中的回车忽略
            term._busy=true; setTimeout(()=>{term._busy=false;},80);
            const cmd=inp.value; inp.disabled=true;
            const t=cmd.trim();
            addLine(`<span class="term-prompt">${esc(ps1())}</span> ${esc(cmd)}`);
            if(t){state.hist.push(cmd);state.hi=state.hist.length;}
            if(t==="clear"){term.innerHTML="";newPrompt();return;}
            if(t==="exit"||t==="logout"){ addLine("logout");
              setTimeout(()=>{ const c=win&&win.querySelector(".close"); if(c)c.click(); },250); return; }
            if(!t){newPrompt();return;}
            // 装包走后端 root 代理（该账号没有免密 sudo，这是有意的安全设计）
            if(/^install\s+/.test(t)){
              const pkgs=t.replace(/^install\s+/,"").trim();
              addLine(`<span style="color:#8b8b8b">正在安装 ${esc(pkgs)} …（首次可能较慢）</span>`);
              addLine("term-wait");
              try{
                const rr=await fetch("/api/install",{method:"POST",headers:Object.assign({"Content-Type":"application/json"}, (window.authHeaders?window.authHeaders():{})),
                                                 body:JSON.stringify({pkgs:pkgs})});
                const j=await rr.json();
                const w=term.lastElementChild; if(w&&w.classList.contains("term-wait"))w.remove();
                if(j.out)addLine(esc(j.out));
                if(j.err)addLine(esc(j.err),"term-err");
              }catch(e){
                const w=term.lastElementChild; if(w&&w.classList.contains("term-wait"))w.remove();
                addLine("安装请求失败："+esc(String(e)),"term-err");
              }
              newPrompt(); return;
            }
            const r=await window.API.exec(cmd,state.cwd,state.sid);
            if(r.out)addLine(esc(r.out));
            if(r.err)addLine(esc(r.err),"term-err");
            if(r.cwd)state.cwd=r.cwd;
            newPrompt();
          } else if(e.key==="ArrowUp"){ if(state.hi>0){state.hi--;inp.value=state.hist[state.hi]||"";} e.preventDefault(); }
          else if(e.key==="ArrowDown"){ if(state.hi<state.hist.length-1){state.hi++;inp.value=state.hist[state.hi]||"";}else{inp.value="";} e.preventDefault(); }
          else if(e.key==="Tab"){ e.preventDefault(); }
        };
      }
      addLine("欢迎使用 Ubuntu 24.04.3 LTS (GNOME 46)");
      (async()=>{
        try{ const w=await window.API.identity(state.sid);
          state.user=w.user||"user"; state.host=w.host||"ubuntu"; state.home=w.home||"/home/user"; state.cwd=w.cwd||state.home;
        }catch(e){}
        addLine(`终端已连接真实 bash（以 <b>${esc(state.user)}@${esc(state.host)}</b> 身份运行）。输入 <span class='term-link'>help</span> 或任意 Linux 命令。`);
        addLine(`<span style="color:#8b8b8b">提示：出于安全考虑该账号无免密 sudo；装软件请用「软件中心」或 <span class='term-link'>install 包名</span>。</span>`);
        addLine("");
        newPrompt();
        setTimeout(()=>{ const __f=body.querySelector(".term-input"); if(__f)__f.focus(); },50);
      })();
    }
  },

  "计算器": { icon:"🧮", dash:true, render: () => `
    <div class="calc">
      <div class="calc-disp" id="calcDisp">0</div>
      <div class="calc-keys" id="calcKeys"></div>
    </div>`,
    onMount:(body)=>{
      const disp=body.querySelector("#calcDisp");
      const keys=["C","±","%","÷","7","8","9","×","4","5","6","−","1","2","3","+","0",".","="];
      const kc=body.querySelector("#calcKeys");
      let cur="0",op=null,prev=null;
      const render=()=>disp.textContent=cur;
      keys.forEach(k=>{
        const b=document.createElement("button");b.className="calc-key";b.textContent=k;
        if("÷×−+=".includes(k))b.classList.add("op");
        if(k==="=")b.classList.add("eq");
        if("C±%".includes(k))b.classList.add("fn");
        b.onclick=()=>{
          if(/[0-9.]/.test(k)){ cur = cur==="0"?k:cur+k; }
          else if(k==="C"){cur="0";op=null;prev=null;}
          else if(k==="±"){cur=String(parseFloat(cur)*-1);}
          else if(k==="%"){cur=String(parseFloat(cur)/100);}
          else if("÷×−+".includes(k)){ if(op&&prev!==null)calc(); op=k; prev=parseFloat(cur); cur="0"; }
          else if(k==="="){ if(op&&prev!==null)calc(); op=null; prev=null; }
          render();
        };
        kc.appendChild(b);
      });
      function calc(){
        const a=prev,b=parseFloat(cur);
        const m={"÷":a/b,"×":a*b,"−":a-b,"+":a+b};
        cur=String(m[op]==null?b:m[op]);
      }
    }
  },

  "设置": { icon:"⚙️", dash:true, render: () => `
    <div class="settings">
      <div class="settings-side" id="setSide"></div>
      <div class="settings-main" id="setMain"></div>
    </div>`,
    onMount:(body)=>{
      const pages={
        "网络":`<div class="settings-h">网络</div>
          <div class="settings-card"><div><div class="l">Wi‑Fi</div><div class="d">已连接 · UbuntuNet</div></div><div class="toggle on" data-t></div></div>
          <div class="settings-card"><div><div class="l">有线</div><div class="d">已连接 · 1 Gbps</div></div><div class="toggle on" data-t></div></div>
          <div class="settings-card"><div><div class="l">VPN</div><div class="d">未连接</div></div><div class="toggle" data-t></div></div>`,
        "蓝牙":`<div class="settings-h">蓝牙</div><div class="settings-card"><div><div class="l">蓝牙</div><div class="d">关闭</div></div><div class="toggle" data-t></div></div>`,
        "背景":`<div class="settings-h">背景</div><div class="settings-card"><div><div class="l">壁纸</div><div class="d">Noble Numbat</div></div><span style="color:#999">›</span></div>`,
        "电源":`<div class="settings-h">电源</div><div class="settings-card"><div><div class="l">省电模式</div><div class="d">关闭</div></div><div class="toggle" data-t></div></div><div class="settings-card"><div><div class="l">息屏时间</div><div class="d">5 分钟</div></div><span style="color:#999">›</span></div>`,
        "显示":`<div class="settings-h">显示</div><div class="settings-card"><div><div class="l">分辨率</div><div class="d">1920 × 1080</div></div><span style="color:#999">›</span></div><div class="settings-card"><div><div class="l">夜间模式</div><div class="d">关闭</div></div><div class="toggle" data-t></div></div>`,
        "声音":`<div class="settings-h">声音</div><div class="settings-card"><div><div class="l">输出音量</div><div class="d">65%</div></div></div>`,
        "关于":`<div class="settings-h">关于</div>
          <div style="display:flex;gap:16px;align-items:center;margin-bottom:18px"><div style="font-size:48px">🐧</div><div><div style="font-size:18px;font-weight:600">Ubuntu 24.04.3 LTS</div><div style="color:#777">Noble Numbat</div></div></div>
          <div class="settings-about" id="aboutInfo"></div>`,
        "日期和时间":`<div class="settings-h">日期和时间</div><div class="settings-card"><div><div class="l">自动设置时间</div></div><div class="toggle on" data-t></div></div><div class="settings-card"><div><div class="l">时区</div><div class="d">亚洲/上海 (CST, UTC+8)</div></div></div>`,
        "用户":`<div class="settings-h">用户</div><div class="settings-card"><div><div class="l">User</div><div class="d">管理员</div></div></div>`,
        "隐私":`<div class="settings-h">隐私与安全</div><div class="settings-card"><div><div class="l">屏幕共享</div></div><div class="toggle" data-t></div></div><div class="settings-card"><div><div class="l">位置服务</div></div><div class="toggle" data-t></div></div>`,
        "通知":`<div class="settings-h">通知</div><div class="settings-card"><div><div class="l">通知弹窗</div></div><div class="toggle on" data-t></div></div>`
      };
      const side=body.querySelector("#setSide");
      const main=body.querySelector("#setMain");
      Object.keys(pages).forEach((p,i)=>{
        const n=document.createElement("div");n.className="settings-nav"+(i===0?" active":"");n.textContent=p;
        n.onclick=()=>{side.querySelectorAll(".settings-nav").forEach(x=>x.classList.remove("active"));n.classList.add("active");main.innerHTML=pages[p];bindToggles(main);if(p==="关于")fillAbout(main);};
        side.appendChild(n);
      });
      main.innerHTML=pages["网络"];bindToggles(main);
      function bindToggles(el){el.querySelectorAll("[data-t]").forEach(t=>t.onclick=()=>t.classList.toggle("on"));}
      function fillAbout(el){
        const info=el.querySelector("#aboutInfo");
        const rows=[
          ["设备名称","ubuntu-vm"],
          ["处理器","32 核 x86_64"],
          ["内存","123.0 GiB"],
          ["磁盘","256 GB (已用 110 MB)"],
          ["图形","SVGA3D / LLVMPIPE"],
          ["系统版本","Ubuntu 24.04.3 LTS"],
          ["GNOME 版本","46"],
          ["窗口系统","X11 / Wayland"],
          ["内核","6.6.117"]
        ];
        info.innerHTML=rows.map(r=>`<div class="k">${r[0]}</div><div class="v">${r[1]}</div>`).join("");
      }
    }
  },

  "系统监视器": { icon:"📊", dash:false, render: () => `
    <div style="height:100%;display:flex;flex-direction:column">
      <div class="files-pathbar"><b>进程</b></div>
      <div style="flex:1;overflow:auto"><table class="mon-table" id="monTable"></table></div>
    </div>`,
    onMount:async(body)=>{
      const t=body.querySelector("#monTable");
      t.innerHTML=`<tr><th>PID</th><th>名称</th><th>用户</th><th>CPU%</th><th>内存</th></tr>`;
      const data=await (window.API&&window.API.pslist?window.API.pslist():Promise.resolve(null));
      const rows=data&&data.length?data:[
        {pid:1,cmd:"systemd",user:"root",cpu:0,mem:"12.4 MiB"},
        {pid:1234,cmd:"gnome-shell",user:"user",cpu:3.2,mem:"184 MiB"},
        {pid:2048,cmd:"Xwayland",user:"user",cpu:1.1,mem:"64 MiB"},
        {pid:3156,cmd:"firefox",user:"user",cpu:8.7,mem:"1.2 GiB"},
        {pid:3301,cmd:"gnome-terminal",user:"user",cpu:0.4,mem:"42 MiB"}
      ];
      rows.slice(0,14).forEach(r=>{
        const tr=document.createElement("tr");
        tr.innerHTML=`<td>${r.pid}</td><td>${r.cmd}</td><td>${r.user}</td><td>${r.cpu}</td><td>${r.mem}</td>`;
        t.appendChild(tr);
      });
      const note=document.createElement("div");note.style.cssText="padding:8px 12px;color:#777;font-size:11px";
      note.textContent="数据"+(data?"来自真实 ps 命令":"为演示模拟");t.parentElement.appendChild(note);
    }
  },

  "软件": { icon:"软件", dash:true, render: () => `
    <div class="sw">
      <div class="sw-side">
        <div class="sw-nav active" data-tab="software">📦 软件</div>
        <div class="sw-nav" data-tab="image">💿 系统镜像</div>
        <div class="sw-nav" data-tab="games">🎮 游戏</div>
        <div class="sw-nav" data-tab="mine">📥 已安装</div>
      </div>
      <div class="sw-main">
        <div style="font-size:22px;font-weight:300;margin-bottom:4px">应用商店</div>
        <div style="font-size:12px;opacity:.6;margin-bottom:12px" id="swSub">下载软件到你的账号，仅你可见、不影响他人</div>
        <div id="swList"></div>
      </div>
    </div>`,
    onMount:(body)=>{
      const list=body.querySelector("#swList");
      const sub=body.querySelector("#swSub");
      const side=body.querySelector(".sw-side");
      let catalog=[], mine={};
      const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
      const card=(it)=>{
        const installed=!!mine[it.id];
        let btn;
        if(it.unsupported){
          btn=`<button class="sw-btn sw-disabled" disabled title="${esc(it.desc)}">暂不支持</button>`;
        }else if(installed){
          btn=`<button class="sw-btn sw-inst" data-act="open" data-id="${esc(it.id)}">打开</button>`
             +`<button class="sw-btn sw-del" data-act="uninstall" data-id="${esc(it.id)}">卸载</button>`;
        }else{
          btn=`<button class="sw-btn sw-add" data-act="install" data-id="${esc(it.id)}">安装</button>`;
        }
        return `<div class="sw-card"><div class="sw-ic">${esc(it.icon||"📦")}</div>`
          +`<div class="sw-info"><div class="sw-name">${esc(it.name)}</div>`
          +`<div class="sw-desc">${esc(it.desc)}</div>`
          +`<div class="sw-meta">${esc(it.category||"")} · ${esc(it.size||"")}</div></div>`
          +`<div class="sw-act">${btn}</div></div>`;
      };
      const renderSoftware=()=>{
        sub.textContent="下载软件到你的账号，仅你可见、不影响他人";
        list.innerHTML="";
        const items=catalog.filter(i=>i.category==="软件");
        if(!items.length){ list.innerHTML=`<div style="opacity:.6;padding:20px">暂无软件</div>`; return; }
        items.forEach(it=>list.insertAdjacentHTML("beforeend",card(it)));
      };
      const renderImage=()=>{
        sub.textContent="系统镜像会真实下载到你的专属空间，可在「虚拟机」里启动";
        list.innerHTML="";
        catalog.filter(i=>i.category==="系统镜像").forEach(it=>list.insertAdjacentHTML("beforeend",card(it)));
      };
      const renderGames=()=>{
        sub.textContent="官方小游戏 + 网页版 Minecraft，下载后从启动器或「小游戏」文件夹打开";
        list.innerHTML="";
        const items=catalog.filter(i=>i.category==="游戏");
        if(!items.length){ list.innerHTML=`<div style="opacity:.6;padding:20px">暂无游戏</div>`; return; }
        items.forEach(it=>list.insertAdjacentHTML("beforeend",card(it)));
      };
      const renderMine=()=>{
        sub.textContent="你已安装的内容";
        list.innerHTML="";
        const ids=Object.keys(mine);
        if(!ids.length){ list.innerHTML=`<div style="opacity:.6;padding:20px">还没有安装任何内容。去「软件」或「系统镜像」点安装吧。</div>`; return; }
        ids.forEach(id=>{
          const it=catalog.find(x=>x.id===id)||{name:id,icon:"📦",desc:"",category:"",size:""};
          list.insertAdjacentHTML("beforeend",card(it));
        });
      };
      const refresh=async()=>{
        try{
          const r=await fetch("/api/store/catalog",{headers:authHeaders()});
          if(r.ok){ const j=await r.json(); catalog=j.items||[]; mine={}; catalog.forEach(i=>{ if(i.installed) mine[i.id]=1; }); }
        }catch(e){}
        const active=side.querySelector(".sw-nav.active");
        if(active && active.dataset.tab==="image") renderImage();
        else if(active && active.dataset.tab==="games") renderGames();
        else if(active && active.dataset.tab==="mine") renderMine();
        else renderSoftware();
      };
      side.addEventListener("click",e=>{
        const n=e.target.closest(".sw-nav"); if(!n) return;
        side.querySelectorAll(".sw-nav").forEach(x=>x.classList.remove("active"));
        n.classList.add("active");
        if(n.dataset.tab==="image") renderImage();
        else if(n.dataset.tab==="games") renderGames();
        else if(n.dataset.tab==="mine") renderMine();
        else renderSoftware();
      });
      list.addEventListener("click",async e=>{
        const b=e.target.closest("button[data-act]"); if(!b) return;
        const act=b.dataset.act, id=b.dataset.id;
        if(act==="install"){
          b.disabled=true; b.textContent="下载中…";
          try{
            const r=await fetch("/api/store/install",{method:"POST",
              headers:Object.assign({"Content-Type":"application/json"},authHeaders()),
              body:JSON.stringify({id})});
            const j=await r.json().catch(()=>({}));
            if(r.ok && j.ok){
              b.textContent="已安装";
              if(j.type==="software" && j.appKey){
                // 软件类：通知桌面把对应应用并入启动器（单用户隔离）
                window.dispatchEvent(new CustomEvent("store:appinstalled",{detail:{appKey:j.appKey,id}}));
              }else{
                window.dispatchEvent(new CustomEvent("store:changed",{detail:{id}}));
              }
              await refresh();
            }else{
              b.textContent="重试";
              alert("安装失败："+(j.err||r.status));
            }
          }catch(err){ b.textContent="重试"; alert("安装失败："+err); }
        }else if(act==="uninstall"){
          b.disabled=true; b.textContent="卸载中…";
          try{
            const it=catalog.find(x=>x.id===id)||{};
            await fetch("/api/store/uninstall",{method:"POST",
              headers:Object.assign({"Content-Type":"application/json"},authHeaders()),
              body:JSON.stringify({id})});
            delete mine[id];
            if(it.type==="software" && it.appKey)
              window.dispatchEvent(new CustomEvent("store:appuninstalled",{detail:{appKey:it.appKey,id}}));
            else
              window.dispatchEvent(new CustomEvent("store:changed",{detail:{id,removed:true}}));
            await refresh();
          }catch(err){ alert("卸载失败："+err); }
        }else if(act==="open"){
          const it=catalog.find(x=>x.id===id)||{};
          if(it.type==="software" && it.appKey){
            try{ window.openApp(it.appKey); }catch(e){}
          }else{
            try{ if(window.openApp) window.openApp("虚拟机"); }catch(e){}
            window.dispatchEvent(new CustomEvent("store:open",{detail:{id}}));
          }
        }
      });
      refresh();
    }
  },

  "日志": { icon:"日志", dash:false, render: () => `
    <div class="logs">
      <div class="logs-bar">
        <button class="active" data-log="dmesg">内核 (dmesg)</button>
        <button data-log="journal">系统日志</button>
        <button data-log="auth">认证</button>
        <button data-log="audit">安全审计 🔒</button>
      </div>
      <div class="logs-body" id="logsBody">加载中…</div>
    </div>`,
    onMount:async(body)=>{
      const out=body.querySelector("#logsBody");
      const load=async(type)=>{
        out.textContent="加载中…";
        if(type==="audit"){
          // 审计接口返回数组，每行自带 IP / 事件 / UA，
          // 就是用来回答"我的口令被哪个 IP 用了"的
          const rows=await (window.API&&window.API.audit?window.API.audit(80):Promise.resolve(null));
          if(!rows||!rows.length){out.textContent="暂无审计记录。";return;}
          out.textContent=rows.join("\n");
          return;
        }
        const txt=await (window.API&&window.API.logs?window.API.logs(type):Promise.resolve(null));
        out.textContent=txt||"[演示] 暂无日志数据。";
      };
      body.querySelectorAll("[data-log]").forEach(b=>b.onclick=()=>{body.querySelectorAll("[data-log]").forEach(x=>x.classList.remove("active"));b.classList.add("active");load(b.dataset.log);});
      load("dmesg");
    }
  },

  "截图": { icon:"截图", dash:false, render: () => `
    <div style="height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px">
      <div style="font-size:18px">截图工具</div>
      <div style="display:flex;gap:12px">
        <button class="sw-card" style="padding:14px 18px;border:none;cursor:pointer">🖥️ 全屏</button>
        <button class="sw-card" style="padding:14px 18px;border:none;cursor:pointer">🪟 窗口</button>
        <button class="sw-card" style="padding:14px 18px;border:none;cursor:pointer">✂️ 区域</button>
      </div>
      <div class="imgv-canvas" style="width:280px;height:170px">📷</div>
      <div style="color:#999;font-size:12px">预览（模拟）</div>
    </div>`
  },

  "图像查看器": { icon:"图像查看器", dash:false, render: () => `
    <div class="imgv">
      <div class="imgv-canvas">🖼️</div>
      <div class="imgv-meta">sample.png · 1920 × 1080 · 2.4 MB（模拟）</div>
    </div>`
  },

  "磁盘": { icon:"磁盘", dash:false, render: () => `
    <div style="padding:20px"><div style="font-size:22px;font-weight:300;margin-bottom:14px">磁盘</div><div id="diskList"></div></div>`,
    onMount:async(body)=>{
      const d=await (window.API&&window.API.disks?window.API.disks():Promise.resolve(null));
      const rows=d||[{fs:"overlay",size:"256G",used:"110M",avail:"254G",mount:"/"},{fs:"tmpfs",size:"61G",used:"2.1M",avail:"61G",mount:"/dev/shm"}];
      body.querySelector("#diskList").innerHTML=`<table class="mon-table"><tr><th>文件系统</th><th>大小</th><th>已用</th><th>可用</th><th>挂载点</th></tr>`+
        rows.map(r=>`<tr><td>${r.fs}</td><td>${r.size}</td><td>${r.used}</td><td>${r.avail}</td><td>${r.mount}</td></tr>`).join("")+`</table>`;
    }
  },

  "帮助": { icon:"帮助", dash:false, render: () => `
    <div class="app-pad" style="line-height:1.7">
      <h2>Ubuntu 桌面帮助</h2><br>
      • 左上角<b>活动</b>或按 <b>Super</b> 键进入概览<br>
      • 左侧 <b>Ubuntu Dock</b> 固定常用应用<br>
      • <b>终端</b>已接入真实 bash，可直接执行 Linux 命令<br>
      • <b>文件</b>已接入真实文件系统，双击文本可直接查看内容<br>
      • 顶栏右侧 ⏻ 打开电源 / 用户菜单<br>
      • 按住 <b>Alt+Tab</b> 切换应用<br>
      • 快速设置里可开关<b>深色模式</b>（真生效）<br>
      • 桌面右键可新建文件夹、改背景、打开终端<br><br>
      这是一个运行在浏览器中的 Ubuntu 24.04 桌面模拟器，部分界面为还原展示，终端、文件、系统监视器等已连接到真实环境。
    </div>`
  },

  "磁盘使用分析器": { icon:"磁盘使用分析器", dash:false, render: () => `
    <div style="height:100%;display:flex;flex-direction:column">
      <div class="files-pathbar"><b>磁盘使用情况</b> · 主目录</div>
      <div style="flex:1;overflow:auto;padding:14px" id="baobabList"></div>
    </div>`,
    onMount: async (body) => {
      const el=body.querySelector("#baobabList");
      el.innerHTML=`<div style="color:#888;font-size:12px">正在分析真实 du 数据…</div>`;
      const r=await (window.API&&window.API.exec?window.API.exec("du -sh /root/* 2>/dev/null | sort -rh | head -12"):Promise.resolve({err:"无后端"}));
      const txt=r.out||r.err||"";
      const lines=txt.split("\n").filter(x=>x.trim());
      if(!lines.length){ el.innerHTML=`<div style="color:#888">无数据</div>`; return; }
      const max=parseFloat(lines[0])||1;
      const map={K:1024,M:1048576,G:1073741824};
      const toBytes=(s)=>{ const m=s.match(/^([\d.]+)([KMG])$/); return m?parseFloat(m[1])*map[m[2]]:parseFloat(s)||0; };
      el.innerHTML=lines.map(l=>{
        const sp=l.indexOf("\t"); const size=l.slice(0,sp).trim(), path=l.slice(sp+1).trim();
        const pct=Math.min(100,toBytes(size)/max*100);
        return `<div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px"><span>${path.split("/").pop()}</span><span style="color:#777">${size}</span></div><div class="mon-bar"><i style="width:${pct}%"></i></div></div>`;
      }).join("")+`<div style="color:#999;font-size:11px;margin-top:8px">数据来自真实 du 命令（主目录 /root）</div>`;
    }
  },

  "日历": { icon:"日历", dash:false, render: (win) => {
    const d=new Date();
    const y=d.getFullYear(), m=d.getMonth();
    const first=new Date(y,m,1).getDay();
    const days=new Date(y,m+1,0).getDate();
    const wd=["日","一","二","三","四","五","六"];
    const events={ [d.getDate()]:["📅 今天"], [(d.getDate()%28)+1]:"🎂 生日提醒", [(d.getDate()%28)+7]:"💼 团队会议" };
    let html=`<div class="cal-app"><div class="cal-head">${y}年 ${m+1}月</div><div class="cal-grid">`;
    html+=wd.map(x=>`<div class="cal-ch">${x}</div>`).join("");
    for(let i=0;i<first;i++)html+=`<div class="cal-cd other"></div>`;
    for(let i=1;i<=days;i++){
      const ev=events[i]?"<span class='cal-dot'></span>":"";
      html+=`<div class="cal-cd${i===d.getDate()?" today":""}">${i}${ev}</div>`;
    }
    html+=`</div><div class="cal-side"><div class="cal-side-h">今日</div>`;
    html+=events[d.getDate()]?`<div class="cal-ev">${events[d.getDate()][0]} ${events[d.getDate()].slice(2)}</div>`:`<div style="color:#999;font-size:12px">无安排</div>`;
    html+=`</div></div>`;
    return html;
  } },

  "天气": { icon:"天气", dash:false, render: () => `
    <div class="weather-app">
      <div class="wa-top"><div class="wa-city">上海</div><div class="wa-now">🌤️<span>22°</span></div><div class="wa-desc">局部多云 · 最高 25° 最低 18°</div></div>
      <div class="wa-hourly" id="waHourly"></div>
      <div class="wa-daily" id="waDaily"></div>
    </div>`,
    onMount:(body)=>{
      const ico=["☀️","🌤️","⛅","🌥️","🌧️","⛈️","🌨️"];
      const hr=body.querySelector("#waHourly");
      for(let i=0;i<8;i++){ const hh=(new Date().getHours()+i*3)%24; hr.innerHTML+=`<div class="wa-h"><div>${hh}:00</div><div class="wa-hi">${ico[i%7]}</div><div>${18+i%7}°</div></div>`; }
      const wk=["今天","周一","周二","周三","周四","周五","周六"];
      const dl=body.querySelector("#waDaily");
      for(let i=0;i<7;i++){ dl.innerHTML+=`<div class="wa-d"><div>${wk[i]}</div><div class="wa-di">${ico[(i+2)%7]}</div><div class="wa-dt">${20+i%5}° / ${14+i%4}°</div></div>`; }
    }
  }

};

// 本机模拟命令（后端不可用时兜底）
function simExec(cmd){
  const c=cmd.trim();
  if(!c)return{out:""};
  const [bin,...args]=c.split(/\s+/);
  const map={
    help:"可用模拟命令: help, ls, pwd, whoami, uname, echo, date, cat, neofetch, clear\n更多命令请连接真实 bash 后端。",
    pwd:"/home/user",
    whoami:"user",
    uname:"Linux ubuntu-vm 6.6.117 #1 SMP x86_64 GNU/Linux",
    ls:"桌面  下载  文档  图片  音乐  视频  公共  模板  .bashrc  .profile  .config",
    date:new Date().toString(),
    neofetch:`       /-\\        user@ubuntu\n      |o o|       OS: Ubuntu 24.04.3 LTS\n      |  /         Kernel: 6.6.117\n     \\  \\        Shell: bash\n                CPU: 32 x86_64\n                Memory: 123GiB`
  };
  if(map[bin])return{out:map[bin]};
  if(bin==="echo")return{out:args.join(" ")};
  if(bin==="cat"&&args[0]==="/etc/os-release")return{out:"PRETTY_NAME=\"Ubuntu 24.04.3 LTS\"\nVERSION=\"24.04.3 LTS (Noble Numbat)\""};
  return{err:`bash: ${bin}: 命令未找到（请连接真实后端执行）`};
}
