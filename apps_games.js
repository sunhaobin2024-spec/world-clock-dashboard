// ============================================================
// 小游戏模块：自创 5 个可玩游戏 + 桌面「小游戏」文件夹 + 网页版 Minecraft
// 每个游戏都是标准桌面应用；关闭窗口时清理定时器 / 键盘监听，避免内存泄漏。
// ============================================================
(function () {
  // 注意：apps.js 里的 APP_REGISTRY 是顶层 const（词法全局，不挂在 window 上），
  // 必须用裸 APP_REGISTRY 引用同一个对象，不能写 window.APP_REGISTRY（否则会新建独立对象，openApp 查不到）。
  const REG = APP_REGISTRY;

  // ---------------------------------------------------------- 2048
  REG["2048"] = {
    icon: "2048", dash: false, store: true, width: 420, height: 540,
    render: () => `
    <div class="g2048">
      <div class="g-bar"><b>2048</b><span>分数 <i id="g2s">0</i></span></div>
      <div class="g-bar2"><button class="g-btn" id="g2new">新游戏</button><span id="g2msg"></span></div>
      <div class="g-board" id="g2b"></div>
      <div class="g-hint">方向键 / 滑动 移动方块，相同数字合并到 2048</div>
    </div>`,
    onMount: (body, win) => {
      const SIZE = 4, N = 72, GAP = 6;
      let grid, score = 0, over = false;
      const board = body.querySelector('#g2b'), scoreEl = body.querySelector('#g2s'), msg = body.querySelector('#g2msg');
      const empty = () => Array.from({ length: SIZE }, () => Array(SIZE).fill(0));
      function addR() {
        const e = []; for (let r = 0; r < SIZE; r++) for (let c = 0; c < SIZE; c++) if (!grid[r][c]) e.push([r, c]);
        const [r, c] = e[Math.floor(Math.random() * e.length)]; grid[r][c] = Math.random() < 0.9 ? 2 : 4;
      }
      function draw() {
        board.innerHTML = '';
        for (let r = 0; r < SIZE; r++) for (let c = 0; c < SIZE; c++) {
          const v = grid[r][c], cell = document.createElement('div');
          cell.className = 'g2c' + (v ? ' v' + Math.min(v, 2048) : '');
          cell.textContent = v || '';
          cell.style.left = (c * (N + GAP) + GAP) + 'px';
          cell.style.top = (r * (N + GAP) + GAP) + 'px';
          board.appendChild(cell);
        }
        scoreEl.textContent = score;
      }
      function slide(row) {
        let a = row.filter(x => x), res = [];
        for (let i = 0; i < a.length; i++) { if (i + 1 < a.length && a[i] === a[i + 1]) { res.push(a[i] * 2); score += a[i] * 2; i++; } else res.push(a[i]); }
        while (res.length < SIZE) res.push(0); return res;
      }
      function trans(g) { const t = empty(); for (let i = 0; i < SIZE; i++) for (let j = 0; j < SIZE; j++) t[j][i] = g[i][j]; return t; }
      function move(dir) {
        if (over) return;
        const before = JSON.stringify(grid);
        if (dir === 'left') grid = grid.map(slide);
        else if (dir === 'right') grid = grid.map(r => slide(r.slice().reverse()).reverse());
        else if (dir === 'up') grid = trans(grid.map(slide));
        else if (dir === 'down') grid = trans(trans(grid).map(r => slide(r.slice().reverse()).reverse()));
        if (JSON.stringify(grid) !== before) { addR(); draw(); check(); }
      }
      function check() {
        let full = true, merge = false, win = false;
        for (let r = 0; r < SIZE; r++) for (let c = 0; c < SIZE; c++) {
          if (grid[r][c] === 0) full = false;
          if (grid[r][c] === 2048) win = true;
          if (grid[r][c] && c + 1 < SIZE && grid[r][c + 1] === grid[r][c]) merge = true;
          if (grid[r][c] && r + 1 < SIZE && grid[r + 1][c] === grid[r][c]) merge = true;
        }
        if (win) msg.textContent = '🎉 合成 2048！';
        else if (full && !merge) { over = true; msg.textContent = '游戏结束'; }
      }
      function init() { grid = empty(); score = 0; over = false; msg.textContent = ''; addR(); addR(); draw(); }
      body.querySelector('#g2new').onclick = init;
      const onKey = e => { if (!win.classList.contains('focused')) return; const m = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }; if (m[e.key]) { e.preventDefault(); move(m[e.key]); } };
      document.addEventListener('keydown', onKey);
      let sx = 0, sy = 0;
      board.addEventListener('touchstart', e => { const t = e.touches[0]; sx = t.clientX; sy = t.clientY; }, { passive: true });
      board.addEventListener('touchend', e => { const t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy; if (Math.abs(dx) < 20 && Math.abs(dy) < 20) return; if (Math.abs(dx) > Math.abs(dy)) move(dx > 0 ? 'right' : 'left'); else move(dy > 0 ? 'down' : 'up'); }, { passive: true });
      win.addEventListener('closed', () => document.removeEventListener('keydown', onKey));
      init();
    }
  };

  // ---------------------------------------------------------- 贪吃蛇
  REG["贪吃蛇"] = {
    icon: "贪吃蛇", dash: false, store: true, width: 360, height: 470,
    render: () => `
    <div class="g-snake">
      <div class="g-bar"><b>🐍 贪吃蛇</b><span>得分 <i id="sns">0</i></span></div>
      <canvas id="sncv" width="300" height="300" class="gcv"></canvas>
      <div class="g-bar2"><button class="g-btn" id="snstart">重新开始</button><span id="snmsg">方向键 / 滑动</span></div>
    </div>`,
    onMount: (body, win) => {
      const cv = body.querySelector('#sncv'), ctx = cv.getContext('2d'), G = 15, C = 20;
      let snake, dir, next, food, timer, alive, score = 0;
      const scoreEl = body.querySelector('#sns'), msg = body.querySelector('#snmsg');
      function placeFood() { do { food = { x: Math.floor(Math.random() * G), y: Math.floor(Math.random() * G) }; } while (snake.some(s => s.x === food.x && s.y === food.y)); }
      function draw() {
        ctx.fillStyle = '#0f1b13'; ctx.fillRect(0, 0, 300, 300);
        ctx.strokeStyle = 'rgba(255,255,255,.05)';
        for (let i = 0; i <= G; i++) { ctx.beginPath(); ctx.moveTo(i * C, 0); ctx.lineTo(i * C, 300); ctx.stroke(); ctx.beginPath(); ctx.moveTo(0, i * C); ctx.lineTo(300, i * C); ctx.stroke(); }
        ctx.fillStyle = '#ff5b6e'; ctx.fillRect(food.x * C + 3, food.y * C + 3, C - 6, C - 6);
        snake.forEach((s, i) => { ctx.fillStyle = i === 0 ? '#52f2a8' : '#2fae74'; ctx.fillRect(s.x * C + 2, s.y * C + 2, C - 4, C - 4); });
      }
      function step() {
        if (!alive) return; dir = next;
        const h = { x: snake[0].x + dir.x, y: snake[0].y + dir.y };
        if (h.x < 0 || h.x >= G || h.y < 0 || h.y >= G || snake.some(s => s.x === h.x && s.y === h.y)) { alive = false; msg.textContent = '💀 游戏结束，点重新开始'; clearInterval(timer); return; }
        snake.unshift(h);
        if (h.x === food.x && h.y === food.y) { score++; scoreEl.textContent = score; placeFood(); } else snake.pop();
        draw();
      }
      function start() { if (timer) clearInterval(timer); snake = [{ x: 7, y: 7 }, { x: 6, y: 7 }, { x: 5, y: 7 }]; dir = { x: 1, y: 0 }; next = { x: 1, y: 0 }; placeFood(); score = 0; alive = true; scoreEl.textContent = 0; msg.textContent = ''; draw(); timer = setInterval(step, 120); }
      body.querySelector('#snstart').onclick = start;
      const onKey = e => { if (!win.classList.contains('focused')) return; const k = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }; if (k[e.key]) { e.preventDefault(); const [x, y] = k[e.key]; if ((x + dir.x) === 0 && (y + dir.y) === 0) return; next = { x, y }; } };
      document.addEventListener('keydown', onKey);
      let sx = 0, sy = 0;
      cv.addEventListener('touchstart', e => { const t = e.touches[0]; sx = t.clientX; sy = t.clientY; }, { passive: true });
      cv.addEventListener('touchend', e => { const t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy; if (Math.abs(dx) < 20 && Math.abs(dy) < 20) return; if (Math.abs(dx) > Math.abs(dy)) { const x = dx > 0 ? 1 : -1; if ((x + dir.x) !== 0) next = { x, y: 0 }; } else { const y = dy > 0 ? 1 : -1; if ((y + dir.y) !== 0) next = { x: 0, y }; } }, { passive: true });
      win.addEventListener('closed', () => { if (timer) clearInterval(timer); document.removeEventListener('keydown', onKey); });
      start();
    }
  };

  // ---------------------------------------------------------- 俄罗斯方块
  REG["俄罗斯方块"] = {
    icon: "俄罗斯方块", dash: false, store: true, width: 280, height: 500,
    render: () => `
    <div class="g-tetris">
      <div class="g-bar"><b>🧱 俄罗斯方块</b><span>分 <i id="tts">0</i> · 行 <i id="ttl">0</i></span></div>
      <canvas id="ttcv" width="200" height="400" class="gcv"></canvas>
      <div class="g-bar2"><button class="g-btn" id="ttpause">暂停</button><span id="ttmsg">←→移动 ↑旋转 空格速降</span></div>
    </div>`,
    onMount: (body, win) => {
      const cv = body.querySelector('#ttcv'), ctx = cv.getContext('2d'), COLS = 10, ROWS = 20, C = 20;
      const SHAPES = {
        I: [[0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0], [0, 0, 0, 0]],
        O: [[1, 1], [1, 1]], T: [[0, 1, 0], [1, 1, 1], [0, 0, 0]],
        S: [[0, 1, 1], [1, 1, 0], [0, 0, 0]], Z: [[1, 1, 0], [0, 1, 1], [0, 0, 0]],
        J: [[1, 0, 0], [1, 1, 1], [0, 0, 0]], L: [[0, 0, 1], [1, 1, 1], [0, 0, 0]]
      };
      const COL = { I: '#3ad0e0', O: '#f2d23a', T: '#b15be0', S: '#5be05b', Z: '#e05b5b', J: '#5b6fe0', L: '#e09a3a' };
      let board, cur, curType, curX, curY, score, lines, timer, paused, alive, speed;
      const scoreEl = body.querySelector('#tts'), lineEl = body.querySelector('#ttl'), msg = body.querySelector('#ttmsg');
      const rnd = () => { const t = Object.keys(SHAPES); return t[Math.floor(Math.random() * t.length)]; };
      const rotate = m => { const n = m.length, r = Array.from({ length: n }, () => Array(n).fill(0)); for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) r[j][n - 1 - i] = m[i][j]; return r; };
      const newBoard = () => Array.from({ length: ROWS }, () => Array(COLS).fill(0));
      function collide(x, y, m) { for (let r = 0; r < m.length; r++) for (let c = 0; c < m[r].length; c++) { if (!m[r][c]) continue; const bx = x + c, by = y + r; if (bx < 0 || bx >= COLS || by >= ROWS) return true; if (by >= 0 && board[by][bx]) return true; } return false; }
      function spawn() { curType = rnd(); cur = SHAPES[curType].map(r => r.slice()); curX = Math.floor((COLS - cur[0].length) / 2); curY = 0; if (collide(curX, curY, cur)) { alive = false; msg.textContent = '游戏结束'; clearInterval(timer); return; } }
      function merge() { for (let r = 0; r < cur.length; r++) for (let c = 0; c < cur[r].length; c++) if (cur[r][c] && curY + r >= 0) board[curY + r][curX + c] = curType; }
      function clearLines() { let cl = 0; for (let r = ROWS - 1; r >= 0; r--) { if (board[r].every(v => v)) { board.splice(r, 1); board.unshift(Array(COLS).fill(0)); cl++; r++; } } if (cl) { lines += cl; score += [0, 100, 300, 500, 800][cl]; scoreEl.textContent = score; lineEl.textContent = lines; speed = Math.max(80, 500 - lines * 15); if (timer) { clearInterval(timer); timer = setInterval(loop, speed); } } }
      function step() { if (!alive || paused) return; if (!collide(curX, curY + 1, cur)) curY++; else { merge(); clearLines(); spawn(); } draw(); }
      const loop = () => step();
      function draw() {
        ctx.fillStyle = '#0c1320'; ctx.fillRect(0, 0, 200, 400);
        for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) if (board[r][c]) { ctx.fillStyle = COL[board[r][c]]; ctx.fillRect(c * C + 1, r * C + 1, C - 2, C - 2); }
        if (cur) for (let r = 0; r < cur.length; r++) for (let c = 0; c < cur[r].length; c++) if (cur[r][c] && curY + r >= 0) { ctx.fillStyle = COL[curType]; ctx.fillRect((curX + c) * C + 1, (curY + r) * C + 1, C - 2, C - 2); }
      }
      function move(dx) { if (!alive || paused) return; if (!collide(curX + dx, curY, cur)) { curX += dx; draw(); } }
      function rot() { if (!alive || paused) return; const m = rotate(cur); if (!collide(curX, curY, m)) cur = m; else if (!collide(curX - 1, curY, m)) { curX--; cur = m; } else if (!collide(curX + 1, curY, m)) { curX++; cur = m; } else if (!collide(curX, curY - 1, m)) { curY--; cur = m; } if (cur !== m && collide(curX, curY, cur)) {} draw(); }
      function hard() { if (!alive || paused) return; while (!collide(curX, curY + 1, cur)) curY++; step(); }
      function start() { if (timer) clearInterval(timer); board = newBoard(); score = 0; lines = 0; alive = true; paused = false; speed = 500; scoreEl.textContent = 0; lineEl.textContent = 0; msg.textContent = ''; spawn(); draw(); timer = setInterval(loop, speed); }
      body.querySelector('#ttpause').onclick = () => { if (!alive) return; paused = !paused; msg.textContent = paused ? '已暂停' : '继续'; };
      const onKey = e => { if (!win.classList.contains('focused')) return; if (e.code === 'ArrowLeft') { e.preventDefault(); move(-1); } else if (e.code === 'ArrowRight') { e.preventDefault(); move(1); } else if (e.code === 'ArrowUp') { e.preventDefault(); rot(); } else if (e.code === 'ArrowDown') { e.preventDefault(); step(); } else if (e.code === 'Space') { e.preventDefault(); hard(); } };
      document.addEventListener('keydown', onKey);
      win.addEventListener('closed', () => { if (timer) clearInterval(timer); document.removeEventListener('keydown', onKey); });
      start();
    }
  };

  // ---------------------------------------------------------- 扫雷
  REG["扫雷"] = {
    icon: "扫雷", dash: false, store: true, width: 360, height: 470,
    render: () => `
    <div class="g-mine">
      <div class="g-bar"><b>💣 扫雷</b><span>💣 <i id="mnM">10</i> · ⏱ <i id="mnT">0</i></span></div>
      <div class="g-bar2">
        <button class="g-btn" data-d="easy">初级</button>
        <button class="g-btn" data-d="mid">中级</button>
        <button class="g-btn" data-d="hard">高级</button>
        <button class="g-btn" id="mnnew">重开</button>
      </div>
      <div class="g-mgrid" id="mngrid"></div>
      <div class="g-hint" id="mnmsg">左键翻开 · 右键插旗</div>
    </div>`,
    onMount: (body, win) => {
      let R, C, M, grid, revealed, flags, started, dead, won, timerInt, sec;
      const gridEl = body.querySelector('#mngrid'), msg = body.querySelector('#mnmsg'), mEl = body.querySelector('#mnM'), tEl = body.querySelector('#mnT');
      const cfg = d => d === 'easy' ? [9, 9, 10] : d === 'mid' ? [12, 12, 22] : [16, 16, 40];
      function start(d) {
        [R, C, M] = cfg(d || 'easy'); grid = []; revealed = Array.from({ length: R }, () => Array(C).fill(false)); flags = Array.from({ length: R }, () => Array(C).fill(false));
        started = false; dead = false; won = false; sec = 0;
        if (timerInt) clearInterval(timerInt); tEl.textContent = 0; mEl.textContent = M; msg.textContent = ''; build();
      }
      function build() {
        grid = Array.from({ length: R }, () => Array(C).fill(0));
        let p = 0; while (p < M) { const r = Math.floor(Math.random() * R), c = Math.floor(Math.random() * C); if (grid[r][c] !== -1) { grid[r][c] = -1; p++; } }
        for (let r = 0; r < R; r++) for (let c = 0; c < C; c++) { if (grid[r][c] === -1) continue; let n = 0; for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) { const rr = r + dr, cc = c + dc; if (rr >= 0 && rr < R && cc >= 0 && cc < C && grid[rr][cc] === -1) n++; } grid[r][c] = n; }
        render();
      }
      function render() {
        gridEl.style.gridTemplateColumns = `repeat(${C},22px)`;
        gridEl.innerHTML = '';
        for (let r = 0; r < R; r++) for (let c = 0; c < C; c++) {
          const b = document.createElement('div'); b.className = 'mnc'; const v = grid[r][c], rv = revealed[r][c], fl = flags[r][c];
          if (rv) { if (v === -1) { b.classList.add('mine'); b.textContent = '💣'; } else if (v > 0) { b.classList.add('n' + Math.min(v, 8)); b.textContent = v; } else b.classList.add('open'); }
          else if (fl) b.textContent = '🚩';
          b.onclick = () => reveal(r, c);
          b.oncontextmenu = e => { e.preventDefault(); toggleFlag(r, c); };
          gridEl.appendChild(b);
        }
      }
      function reveal(r, c) {
        if (dead || won || flags[r][c] || revealed[r][c]) return;
        if (!started) { started = true; timerInt = setInterval(() => { sec++; tEl.textContent = sec; }, 1000); }
        if (grid[r][c] === -1) { dead = true; if (timerInt) clearInterval(timerInt); for (let i = 0; i < R; i++) for (let j = 0; j < C; j++) if (grid[i][j] === -1) revealed[i][j] = true; msg.textContent = '💥 踩雷了'; render(); return; }
        const st = [[r, c]];
        while (st.length) { const [y, x] = st.pop(); if (y < 0 || y >= R || x < 0 || x >= C) continue; if (revealed[y][x] || flags[y][x]) continue; revealed[y][x] = true; if (grid[y][x] === 0) for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) st.push([y + dr, x + dc]); }
        let left = 0; for (let i = 0; i < R; i++) for (let j = 0; j < C; j++) if (grid[i][j] !== -1 && !revealed[i][j]) left++;
        if (left === 0) { won = true; if (timerInt) clearInterval(timerInt); msg.textContent = '🎉 通关！'; }
        render();
      }
      function toggleFlag(r, c) { if (dead || won || revealed[r][c]) return; flags[r][c] = !flags[r][c]; render(); }
      body.querySelector('#mnnew').onclick = () => start();
      body.querySelectorAll('[data-d]').forEach(b => b.onclick = () => start(b.dataset.d));
      win.addEventListener('closed', () => { if (timerInt) clearInterval(timerInt); });
      start();
    }
  };

  // ---------------------------------------------------------- 井字棋
  REG["井字棋"] = {
    icon: "井字棋", dash: false, store: true, width: 360, height: 460,
    render: () => `
    <div class="g-ttt">
      <div class="g-bar"><b>⭕ 井字棋</b><span id="tttmsg">你执 X，先手</span></div>
      <div class="g-tttgrid" id="tttgrid"></div>
      <div class="g-bar2"><button class="g-btn" id="tttnew">重新开始</button></div>
      <div class="g-hint">点击空格落子，AI 执 O（极小化极大）</div>
    </div>`,
    onMount: (body) => {
      let board, over;
      const gridEl = body.querySelector('#tttgrid'), msg = body.querySelector('#tttmsg');
      const winLine = b => { const L = [[0, 1, 2], [3, 4, 5], [6, 7, 8], [0, 3, 6], [1, 4, 7], [2, 5, 8], [0, 4, 8], [2, 4, 6]]; for (const [a, b1, c] of L) if (b[a] && b[a] === b[b1] && b[a] === b[c]) return b[a]; return null; };
      function minimax(b, player) {
        const w = winLine(b); if (w) return w === 'O' ? 1 : -1;
        const empt = []; for (let i = 0; i < 9; i++) if (!b[i]) empt.push(i);
        if (!empt.length) return 0;
        if (player === 'O') { let best = -2; for (const i of empt) { b[i] = 'O'; best = Math.max(best, minimax(b, 'X')); b[i] = ''; } return best; }
        let best = 2; for (const i of empt) { b[i] = 'X'; best = Math.min(best, minimax(b, 'O')); b[i] = ''; } return best;
      }
      function draw() {
        gridEl.innerHTML = '';
        for (let i = 0; i < 9; i++) { const c = document.createElement('div'); c.className = 'ttc' + (board[i] ? ' f ' + (board[i] === 'X' ? 'x' : 'o') : ''); c.textContent = board[i] || ''; c.onclick = () => play(i); gridEl.appendChild(c); }
      }
      function play(i) {
        if (over || board[i]) return; board[i] = 'X'; draw();
        const w = winLine(board); if (w) { over = true; msg.textContent = '🎉 你赢了！'; return; }
        if (board.every(v => v)) { over = true; msg.textContent = '平局'; return; }
        ai();
      }
      function ai() {
        const empt = []; for (let i = 0; i < 9; i++) if (!board[i]) empt.push(i);
        if (!empt.length) return; let best = -2, bi = empt[0];
        for (const i of empt) { board[i] = 'O'; const s = minimax(board, 'X'); board[i] = ''; if (s > best) { best = s; bi = i; } }
        board[bi] = 'O'; draw();
        const w = winLine(board); if (w) { over = true; msg.textContent = 'AI 赢了'; } else if (board.every(v => v)) { over = true; msg.textContent = '平局'; }
      }
      function init() { board = Array(9).fill(''); over = false; msg.textContent = '你执 X，先手'; draw(); }
      body.querySelector('#tttnew').onclick = init;
      init();
    }
  };

  // ---------------------------------------------------------- 小游戏文件夹（桌面常驻）
  REG["小游戏"] = {
    icon: "小游戏", dash: true, width: 580, height: 480,
    render: () => `
    <div class="gf">
      <div class="gf-title">🎮 小游戏</div>
      <div class="gf-grid" id="gfgrid"></div>
      <div class="gf-foot">点击游戏即可打开，进度各自独立</div>
    </div>`,
    onMount: (body) => {
      const games = [["2048", "2048"], ["贪吃蛇", "贪吃蛇"], ["俄罗斯方块", "俄罗斯方块"], ["扫雷", "扫雷"], ["井字棋", "井字棋"], ["Minecraft 网页版", "Minecraft"]];
      const g = body.querySelector('#gfgrid');
      games.forEach(([name, ic]) => {
        const t = document.createElement('div'); t.className = 'gf-tile';
        t.innerHTML = `<div class="gf-ic">${window.ICON(ic, 42)}</div><div class="gf-lb">${name}</div>`;
        t.onclick = () => { try { if (window.openApp) window.openApp(name); } catch (e) { } };
        g.appendChild(t);
      });
    }
  };

  // ---------------------------------------------------------- 网页版 Minecraft（mc.js 官方客户端，窗口内直达）
  // 不开新标签页：低内存手机同时驻留两个页面会被系统杀掉。
  // 窗口内自选版本 → iframe 直接加载 mc.js 官方客户端（Eaglercraft 中文版），
  // 全程单页面。线路为官方提供的镜像，加载慢可切换。
  const MC_VERS = [
    { id:"1.8.8",     lb:"1.8.8 电脑",   tip:"推荐 · 简体中文完整翻译 · 21MB", t:"pc"  },
    { id:"1.8.8wasm", lb:"1.8.8 WASM",   tip:"高性能电脑 · 极致流畅 · 9.7MB",  t:"pc"  },
    { id:"1.12.2",    lb:"1.12.2 电脑",  tip:"简体中文完整翻译 · 30MB",        t:"pc"  },
    { id:"1.12.2wasm",lb:"1.12.2 WASM",  tip:"高性能电脑 · 16MB",             t:"pc"  },
    { id:"26.2",      lb:"26.2 手机",    tip:"手机触屏操作 · 72MB",           t:"mob" },
    { id:"26.3",      lb:"26.3 手机",    tip:"手机触屏操作 · 83MB",           t:"mob" },
  ];
  const MC_SITES = {
    pc:  ["https://play.mcjs.cc/", "https://ipv6.mcjs.cc/", "https://mirror.mcjs.cc/", "https://playmcjscc.pages.dev/"],
    mob: ["https://mcjs.144449.xyz/", "https://mcjs-beta.144449.xyz/"],
  };
  REG["Minecraft 网页版"] = {
    icon: "Minecraft", dash: false, store: true, width: 940, height: 660,
    render: () => `
      <div style="position:absolute;inset:0;display:flex;flex-direction:column;background:#10131a;color:#e7e7e7;font-family:system-ui,'Microsoft YaHei',sans-serif">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.08)">
          ${MC_VERS.map((v,i)=>`<button class="g-btn mcjs-v" data-i="${i}" title="${v.tip}" style="padding:6px 12px;font-size:12.5px">${v.lb}</button>`).join("")}
          <select id="mcjsSite" title="官方线路，加载慢就换一条" style="margin-left:auto;padding:5px 8px;border-radius:8px;border:1px solid rgba(255,255,255,.15);background:#1c2530;color:#e7e7e7;font-size:12px"></select>
        </div>
        <div style="flex:1;position:relative">
          <div id="mcjsHint" style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:20px;text-align:center">
            <b style="color:#e7e7e7;font-size:17px">Minecraft 网页版 · mc.js 官方客户端</b>
            <span style="font-size:13.5px">点上方版本，就在本窗口里启动，不会打开新页面</span>
            <span style="font-size:12px;opacity:.65">电脑推荐 1.8.8 / 手机选 26.x 触屏版 · 首次进入需下载客户端（9~83MB，自动缓存）</span>
            <span style="font-size:12px;opacity:.65">加载慢就换右上角线路（官方镜像）</span>
          </div>
          <iframe id="mcframe" src="about:blank" allow="pointer-lock; fullscreen; gamepad" style="position:absolute;inset:0;width:100%;height:100%;border:0;display:none;background:#10131a"></iframe>
        </div>
      </div>`,
    onMount: (body, win) => {
      const f = body.querySelector('#mcframe');
      const hint = body.querySelector('#mcjsHint');
      const sel = body.querySelector('#mcjsSite');
      let curType = "pc";
      const fillSites = t => { sel.innerHTML = MC_SITES[t].map((s,i)=>`<option value="${i}">${t==="pc"?"电脑":"手机"}线路 ${i+1}</option>`).join(""); };
      fillSites("pc");
      body.querySelectorAll('.mcjs-v').forEach(btn => {
        btn.onclick = () => {
          const v = MC_VERS[+btn.dataset.i];
          if (v.t !== curType) { curType = v.t; fillSites(curType); }
          f.src = MC_SITES[curType][+sel.value || 0] + v.id;
          f.style.display = "block";
          hint.style.display = "none";
        };
      });
      win.addEventListener('closed', () => { try { f.src = 'about:blank'; } catch (e) { } });
    }
  };
})();
