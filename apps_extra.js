// ============================================================
// 桌面附加应用：「更新记录」「问题反馈」
// 数据源：changelog.js（window.CHANGELOG / window.CURRENT_VERSION）
// 反馈提交：POST /api/feedback → 服务端落盘 feedback/feedback.jsonl
// ============================================================

/* ---------------- 更新记录 ---------------- */
APP_REGISTRY["更新记录"] = {
  icon:"更新记录", dash:true, width:620, height:540,
  render: () => `
    <div class="app-cl">
      <div class="cl-head">
        <div class="cl-cur">
          <span class="cl-ver">${window.CURRENT_VERSION||"v2.7"}</span>
          <span class="cl-cur-lb">当前版本</span>
          <button id="clChk" style="margin-left:12px;padding:6px 14px;border:0;border-radius:8px;background:linear-gradient(180deg,#4f7cff,#3a5ee0);color:#fff;font-size:12.5px;cursor:pointer">检查更新</button>
        </div>
        <div class="cl-sub">版本更新记录 · 共 ${(window.CHANGELOG||[]).length} 个版本</div>
      </div>
      <div id="clUpd"></div>
      <div class="cl-list">
        ${(window.CHANGELOG||[]).map(v=>`
          <div class="cl-item">
            <div class="cl-item-head">
              <span class="cl-v">${v.ver}</span>
              <span class="cl-tag t-${v.tag}">${v.tag}</span>
              <span class="cl-date">${v.date}</span>
            </div>
            <ul class="cl-items">${v.items.map(it=>`<li>${it}</li>`).join("")}</ul>
          </div>`).join("")}
      </div>
    </div>`,
  onMount(body){
    const box = body.querySelector("#clUpd");
    const btn = body.querySelector("#clChk");
    const esc = s => String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
    const say = (html) => { box.innerHTML = html; };
    const idle = () => say("");
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = "检查中…";
      try{
        const r = await fetch("/api/version", {
          method:"POST", headers:{"Content-Type":"application/json"},
          body:"{}"
        });
        const j = await r.json();
        const sv = (j && j.version) || "";
        const mine = window.CURRENT_VERSION || "";
        if(!sv){
          say(`<div class="upd-card" style="border-color:#e6b96a;background:#fff7e6">⚠ 无法获取服务器版本（服务端可能未升级此接口），稍后再试。</div>`);
        }else if(sv === mine){
          say(`<div class="upd-card" style="border-color:#9ad29a;background:#eefaf0">✓ 已是最新版本（${esc(mine)}），与服务器一致。</div>`);
          setTimeout(idle, 4000);
        }else{
          // 发现新版本：提示刷新更新，或取消留在旧版本
          say(`<div class="upd-card" style="border-color:#7cb1f5;background:#eef5ff">
            <div style="font-weight:700;margin-bottom:6px">🚀 发现新版本 ${esc(sv)}（当前 ${esc(mine)}）</div>
            <div style="font-size:12.5px;opacity:.75;margin-bottom:10px">刷新页面即可完成更新。也可以取消，继续体验当前版本——但取消后旧版本提交的反馈将不会被记录。</div>
            <div style="display:flex;gap:8px">
              <button id="updGo" style="padding:7px 16px;border:0;border-radius:8px;background:linear-gradient(180deg,#4f7cff,#3a5ee0);color:#fff;font-size:13px;cursor:pointer">刷新完成更新</button>
              <button id="updNo" style="padding:7px 16px;border:1px solid rgba(0,0,0,.18);border-radius:8px;background:#fff;font-size:13px;cursor:pointer">取消，继续用旧版本</button>
            </div>
          </div>`);
          body.querySelector("#updGo").onclick = () => {
            say(`<div class="upd-card" style="border-color:#7cb1f5;background:#eef5ff">⏳ 正在刷新完成更新到 ${esc(sv)} …</div>`);
            setTimeout(() => location.reload(), 500);
          };
          body.querySelector("#updNo").onclick = () => {
            window.__stayOldVer = mine;
            say(`<div class="upd-card" style="border-color:#e6a0a0;background:#fdf1f1">已保留当前版本 ${esc(mine)}。注意：旧版本提交的反馈不会被记录；随时可回到这里点「检查更新」升级。</div>`);
          };
        }
      }catch(e){
        say(`<div class="upd-card" style="border-color:#e6b96a;background:#fff7e6">⚠ 网络异常：${esc(e.message)}，请稍后再试。</div>`);
      }finally{
        btn.disabled = false; btn.textContent = "检查更新";
      }
    };
  }
};

