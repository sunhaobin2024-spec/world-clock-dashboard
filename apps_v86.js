// ============================================================
// 虚拟机 —— 作为 GNOME 窗口内的应用（v86 真实 x86 模拟）
// 设计要点（针对低内存老手机）：
//  1. 不整页跳转到 /win/，避免同时驻留两个页面的内存
//  2. libv86.js(360KB) + v86.wasm(2MB) 按需加载，不启动不占内存
//  3. 窗口关闭时 emu.destroy() 真正释放 WASM 线性内存
//  4. 同一时刻只允许一个 v86 实例，重开即复用
// ============================================================

// 启动方式有两种：
//  bzimage: 直接把内核+initrd 注入 option_rom，绕过 isolinux/GRUB 菜单
//  image:   挂载整张镜像，走原本的 BIOS 引导流程
//
// 为什么 SliTaz 必须用 bzimage：
//  它的 isolinux.cfg 不是直接引导内核，而是先进 c32box 菜单
//  （COM32 c32box.c32 + append ifmem 384M … 内存不到 384M 就不启动；
//   PROMPT 0 + TIMEOUT 80 会自动进菜单后干等）。手机上没法按键，表现就是卡黑屏。
//  TinyCore 同理走 bzimage 直导最稳。
//
// 历史坑（2026-10-04 已修）：DSL 曾被"优化"成只下 292KB 迷你 initrd（号称 -97%），
//  但那是 Miniroot，必须再挂载整张 KNOPPIX 才能进系统；bzimage 模式不挂光驱，
//  于是永远黑屏（协作者靠解压 initrd 取证锁定）。DSL 已整体摘掉，默认改 TinyCore
//  （initrd 自带完整根文件系统，控制组：TinyCore 9.7MB / SliTaz 51.8MB 都是自包含根 fs）。
const V86_SYSTEMS = {
  tinycore: { name:"TinyCore Linux 11.0", mode:"bzimage", mem:128*1024*1024, rec:true,
              kernel:"/win/boot/tinycore-kernel", initrd:"/win/boot/tinycore-initrd",
              cmdline:"loglevel=3 cde", size:"13.7 MB", isoSize:"19 MB",
              desc:"极简 Linux（busybox）。initrd 自带完整根文件系统，bzimage 直导即可稳定启动，最适合练命令行。" },
  slitaz:   { name:"SliTaz 5.0-RC4", mode:"bzimage", mem:384*1024*1024,
              kernel:"/win/boot/slitaz-kernel", initrd:"/win/boot/slitaz-initrd",
              cmdline:"root=0x100 rw video=-32 autologin",
              size:"53 MB", isoSize:"56 MB",
              desc:"SliTaz 滚动版，图形桌面 + 完整包管理器。首次下载较慢，之后走缓存。" },
  kolibri:  { name:"KolibriOS", mode:"image", img:"kolibri.img", slot:"fda",
              mem:32*1024*1024, size:"1.4 MB",
              desc:"俄罗斯的 1.4MB 微型系统，图形界面，加载最快。" },
  freedos:  { name:"FreeDOS 1.3", mode:"image", img:"freedos722.img", slot:"fda",
              mem:32*1024*1024, size:"720 KB",
              desc:"开源 DOS。软盘镜像直接引导，命令行练手神器。" },
  msdos:    { name:"MS-DOS 7.10 中文版", mode:"image", img:"msdos.img", slot:"fda",
              mem:32*1024*1024, size:"1.4 MB",
              desc:"带中文支持的 MS-DOS 7.1（Win98 底层内核）。启动菜单倒计时 11 秒，不按键会自动进入。" },
  win101:   { name:"Windows 1.01 (1985)", mode:"image", img:"windows101.img", slot:"fda",
              mem:64*1024*1024, size:"1.4 MB",
              desc:"微软第一版 Windows！40 年前的真品，启动后是 MS-DOS Executive 文件管理器，双击 WIN.COM 即可进入 Windows 1.01 图形界面。" },
  solaros:  { name:"Solar_OS 0.104", mode:"image", img:"os8.dsk", slot:"fda",
              mem:64*1024*1024, size:"1.4 MB",
              desc:"罗马尼亚作者手搓的图形 hobby 系统（2004-2009）。启动菜单出现后按回车，稍等进入蓝色图形桌面。" },
  openbsd:  { name:"OpenBSD 引导盘", mode:"image", img:"openbsd.img", slot:"fda",
              mem:64*1024*1024, size:"1.4 MB",
              desc:"OpenBSD boot 引导器，启动到 boot> 提示符。完整安装较慢，适合看正统 BSD 引导流程。" },
};

/* ---- 用户导入的自定义镜像（"导入镜像"功能追加到这里，动态生效）---- */
const V86_CUSTOM={};
/* ---- 应用商店里当前用户装好的系统（单用户隔离，只显示自己的）---- */
const V86_STORE={};

// libv86 全局只加载一次
let v86LibPromise=null;
function loadV86Lib(){
  if(window.V86) return Promise.resolve(window.V86);
  if(v86LibPromise) return v86LibPromise;
  v86LibPromise=new Promise((res,rej)=>{
    const s=document.createElement("script");
    s.src="/win/vendor/libv86.js";
    s.onload=()=>window.V86?res(window.V86):rej(new Error("libv86 加载后未导出 V86"));
    s.onerror=()=>rej(new Error("libv86.js 下载失败"));
    document.head.appendChild(s);
  });
  return v86LibPromise;
}

/* ---- 标准 PC 键盘 Set 1 scancode ----
   不用 emulate_char/send_text：实测这个 libv86 构建内置的 keyCode→scancode 表有错位
   （keyCode 97 'a' 被映成 79），send_text 会打出错误字符。直接送 scancode 行为确定。 */
const V86_SC={'a':0x1e,'b':0x30,'c':0x2e,'d':0x20,'e':0x12,'f':0x21,'g':0x22,'h':0x23,'i':0x17,
 'j':0x24,'k':0x25,'l':0x26,'m':0x32,'n':0x31,'o':0x18,'p':0x19,'q':0x10,'r':0x13,
 's':0x1f,'t':0x14,'u':0x16,'v':0x2f,'w':0x11,'x':0x2d,'y':0x15,'z':0x2c,
 '0':0x0b,'1':0x02,'2':0x03,'3':0x04,'4':0x05,'5':0x06,'6':0x07,'7':0x08,'8':0x09,'9':0x0a,
 ' ':0x39,'-':0x0c,'=':0x0d,'[':0x1a,']':0x1b,';':0x27,"'":0x28,'`':0x29,'\\':0x2b,
 ',':0x33,'.':0x34,'/':0x35};