/* ---------------- 问题反馈 ---------------- */
APP_REGISTRY["问题反馈"] = {
  icon:"问题反馈", dash:true, width:480, height:520,
  render: () => `
    <div class="app-fb">
      <div class="fb-row">
        <label>问题类型</label>
        <select id="fbType">
          <option>功能异常</option>
          <option>界面显示</option>
          <option>使用建议</option>
          <option>其他</option>
        </select>
      </div>
      <div class="fb-row">
        <label>标题</label>
        <input type="text" id="fbTitle" placeholder="一句话描述问题，如：虚拟机启动后黑屏" maxlength="60">
      </div>
      <div class="fb-row">
        <label>详细描述</label>
        <textarea id="fbDetail" placeholder="怎么出现的、点了什么、期望是什么样……写得越细修得越准" maxlength="2000"></textarea>
      </div>
      <div class="fb-row">
        <label>联系方式</label>
        <input type="text" id="fbContact" placeholder="选填，方便追问（微信/QQ/邮箱）" maxlength="60">
      </div>
      <div class="fb-row fb-btns">
        <button class="pri" id="fbGo">提交反馈</button>
        <button id="fbMine">📋 查我的反馈</button>
        <button id="fbClear">清空</button>
      </div>
      <div class="fb-st" id="fbSt"></div>
      <div id="fbMineList"></div>
      <div class="fb-tip">提交后保存到本地反馈文件，我会抽时间读取并修复。同类问题修好后会在「更新记录」里写明。</div>
    </div>`,
  onMount(body){
    const $ = s => body.querySelector(s);
    const st = $("#fbSt");
    const say = (t, ok) => { st.textContent = t; st.className = "fb-st" + (ok ? " ok" : ""); };
    const esc = s => String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
    // 查询本机（IPv6 /64 组）历史反馈与回复
    $("#fbMine").onclick = async () => {
      const box = $("#fbMineList");
      box.innerHTML = '<div style="font-size:12.5px;opacity:.6">查询中…</div>';
      try{
        const r = await fetch("/api/feedback/query", {
          method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"
        });
        const j = await r.json();
        if(!j.ok){ box.innerHTML = ""; say(j.err||"查询失败", false); return; }
        box.innerHTML = `<div style="font-size:13px;font-weight:700;margin:10px 0 4px">📋 本机历史反馈（${j.count} 条，按网络归属匹配）</div>` +
          (j.list.length ? j.list.map(f=>`
            <div style="border:1px solid rgba(0,0,0,.1);border-radius:10px;padding:8px 10px;margin:6px 0;font-size:12.5px">
              <div><b>${esc(f.title)}</b>
                <span style="margin-left:6px;font-size:11px;padding:1px 8px;border-radius:10px;${f.fixed?'background:#e3f7e9;color:#1c7c3c':'background:#fdf1e3;color:#9a6b1f'}">${f.fixed?'✓ 已修复':'处理中'}</span>
                <span style="opacity:.55;font-size:11px;margin-left:6px">${esc(f.ts)}</span></div>
              ${f.detail?`<div style="opacity:.75;margin-top:3px">${esc(f.detail)}</div>`:''}
              ${f.reply?`<div style="margin-top:4px;background:#eef5ff;border-radius:8px;padding:5px 8px">↩ ${esc(f.reply)}</div>`:''}
            </div>`).join("")
          : '<div style="font-size:12.5px;opacity:.6">这台网络还没有提交过反馈</div>');
      }catch(e){
        box.innerHTML = ""; say("查询失败："+e.message, false);
      }
    };
    $("#fbClear").onclick = () => {
      $("#fbTitle").value = ""; $("#fbDetail").value = ""; $("#fbContact").value = "";
      say("", false);
    };
    $("#fbGo").onclick = async () => {
      const title = $("#fbTitle").value.trim();
      const detail = $("#fbDetail").value.trim();
      if(!title){ say("先填个标题，一句话就行", false); return; }
      if(detail.length < 5){ say("详细描述再写几个字，方便定位", false); return; }
      $("#fbGo").disabled = true;
      say("提交中…", false);
      try{
        const r = await fetch("/api/feedback", {
          method:"POST",
          headers: Object.assign({"Content-Type":"application/json"}, authHeaders()),
          body: JSON.stringify({
            type: $("#fbType").value,
            title, detail,
            contact: $("#fbContact").value.trim(),
            url: location.href,
            ua: navigator.userAgent,
            ver: window.CURRENT_VERSION || ""
          })
        });
        const j = await r.json();
        if(j && j.ok){
          if(j.stale){
            say("你正在使用旧版本，本次反馈未被记录。请到「更新记录」点「检查更新」升级后再提交。", false);
          }else{
            say(`已收到，编号 ${j.id}。保存到本地了，我会抽时间读取修复。`, true);
            $("#fbTitle").value = ""; $("#fbDetail").value = ""; $("#fbContact").value = "";
          }
        }else{
          say("提交失败：" + ((j && j.err) || ("HTTP " + r.status)) + "，稍后再试", false);
        }
      }catch(e){
        say("网络异常：" + e.message, false);
      }finally{
        $("#fbGo").disabled = false;
      }
    };
  }
};