const V86_SHIFTED={'!':'1','@':'2','#':'3','$':'4','%':'5','^':'6','&':'7','*':'8','(':'9',')':'0',
 '_':'-','+':'=','{':'[','}':']',':':';','"':"'",'~':'`','|':'\\','<':',','>':'.','?':'/'};
const V86_SPECIAL={'ENTER':0x1c,'BACKSPACE':0x0e,'ESC':0x01,'TAB':0x0f,'SPACE':0x39,
 'UP':0x48,'DOWN':0x50,'LEFT':0x4b,'RIGHT':0x4d,'HOME':0x47,'END':0x4f,'PGUP':0x49,
 'PGDN':0x51,'DEL':0x53,'INS':0x52,'F1':0x3b,'F2':0x3c,'F3':0x3d,'F4':0x3e,'F5':0x3f,
 'F6':0x40,'F7':0x41,'F8':0x42,'F9':0x43,'F10':0x44,'F11':0x57,'F12':0x58};
const V86_K_SHIFT=0x2a, V86_K_CTRL=0x1d, V86_K_ALT=0x38;

/* v86 实例 + 下载测速状态从 v2.9 起改为“每个窗口各自独立”（支持多开），
   不再用模块级全局单例。这里只保留库加载器与纯计算函数。 */

/* ---- 下载测速：显示真实速度与剩余时间 ----
   实测 download-progress 在本地镜像下不一定触发，
   所以再走 PerformanceResourceTiming 兜底（资源必进 timeline）。
   net 由各窗口在 onMount 内传入，互不干扰（支持多开并行下载）。 */
function v86NetTick(net, loaded, total){
  const now=Date.now();
  if(total) net.total=total;
  if(!net.lastT){ net.lastT=now; net.lastLoaded=loaded; net.loaded=loaded; return 0; }
  net.loaded=loaded;
  const dt=(now-net.lastT)/1000;
  if(dt>=0.7 && loaded>net.lastLoaded){
    const inst=(loaded-net.lastLoaded)/dt;
    net.speed = net.speed ? net.speed*0.65+inst*0.35 : inst;  // 平滑，避免数字乱跳
    net.lastLoaded=loaded; net.lastT=now;
  }
  return net.speed;
}
function fmtDur(s){
  if(!isFinite(s)||s<=0) return "";
  s=Math.round(s);
  return s<60 ? s+" 秒" : Math.floor(s/60)+" 分 "+String(s%60).padStart(2,"0")+" 秒";
}

/* 释放单个 v86 实例的 WASM 线性内存。inst 由各窗口持有（支持多开，不共享全局）。
   注意：只 destroy 实例本身，不要回写模块级变量。 */
function v86Destroy(inst){
  if(!inst) return;
  try{ inst.emu && inst.emu.destroy(); }catch(e){}
  try{ inst.emu && inst.emu.stop(); }catch(e){}
}

/* ============ v86 引导界面（BIOS 皮，win32.run 风格 4:3 舞台）============
   窗口内嵌 /boot.html：Award 风格 BIOS 自检动画 -> 引导设备菜单 -> 选系统
   后顶层跳转 /?app=虚拟机&sys=xxx，由 script.js 深链自动开窗并启动。
   竖屏时 boot.html 自己显示"请横屏"提示，不影响桌面。 */
APP_REGISTRY["虚拟机引导"] = {
  icon:"虚拟机", dash:true, width:820, height:648,
  render: () => `<div class="win-v86boot" style="width:100%;height:100%;background:#000">
    <iframe src="/boot.html" style="width:100%;height:100%;border:0;display:block;background:#000"
      title="v86 引导界面"></iframe>
  </div>`,
  onMount(body, win){}
};

APP_REGISTRY["虚拟机"] = {
  icon:"虚拟机", dash:true, multi:true, width:900, height:620,
  render: () => `
    <div class="win-v86">
      <div class="v86-bar">
        <span class="st"><span class="dot" id="v86Dot"></span><span id="v86St">未启动</span></span>
        <select id="v86Sys"></select>
        <button class="pri" id="v86Boot">启动</button>
        <button id="v86Imp" title="导入镜像">📥</button>
        <button id="v86Kb">⌨️</button>
        <button id="v86Cad">^Alt^Del</button>
        <button id="v86Rst">↺</button>
        <button id="v86Full">⛶</button>
      </div>
      <div class="v86-imp" id="v86ImpPanel">
        <div class="imp-row">
          <label>镜像文件</label>
          <input type="file" id="v86ImpFile" accept=".img,.iso,.qcow2,.vhd,.vhdx,.esd,.wim">
        </div>
        <div class="imp-row">
          <label>显示名称</label>
          <input type="text" id="v86ImpName" placeholder="如 Windows XP SP3">
        </div>
        <div class="imp-row">
          <label>内存档位</label>
          <select id="v86ImpMem">
            <option value="128">128 MB（DOS/Win95/98）</option>
            <option value="256">256 MB（Win2000）</option>
            <option value="512" selected>512 MB（WinXP 推荐）</option>
            <option value="1024">1 GB（WinXP 高配/Win7 尝试）</option>
            <option value="2048">2 GB（Win7 极限，会很慢）</option>
            <option value="3072">3 GB（桌面浏览器，很慢）</option>
            <option value="4096">4 GB（引擎硬上限，极易崩溃）</option>
          </select>
        </div>
        <div class="imp-row">
          <label>空白硬盘</label>
          <select id="v86ImpHdd">
            <option value="0" selected>不挂载</option>
            <option value="67108864">64 MB</option>
            <option value="268435456">256 MB</option>
            <option value="536870912">512 MB</option>
            <option value="1073741824">1 GB</option>
            <option value="2147483648">2 GB（占用同量浏览器内存）</option>
          </select>
        </div>
        <div class="imp-row imp-btns">
          <button class="pri" id="v86ImpGo">开始上传</button>
          <button id="v86ImpCancel">取消</button>
        </div>
        <div class="imp-tip" id="v86ImpTip">支持 .img / .iso，单文件最大 4GB，自动选挂载槽位（.iso→光驱，小于 2MB→软盘，大镜像→硬盘）。Windows 8 及以上因 UEFI 启动无法在 v86 运行；XP/2000/98/DOS/ReactOS 可以。上传走分片，断网重传该片即可。<br>📦 内存：v86 是 32 位 WASM 引擎，4 GB 是硬上限（16 GB 物理不可能）；手机选 ≤2 GB。<br>💾 空白硬盘：给系统挂一块空盘用于装软件/存文件，导入 ISO 时自动挂为主盘、导入硬盘镜像时挂为第二块盘。硬盘数据在浏览器内存里，关窗即清空。<br>🧠 CPU 核心数：v86 模拟的是单核 CPU，没有多核概念，此项不提供（引擎上限）。</div>
      </div>
      <button id="v86x64Btn" style="margin:8px 0 0 12px;padding:7px 14px;border:0;border-radius:8px;background:linear-gradient(180deg,#8b5cf6,#6d3fd6);color:#fff;font-size:12.5px;cursor:pointer">🖥️ 64 位虚拟机（服务端 QEMU · 8 核真多核）</button>
      <div class="v86-imp" id="v86x64Panel">
        <div class="imp-row">
          <label>内存</label>
          <select id="x64Mem">
            <option value="1">1 GB</option>
            <option value="2" selected>2 GB</option>
            <option value="3">3 GB</option>
            <option value="4">4 GB（服务器当前配额上限）</option>
          </select>
        </div>
        <div class="imp-row">
          <label>CPU 核心</label>
          <select id="x64Cores">
            <option value="1">1 核</option>
            <option value="2" selected>2 核</option>
            <option value="4">4 核</option>
            <option value="8">8 核</option>
          </select>
        </div>
        <div class="imp-row">
          <label>磁盘大小</label>
          <select id="x64Disk">
            <option value="0" selected>不挂载（只用光驱 Live）</option>
            <option value="10">10 GB</option>
            <option value="20">20 GB</option>
            <option value="40">40 GB</option>
          </select>
        </div>
        <div class="imp-row">
          <label>系统 ISO</label>
          <select id="x64Iso"><option>加载中…</option></select>
        </div>
        <div class="imp-row imp-btns">
          <button class="pri" id="x64Go">🚀 启动 64 位虚拟机</button>
          <button id="x64Stop">关机</button>
          <button id="x64Cancel">收起</button>
        </div>
        <div class="imp-tip" id="x64Tip">64 位真虚拟机跑在服务器上（32 核 / 123GB 真实硬件），浏览器通过 noVNC 直接操作：64 位系统、多核 CPU、真实 qcow2 磁盘全部生效。服务器做 CPU 软件模拟，速度比真机慢，启动耐心等 1~3 分钟。内存当前上限 4GB——服务器容器配额为 8GB（含系统占用），8/16GB 需扩配额后放开。ISO 先在应用商店下载到服务器，Kali Live 已就绪。</div>
      </div>
      <div id="x64Stage" style="position:absolute;inset:0;display:none;z-index:60;background:#0b0d12">
        <iframe id="x64Frame" src="about:blank" allow="fullscreen; clipboard-read; clipboard-write" style="position:absolute;inset:0;width:100%;height:100%;border:0"></iframe>
        <button id="x64Off" style="position:absolute;top:8px;right:10px;z-index:2;padding:6px 14px;border:0;border-radius:8px;background:rgba(224,91,91,.92);color:#fff;font-size:12.5px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.4)">⏻ 关机并退出</button>
      </div>
      <div class="v86-stage" id="v86Stage">
        <div class="v86-screen" id="v86Screen"></div>
        <div class="v86-boot" id="v86Boot2">
          <div class="wrap">
            <div class="bios" id="v86Bios"></div>
            <div class="steps">
              <div class="step" data-s="wasm"><i></i><span>加载模拟器内核</span></div>
              <div class="step" data-s="img"><i></i><span>读取系统镜像</span></div>
              <div class="step" data-s="bios"><i></i><span>BIOS 自检</span></div>
              <div class="step" data-s="boot"><i></i><span>引导内核</span></div>
              <div class="step" data-s="desk"><i></i><span>进入桌面</span></div>
            </div>
            <div class="bar2"><i id="v86Bar"></i></div>
            <div class="tip" id="v86Tip">点「启动」开机</div>
          </div>
        </div>
      </div>
      <div class="v86-kb" id="v86KbPanel">
        <textarea id="v86In" placeholder="在这里打字，直接送进虚拟机" autocomplete="off"
                  autocapitalize="off" spellcheck="false"></textarea>
        <div class="keys" id="v86Keys"></div>
      </div>
    </div>`,

  onMount(body, win){
    const $ = s => body.querySelector(s);
    let v86Inst=null;  // 本窗口独立的 v86 实例（从 v2.9 起支持多开，不再共享全局）
    const v86Net={loaded:0,total:0,lastT:0,lastLoaded:0,speed:0};  // 本窗口下载测速状态，多开互不干扰
    const stage=$("#v86Stage"), screen=$("#v86Screen"), kb=$("#v86KbPanel"),
          biosBox=$("#v86Bios"), bar=$("#v86Bar"), tip=$("#v86Tip"),
          stText=$("#v86St"), dot=$("#v86Dot"), sel=$("#v86Sys");
    const stepEls={};
    body.querySelectorAll(".v86-boot .step").forEach(el=>stepEls[el.dataset.s]=el);

    Object.entries(V86_SYSTEMS).forEach(([k,s])=>{
      const mb=parseFloat(s.size);
      const o=document.createElement("option");
      // 标出实际下载量与推荐。bzImage 模式下只下内核+initrd，不再是整张 ISO。
      // 默认/推荐走 TinyCore（initrd 自包含、启动最稳）；SliTaz 体积大、慢网慎选。
      const star = s.rec ? " ★推荐" : (mb>40 ? " ⚠慢网慎选" : "");
      o.value=k;
      o.textContent=`${s.name} · ${s.size}${star}`
        +(s.isoSize&&s.isoSize!==s.size?`（原镜像 ${s.isoSize}）`:"");
      sel.appendChild(o);
    });
    /* ---- 恢复自定义导入的镜像（localStorage 持久化）---- */
    try{
      const custom=JSON.parse(localStorage.getItem("v86.custom")||"[]");
      custom.forEach(c=>{
        V86_CUSTOM[c.key]=c.def;
        const o=document.createElement("option");
        o.value=c.key;
        o.textContent=`${c.def.name} · ${c.def.size} · 导入`;
        sel.appendChild(o);
      });
    }catch(e){}
    /* ---- 兜底：服务器 win/images/ 里已有但本机没记录的镜像，自动注册 ----
       （换设备/清了 localStorage 时，boot.html 菜单和这里仍能对上号；
         key 规则与上传时一致：custom_ + 文件名，非法字符转 _）---- */
    (async()=>{
      try{
        const r=await fetch("/api/images",{headers:authHeaders()});
        const j=await r.json();
        const PRESET=["kolibri.img","freedos722.img","msdos.img","windows101.img","os8.dsk","openbsd.img",
                      "TinyCore-11.0.iso","slitaz-rolling-2024.iso","dsl-4.11.rc2.iso"];
        (j.items||[]).forEach(im=>{
          if(PRESET.includes(im.name)) return;
          const key="custom_"+im.name.replace(/[^a-zA-Z0-9_.-]/g,"_");
          if(V86_CUSTOM[key]) return;
          const ext=(im.name.match(/\.([^.]+)$/)||[,""])[1].toLowerCase();
          const slot = ext==="iso" ? "cdrom" : (im.size<2*1024*1024 ? "fda" : "hda");
          const def={ name:im.name.replace(/\.[^.]+$/,""), mode:"image", img:im.name,
                      slot, mem:512*1024*1024,
                      size:(im.size/1048576).toFixed(0)+" MB", custom:true,
                      desc:"服务器已有镜像（默认 512MB 内存，可用导入面板改）" };
          V86_CUSTOM[key]=def;
          const o=document.createElement("option");
          o.value=key;
          o.textContent=`${def.name} · ${def.size} · 导入`;
          sel.appendChild(o);
        });
      }catch(e){}
    })();
    /* ---- 应用商店：把当前用户装好的系统加进下拉 ----
       单用户隔离：只有自己装过的才出现在这里，且镜像从这台机器后端真实下发的
       /api/store/file 读取（带鉴权），绝不会串到别人的目录。 ---- */
    (async()=>{
      try{
        const r=await fetch("/api/store/mine",{headers:authHeaders()});
        const j=await r.json().catch(()=>({items:[]}));
        (j.items||[]).forEach(it=>{
          const key="store_"+it.id;
          const sys=it.sys||{mode:"image",slot:"fda",mem:64};
          V86_STORE[key]={id:it.id, sys};
          const o=document.createElement("option");
          o.value=key;
          o.textContent=`${it.name} · 商店安装`;
          sel.appendChild(o);
        });
      }catch(e){}
    })();
    // 应用商店里点了「打开」→ 自动选中对应系统并启动（下拉可能还没建好，重试几次）
    const onStoreOpen=(e)=>{
      const id=e&&e.detail&&e.detail.id; if(!id) return;
      let tries=0;
      const tick=()=>{
        const key="store_"+id;
        if(sel.querySelector(`option[value="${key}"]`)){
          sel.value=key; start();
        }else if(tries++<10){
          setTimeout(tick,150);
        }
      };
      tick();
    };
    window.addEventListener("store:open", onStoreOpen);
    const remembered = (()=>{ try{ return localStorage.getItem("win86.sys")||"tinycore"; }catch(e){ return "tinycore"; } })();
    sel.value = V86_SYSTEMS[remembered] ? remembered : "tinycore";
    // 记住所选系统体积超过 45MB 时自动退回最稳的 TinyCore，慢网下别再让人干等
    // （历史 bug：这里曾强制退回 dsl，而 dsl 本身已坏，等于"选 SliTaz 永远跳回黑屏"）
    try{
      if(parseFloat(V86_SYSTEMS[sel.value].size)>45) sel.value="tinycore";
    }catch(e){}

    // ---- 骨架屏驱动 ----
    const BIOS_LINES=[
      "<u>TinyCore v11.0 · i386 · Booting…</u>",
      "CPU: Intel 80486　RAM: 检测中",
      "Probing hardware: PIIX3 IDE, VGA, PS/2 keyboard…",
      "Loading boot sector…",
      "Decompressing kernel…",
      "Mounting root filesystem (tce, squashfs)…",
      "Starting system services…",
      "<u>Welcome to TinyCore Linux</u>",
    ];
    let biosTimer=null, tickTimer=null, deskTimer=null, guardTimer=null, emuReadyAt=0;
    const setStep=(k,c)=>{ const el=stepEls[k]; if(el){ el.classList.remove("on","ok"); if(c) el.classList.add(c);} };
    const stepOk=k=>setStep(k,"ok"), stepOn=k=>setStep(k,"on");
    const biosPush=html=>{ const d=document.createElement("div"); d.innerHTML=html; biosBox.appendChild(d);
      while(biosBox.children.length>7) biosBox.removeChild(biosBox.firstChild); };
    const setBar=p=>{ bar.style.width=Math.max(2,Math.min(100,p))+"%"; };
    const setSt=(t,run)=>{ stText.textContent=t; dot.className="dot"+(run?" run":""); };
    const clearTimers=()=>{ [biosTimer,tickTimer,deskTimer,guardTimer].forEach(t=>t&&clearTimeout(t)||t&&clearInterval(t));
      biosTimer=tickTimer=deskTimer=guardTimer=null; };

    function bootShow(){
      clearTimers();
      $(".v86-boot").classList.remove("done");
      biosBox.innerHTML="";
      Object.values(stepEls).forEach(el=>el.classList.remove("on","ok"));
      setBar(2); tip.textContent="正在取 WebAssembly 内核…";
      stepOn("wasm");
      let i=0;
      biosTimer=setInterval(()=>{ if(i>=BIOS_LINES.length){ clearInterval(biosTimer); biosTimer=null; return; }
        biosPush(BIOS_LINES[i++]); }, 340);
      // 自走进度：v86 几乎不给进度信号，不做就会长时间停住
      const t0=Date.now();
      tickTimer=setInterval(()=>{
        const el=(Date.now()-t0)/1000;
        const p = el<25 ? 2+el/25*43 : 45+Math.min(50,(el-25)/75*50);
        if(p>parseFloat(bar.style.width||0)) setBar(p);
      },400);
    }
    /* 兜底测速：download-progress 不触发时，用 ResourceTiming 算真实吞吐。
       没有这层，用户只能看着进度条猜还要多久。 */
    let netTimer=null;
    // 跟踪一个或多个资源的下载进度。
    // bzImage 模式下内核和 initrd 是并行下载的，要按两者合计算进度，
    // 否则进度条会在第一个文件下完时就显示 100% 然后卡住。
    function netWatch(urlOrList){
      clearInterval(netTimer);
      const urls = Array.isArray(urlOrList) ? urlOrList : [urlOrList];
      v86Net.loaded=0; v86Net.total=0; v86Net.speed=0;
      v86Net.lastT=0; v86Net.lastLoaded=0;
      const seen = {};              // url -> {total, got}
      netTimer=setInterval(()=>{
        try{
          const res=performance.getEntriesByType("resource");
          let total=0, got=0, any=false;
          for(const u of urls){
            const es=res.filter(e=>e.name.indexOf(u)>=0);
            if(!es.length) continue;
            const e=es[es.length-1];
            const t=e.encodedBodySize||e.decodedBodySize||0;
            const g=Math.min(e.transferSize||0, t);   // transferSize 含 gzip 头，可能大于 total，钳一下防超 100%
            if(t>0){ seen[u]={total:t,got:g}; any=true; }
          }
          if(!any) return;
          for(const k in seen){ total+=seen[k].total; got+=seen[k].got; }
          if(total>0 && got>0){
            const sp=v86NetTick(v86Net, got,total);
            const mb=v86Net.total? (v86Net.total/1048576).toFixed(1) : (total/1048576).toFixed(1);
            setBar(8 + got/total*34);       // 下载占进度条 8%~42%
            if(sp>0){
              const kb=sp/1024, remain=(total-got)/sp;
              tip.textContent=`已下载 ${(got/1048576).toFixed(1)} / ${mb} MB`
                +(kb<1024?`　${kb.toFixed(0)} KB/s`:`　${(kb/1024).toFixed(1)} MB/s`)
                +(remain>0?`　剩余约 ${fmtDur(remain)}`:"");
              setSt(`下载 ${(got/1048576).toFixed(0)}/${mb}MB`, false);
            }
          }
        }catch(_){}
      },1000);
    }
    function bootFail(code,msg){
      clearTimers();
      tip.textContent=`[${code}] 启动失败：${msg||"未知错误"}`; tip.style.color="#ff6b6b";
      setSt("启动失败",false);
    }
    function bootFinish(){
      clearTimers();
      clearInterval(netTimer); netTimer=null;   // 彻底停掉测速轮询，否则状态栏会一直显示"下载…"
      stepOk("desk"); setBar(100);
      BIOS_LINES.filter(l=>l.startsWith("<u>")).forEach(l=>biosPush(l));
      tip.textContent="已启动 · 双击打开 · 长按 0.5 秒出右键菜单";
      tip.style.color="";
      $(".v86-boot").classList.add("done");
      setSt("运行中",true);
      v86Inst && (v86Inst.booted=true);
      fit();
    }

    // ---- 缩放 ----
    let fitPending=false;
    function fit(){
      if(fitPending) return; fitPending=true;
      requestAnimationFrame(()=>{
        fitPending=false;
        const emu=v86Inst&&v86Inst.emu; if(!emu) return;
        const cvs=screen.querySelector("canvas"); if(!cvs) return;
        const bx=stage.getBoundingClientRect();
        const cw=cvs.width||640, ch=cvs.height||480;   // canvas 内部像素
        if(cw<2||ch<2) return;
        // 文本模式（DOS/BIOS）：v86 用 DOM 文本层渲染，canvas 是 0x0 闲置的。
        // 显示完全由文本层 + 容器决定（line-height 已在 CSS 修正），这里不要动 canvas。
        if(cw<=480 && ch<=512) return;
        // 图形模式：等比缩放到舞台内（v86 偶尔重置 style，700ms 重设一次即可）
        const vw=Math.max(80,bx.width-8), vh=Math.max(60,bx.height-8);
        const s=Math.min(vw/cw, vh/ch, 2);
        const W=Math.floor(cw*s), H=Math.floor(ch*s);
        if(cvs.style.width!==W+"px") cvs.style.width=W+"px";
        if(cvs.style.height!==H+"px") cvs.style.height=H+"px";
      });
    }
    const ro = window.ResizeObserver ? new ResizeObserver(fit) : null;
    if(ro) ro.observe(stage);
    const mo = window.MutationObserver ? new MutationObserver(fit) : null;
    if(mo) mo.observe(screen,{childList:true,subtree:true,attributes:true,attributeFilter:["width","height"]});
    const ivFit=setInterval(fit,700);
    win.addEventListener("closed",()=>{ clearInterval(ivFit); if(ro) ro.disconnect(); if(mo) mo.disconnect(); });
    // 窗口最大化/还原后重算缩放（ResizeObserver 之外再兜一层）
    win.addEventListener("winresize",fit);

    // ---- 键盘 ----
    const emuOf=()=>v86Inst&&v86Inst.emu;
    const send=c=>{ const e=emuOf(); if(!e||!e.bus) return false; e.bus.send("keyboard-code",c); return true; };
    const tap=(c,hold)=>{ if(!send(c)) return; setTimeout(()=>send(c|0x80), hold||25); };
    const combo=(m,c)=>{ if(!send(m)) return;
      setTimeout(()=>{ send(c); setTimeout(()=>send(c|0x80),30); },60);
      setTimeout(()=>send(m|0x80),150); };
    const multi=codes=>{ codes.forEach(send); setTimeout(()=>codes.forEach(c=>send(c|0x80)),45); };
    // 严格串行：上一个字符的"松开"发完再发下一个，否则 make/break 交错
    let kq=Promise.resolve();
    function typeStr(str){
      kq=kq.then(()=>new Promise(done=>{
        let i=0;
        (function step(){
          if(i>=str.length){ done(); return; }
          const ch=str[i++];
          let code=null, shift=false;
          if(V86_SC[ch]!==undefined) code=V86_SC[ch];
          else if(V86_SHIFTED[ch]){ code=V86_SC[V86_SHIFTED[ch]]; shift=true; }
          else if(ch>='A'&&ch<='Z'){ code=V86_SC[ch.toLowerCase()]; shift=true; }
          if(code===null){ setTimeout(step,0); return; }
          if(shift){ send(V86_K_SHIFT);
            setTimeout(()=>{ send(code); setTimeout(()=>{ send(code|0x80); setTimeout(step,0); },25); },25); }
          else { send(code); setTimeout(()=>{ send(code|0x80); setTimeout(step,0); },25); }
        })();
      }));
      return kq;
    }
    const SPECIALS=[["ENTER","⏎"],["BACKSPACE","⌫"],["ESC","⎋"],["TAB","⇥"],
      ["CTRL+C","^C"],["CTRL+D","^D"],["CTRL+L","^L"],["CTRL+A","^A"],["CTRL+Z","^Z"],
      ["UP","↑"],["DOWN","↓"],["LEFT","←"],["RIGHT","→"],
      ["HOME","Home"],["END","End"],["PGUP","PgUp"],["PGDN","PgDn"],["DEL","Del"],
      ["F1","F1"],["F2","F2"],["F5","F5"],["F10","F10"]];
    const keysBox=$("#v86Keys");
    SPECIALS.forEach(([k,lb])=>{
      const b=document.createElement("button"); b.textContent=lb;
      b.onclick=()=>{
        if(k==="CAD"){ multi([V86_K_CTRL,V86_K_ALT,0x53]); return; }
        if(k.startsWith("CTRL+")){ combo(V86_K_CTRL, V86_SPECIAL[k.slice(5)]); return; }
        if(V86_SPECIAL[k]!==undefined) tap(V86_SPECIAL[k]);
      };
      keysBox.appendChild(b);
    });
    const cadBtn=document.createElement("button");
    cadBtn.textContent="^Alt^Del"; cadBtn.onclick=()=>multi([V86_K_CTRL,V86_K_ALT,0x53]);
    keysBox.appendChild(cadBtn);
    let lastLen=0;
    $("#v86In").addEventListener("input",e=>{
      if(e.target.value.length<lastLen){ lastLen=e.target.value.length; return; }
      const s=e.target.value.slice(lastLen); lastLen=e.target.value.length;
      typeStr(s); e.target.value=""; lastLen=0;
    });

    /* ---- 触摸适配：手机双击/长按/右键 ----
       v86 原生只处理 touchstart(记坐标)/touchend(松开)，纯触摸点击靠浏览器合成 mouse 事件兜底，
       而双击被浏览器当成"双击缩放"吞掉 → 手机上双击必然失灵。这里自己造双击。 */
    const COARSE = !!(window.matchMedia && window.matchMedia("(pointer:coarse)").matches);
    const toAbs=(cx,cy)=>{
      const cvs=screen.querySelector("canvas"); if(!cvs) return null;
      const r=cvs.getBoundingClientRect();
      if(!r.width||!r.height) return null;
      if(cx<r.left-2||cx>r.right+2||cy<r.top-2||cy>r.bottom+2) return null;
      return [Math.max(0,Math.min(65535,Math.round((cx-r.left)/r.width*65535))),
              Math.max(0,Math.min(65535,Math.round((cy-r.top)/r.height*65535)))];
    };
    const mbus=()=>{ const e=emuOf(); return e&&e.bus; };
    const moveTo=a=>{ const b=mbus(); if(b&&a) b.send("mouse-absolute",[a[0]/65535,a[1]/65535,1,1]); };
    const clickOnce=()=>{ const b=mbus(); if(!b) return; b.send("mouse-click",[true,false,false]); b.send("mouse-click",[false,false,false]); };
    const clickRight=()=>{ const b=mbus(); if(!b) return; b.send("mouse-click",[false,false,true]); b.send("mouse-click",[false,false,false]); };
    const dblClick=()=>{ clickOnce(); setTimeout(clickOnce,90); };

    let pt=null, lastTap={t:0,x:0,y:0}, holdT=null, holdFired=false, movedFar=false;
    stage.addEventListener("pointerdown",e=>{
      if(!COARSE) return;
      const a=toAbs(e.clientX,e.clientY); if(!a) return;
      e.preventDefault(); pt={a,id:e.pointerId}; movedFar=false; holdFired=false;
      moveTo(a);
      clearTimeout(holdT);
      holdT=setTimeout(()=>{ if(movedFar||!pt) return; holdFired=true; clickRight();
        if(navigator.vibrate) try{ navigator.vibrate(18); }catch(_){} },500);
    },{passive:false});
    stage.addEventListener("pointermove",e=>{
      if(!COARSE||!pt||e.pointerId!==pt.id) return;
      const a=toAbs(e.clientX,e.clientY); if(!a) return;
      if(Math.abs(e.clientX-lastTap.x)>8||Math.abs(e.clientY-lastTap.y)>8) movedFar=true;
      moveTo(a);
    },{passive:false});
    stage.addEventListener("pointerup",e=>{
      if(!COARSE||!pt||e.pointerId!==pt.id) return;
      const a=toAbs(e.clientX,e.clientY);
      clearTimeout(holdT); pt=null;
      if(holdFired||movedFar||!a) return;
      const now=Date.now();
      const dbl=(now-lastTap.t)<420 && Math.abs(e.clientX-lastTap.x)<24 && Math.abs(e.clientY-lastTap.y)<24;
      if(dbl){ lastTap.t=0; moveTo(a); dblClick(); }
      else { lastTap={t:now,x:e.clientX,y:e.clientY}; moveTo(a); clickOnce(); }
      e.preventDefault();
    },{passive:false});
    stage.addEventListener("pointercancel",()=>{ clearTimeout(holdT); pt=null; },{passive:false});
    stage.addEventListener("dblclick",e=>e.preventDefault(),{passive:false});
    if(COARSE) ["touchstart","touchmove","touchend","touchcancel"]
      .forEach(t=>stage.addEventListener(t,e=>e.preventDefault(),{passive:false}));

    // ---- 启动 ----
    async function start(){
      const key=sel.value;
      let s=V86_SYSTEMS[key]||V86_CUSTOM[key];
      // 应用商店装好的系统：从后端（带鉴权）真实拉取该用户自己的镜像，转 blob 给 v86
      if(key.startsWith("store_")){
        const st=V86_STORE[key];
        if(st){
          try{
            setSt("从商店加载镜像中…",false);
            const resp=await fetch("/api/store/file?id="+encodeURIComponent(st.id),{headers:authHeaders()});
            if(!resp.ok) throw new Error("商店镜像读取失败 "+resp.status);
            const blob=await resp.blob();
            const url=URL.createObjectURL(blob);
            const sys=st.sys||{mode:"image",slot:"fda",mem:64};
            s={ name:sys.name||st.id, mode:sys.mode||"image", slot:sys.slot||"fda",
                mem:(sys.mem||64)*1024*1024, img:url, store:true };
          }catch(e){ bootFail("E02", e.message); return; }
        }
      }
      if(!s){ tip.textContent="该系统不存在，请重新选择"; return; }
      try{ localStorage.setItem("win86.sys",key); }catch(e){}
      // 已在运行同一系统 → 不重启，直接复用（省内存也省时间）
      if(v86Inst && !v86Inst.dead && v86Inst.key===key){ fit(); return; }
      v86Destroy(v86Inst);
      screen.innerHTML="";
      bootShow();
      setSt("加载内核中…",false);
      try{ await loadV86Lib(); }catch(e){ bootFail("E01", e.message); return; }
      if(!$("#v86Stage")) return;    // 窗口已关
      setSt("加载镜像中…",false);

      const cfg={
        wasm_path:"/win/vendor/v86.wasm",
        memory_size:s.mem,
        vga_memory_size:2*1024*1024,
        screen_container:screen,
        bios:{url:"/win/vendor/seabios.bin"},
        vga_bios:{url:"/win/vendor/vgabios.bin"},
        autostart:true,
      };
      // 空白硬盘（导入面板选配）：给系统一块空盘装软件/存文件。
      // 导入 ISO/软盘 → 空白盘当主硬盘(hda)；导入的镜像本身就是硬盘 → 挂第二块(hdb)。
      if(s.hdd){
        const hbuf=new Uint8Array(s.hdd);
        if(s.mode==="bzimage" || s.slot!=="hda"){ cfg.hda={buffer:hbuf}; }
        else { cfg.hdb={buffer:hbuf}; }
      }

      // 两种启动方式：
      //  bzimage —— 直接注入内核+initrd，v86 造一个 option_rom 交给 BIOS，
      //             完全绕过 isolinux/GRUB 菜单（这是 SliTaz/DSL 能启动的关键）
      //  image   —— 挂整张镜像，走原 BIOS 引导（Kolibri 软盘镜像必须这样）
      if(s.mode==="bzimage"){
        cfg.bzimage={ url:s.kernel };
        cfg.initrd  ={ url:s.initrd };
        cfg.cmdline =s.cmdline;
        netWatch([s.kernel, s.initrd]);   // 两个文件并行，按合计算进度
      }else{
        const imgUrl = s.store ? s.img : ("/win/images/"+s.img);
        cfg[s.slot]={ url:imgUrl, async:true, cache_size:4*1024*1024 };
        if(!s.store) netWatch(imgUrl);
      }

      let emu;
      try{ emu=new window.V86(cfg); }
      catch(e){
        const m=String(e&&e.message||e);
        // 构造期抛错：若文案涉及加载/网络/下载，归为 E02（文件没下好）；否则 E03（cfg/WASM 异常）
        const code=/load|fetch|network|download|http|404|failed|url|abort|timeout/i.test(m)?"E02":"E03";
        bootFail(code, m); return;
      }
      v86Inst={emu, key, booted:false, dead:false};
      // 不要再调 emu.run()：v86 在 continue_init 末尾自己调，
      // 外部提前调会命中 this.v86===undefined，报 "reading 'run'"。
      try{ emu.mouse_set_status(true); }catch(e){}

      // 磁盘镜像模式下 v86 会发 download-progress（单个文件的字节数）。
      // bzImage 模式没有磁盘，下载进度由 netWatch 从 PerformanceResourceTiming
      // 统一统计（内核+initrd 合算），这里不要再抢着改进度条，否则两套
      // 进度会互相打架。所以只在 image 模式挂这个监听。
      if(s.mode!=="bzimage"){
        emu.add_listener("download-progress",d=>{
          if(d&&d.total){ stepOk("wasm"); stepOn("img");
            setBar(d.loaded/d.total*42);
            const sp=v86NetTick(v86Net, d.loaded, d.total);
            const kb=sp/1024, remain=sp>0?(d.total-d.loaded)/sp:0;
            tip.textContent=`已下载 ${(d.loaded/1048576).toFixed(1)} / ${(d.total/1048576).toFixed(1)} MB`
              +(sp>0?`　${kb<1024?kb.toFixed(0)+" KB/s":(kb/1024).toFixed(1)+" MB/s"}`:"　测速中…")
              +(remain>0?`　剩余约 ${fmtDur(remain)}`:"");
            setSt(`下载镜像 ${(d.loaded/1048576).toFixed(0)}/${(d.total/1048576).toFixed(0)}MB`, false);
          }
        });
      }
      emu.add_listener("download-finished",()=>{
        stepOk("img"); stepOn("bios");
        clearInterval(tickTimer); tickTimer=null;
        clearInterval(netTimer); netTimer=null;   // 下载完了，停掉测速轮询，别再覆盖提示
        setBar(46);
        tip.textContent="文件就绪，正在启动 BIOS…";
      });
      // 内核/内存盘/镜像下载失败：归 E02，把失败的文件 URL 带出来方便诊断
      // （E01=脚本拉不到，E02=镜像下载失败，E03=模拟器构造异常，E04=引导超时黑屏）
      try{ emu.add_listener("download-error",d=>{
        const u=(d&&(d.url||d.message))||"未知文件";
        bootFail("E02", "文件下载失败："+u);
      }); }catch(e){}
      emu.add_listener("emulator-ready",()=>{
        stepOk("wasm"); stepOk("img"); stepOn("bios");
        setSt("引导中…",true);
        tip.textContent="BIOS 自检中，这一步最快也要十几秒…";
        clearInterval(tickTimer); tickTimer=null;
        const t0=Date.now();
        emuReadyAt=Date.now();   // 记录 BIOS 就绪时刻，供文本模式系统兜底判定
        tickTimer=setInterval(()=>{
          const el=(Date.now()-t0)/1000, p=50+Math.min(45,el/80*45);
          if(p>parseFloat(bar.style.width||0)) setBar(p);
        },400);
      });
      // 客户机切到图形模式（画布从 300x150 变为如 1024x768）= 已进内核
      // 但 DOS 类系统全程停在 80x25 文本模式（画布恒为 300x150），永远等不到
      // width>400，骨架屏会挂死并最终误报 E04。兜底：BIOS 就绪 25 秒后模拟器
      // 仍在跑，就认定已进入系统（文本模式），收骨架让用户看真实屏幕。
      (function watch(){
        if(!v86Inst || v86Inst.emu!==emu) return;
        const cvs=screen.querySelector("canvas");
        const txtMode = emuReadyAt && (Date.now()-emuReadyAt>25000);
        let running=false; try{ running=!!emu.is_running(); }catch(e){}
        if((cvs && cvs.width>400) || (txtMode && running)){
          stepOk("bios"); stepOn("boot"); stepOk("boot"); stepOn("desk");
          clearInterval(tickTimer); tickTimer=null; setBar(97);
          tip.textContent = (cvs && cvs.width>400) ? "内核已启动，正在初始化桌面…"
                                                   : "已进入系统（文本模式，直接打字即可操作）";
          setTimeout(bootFinish,1400);
          return;
        }
        deskTimer=setTimeout(watch,400);
      })();
      // 兜底：150s 仍未进桌面 = 真卡住（黑屏/镜像不匹配），报 E04 而不是假"已启动"
      // （进度条走完≠真进桌面，验收要看屏幕像素非全黑）
      guardTimer=setTimeout(()=>{ if(v86Inst&&v86Inst.emu===emu&&!v86Inst.booted){
        bootFail("E04", "引导超时（150s 未进桌面），可能镜像不匹配或黑屏，请换 TinyCore 重试"); } },150000);
    }

    // ---- 按钮 ----
    $("#v86Boot").onclick=start;
    /* ---- 📥 导入镜像：分片上传到 win/images/，注册为自定义系统 ---- */
    $("#v86Imp").onclick=()=>{ $("#v86ImpPanel").classList.toggle("show"); };
    // ---- 64 位虚拟机（服务端 QEMU + noVNC）----
    const X64H=Object.assign({"Content-Type":"application/json"},authHeaders());
    const loadX64Isos=()=>{
      fetch("/api/vm64/isos",{method:"POST",headers:X64H,body:"{}"})
        .then(r=>r.json()).then(j=>{
          const sel=$("#x64Iso");
          const list=j.isos||[];
          sel.innerHTML=list.length
            ? list.map(i=>`<option value="${i.name}">${i.name}（${i.size_mb} MB）</option>`).join("")
            : "<option value=''>商店还没有已下载的 ISO，先去应用商店下载</option>";
        }).catch(()=>{ $("#x64Iso").innerHTML="<option value=''>列表加载失败</option>"; });
    };
    $("#v86x64Btn").onclick=()=>{ $("#v86x64Panel").classList.toggle("show"); loadX64Isos(); };
    $("#x64Cancel").onclick=()=>{ $("#v86x64Panel").classList.remove("show"); };
    $("#x64Go").onclick=async ()=>{
      const t=$("#x64Tip"), go=$("#x64Go");
      go.disabled=true; t.textContent="启动中：创建磁盘、拉起 QEMU…（首次启动约 1~3 分钟出画面）";
      try{
        const r=await fetch("/api/vm64/start",{method:"POST",headers:X64H,
          body:JSON.stringify({mem_gb:parseInt($("#x64Mem").value,10)||2,
                               cores:parseInt($("#x64Cores").value,10)||2,
                               disk_gb:parseInt($("#x64Disk").value,10)||0,
                               iso:$("#x64Iso").value})});
        const j=await r.json();
        if(!j.ok) throw new Error(j.err||"启动失败");
        $("#v86x64Panel").classList.remove("show");
        $("#x64Stage").style.display="block";
        $("#x64Frame").src="https://vm64.3920850391.dpdns.org/x64.html?autoconnect=1";
      }catch(e){ t.textContent="启动失败："+e.message; }
      finally{ go.disabled=false; }
    };
    const x64Off=async ()=>{
      try{ await fetch("/api/vm64/stop",{method:"POST",headers:X64H,body:"{}"}); }catch(e){}
      $("#x64Stage").style.display="none";
      $("#x64Frame").src="about:blank";
    };
    $("#x64Off").onclick=x64Off;
    $("#x64Stop").onclick=x64Off;
    win.addEventListener("closed", ()=>{ try{ $("#x64Frame").src="about:blank"; }catch(e){} });
    $("#v86ImpCancel").onclick=()=>{ $("#v86ImpPanel").classList.remove("show"); };
    $("#v86ImpFile").onchange=()=>{
      const f=$("#v86ImpFile").files[0];
      if(f && !$("#v86ImpName").value.trim()) $("#v86ImpName").value=f.name.replace(/\.[^.]+$/,"");
    };
    $("#v86ImpGo").onclick=async ()=>{
      const fileInput=$("#v86ImpFile"), file=fileInput.files[0];
      if(!file){ tip.textContent="先选一个 .img / .iso 镜像文件"; return; }
      if(file.size>4*1024*1024*1024){ tip.textContent="文件超过 4GB 上限"; return; }
      const name=$("#v86ImpName").value.trim()||file.name;
      const memMB=parseInt($("#v86ImpMem").value,10)||512;
      const hddBytes=parseInt($("#v86ImpHdd").value,10)||0;
      const key="custom_"+file.name.replace(/[^a-zA-Z0-9_.-]/g,"_");
      const CHUNK=8*1024*1024;
      const total=Math.ceil(file.size/CHUNK);
      $("#v86ImpGo").disabled=true;
      setSt("上传镜像中…",true);
      try{
        for(let i=0;i<total;i++){
          const slice=file.slice(i*CHUNK,(i+1)*CHUNK);
          const b64=await new Promise((res,rej)=>{
            const r=new FileReader(); r.onload=()=>res((r.result||"").split(",")[1]||""); r.onerror=rej; r.readAsDataURL(slice);
          });
          const r=await fetch("/api/upload",{method:"POST",
            headers:Object.assign({"Content-Type":"application/json"},authHeaders()),
            body:JSON.stringify({name:file.name,data:b64,dir:"images",append:i>0,offset:i*CHUNK})});
          const j=await r.json();
          if(!j.ok) throw new Error("分片 "+i+" 上传失败");
          const pct=Math.round((i+1)/total*100);
          setBar(Math.max(2,pct*0.6));
          tip.textContent=`上传 ${file.name}：${pct}%（${(file.size/1048576).toFixed(0)}MB，${i+1}/${total} 片）`;
        }
        // 注册自定义系统：按类型自动选挂载槽位
        //  .iso → 光驱（El Torito 可引导，如 ReactOS/WinXP 安装盘）
        //  <2MB → 软盘（DOS/小镜像，BIOS 优先从软驱引导）
        //  其余 → 硬盘（Windows 磁盘镜像）
        const ext=(file.name.match(/\.([^.]+)$/)||[,""])[1].toLowerCase();
        const slot = ext==="iso" ? "cdrom" : (file.size<2*1024*1024 ? "fda" : "hda");
        const def={ name, mode:"image", img:file.name, slot,
                    mem:memMB*1024*1024, hdd:hddBytes, size:(file.size/1048576).toFixed(0)+" MB",
                    custom:true, desc:"用户导入的镜像（"+(memMB)+"MB 内存"+(hddBytes?(" + "+(hddBytes/1048576)+"MB 空白硬盘"):"")+"）" };
        V86_CUSTOM[key]=def;
        const o=document.createElement("option"); o.value=key;
        o.textContent=`${name} · ${def.size} · 导入`;
        sel.appendChild(o); sel.value=key;
        try{
          const list=JSON.parse(localStorage.getItem("v86.custom")||"[]");
          list.push({key,def});
          localStorage.setItem("v86.custom",JSON.stringify(list));
        }catch(e){}
        $("#v86ImpPanel").classList.remove("show");
        tip.textContent=`${name} 已就绪（${def.size}），点「启动」开机`;
        setSt("未启动",false); setBar(2);
      }catch(e){
        tip.textContent="上传失败："+e.message+"（可重试，分片断点续传）";
        setSt("未启动",false);
      }finally{
        $("#v86ImpGo").disabled=false;
      }
    };
    $("#v86Rst").onclick=()=>{ if(v86Inst&&!v86Inst.dead){ try{ v86Inst.emu.restart(); }catch(e){ v86Destroy(v86Inst); } } else start(); };
    $("#v86Cad").onclick=()=>multi([V86_K_CTRL,V86_K_ALT,0x53]);
    $("#v86Kb").onclick=()=>{ kb.classList.toggle("show"); fit(); };
    $("#v86Full").onclick=()=>{
      if(!document.fullscreenElement) (stage.requestFullscreen||stage.webkitRequestFullscreen||function(){}).call(stage);
      else document.exitFullscreen();
      setTimeout(fit,150);
    };
    sel.onchange=()=>{
      const s=V86_SYSTEMS[sel.value], mb=parseFloat(s.size);
      const direct = s.mode==="bzimage";
      const how = direct ? "直导内核（已绕过 isolinux 菜单，能可靠启动）" : "从镜像引导";
      tip.textContent = mb>=40
        ? `⚠ ${s.name} 需下载 ${s.size}，慢网可能要等 ${Math.round(mb/0.05/60)}~${Math.round(mb/0.7/60)} 分钟。点「启动」继续。`
        : `${s.name} · ${s.size} · ${how}。首次下载后走浏览器缓存。`;
    };

    // 窗口关闭 → 真正释放 WASM 内存（低内存手机必须做）
    win.addEventListener("closed",()=>{ clearTimers(); v86Destroy(v86Inst); });

    // 暴露给自动化测试
    window.__v86win={start, fit, typeStr, tap, multi, dblClick, clickOnce, clickRight,
      get emu(){ return v86Inst&&v86Inst.emu; }, v86InstRef:()=>v86Inst};
  }
};
