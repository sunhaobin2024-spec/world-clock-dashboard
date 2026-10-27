/* ============================================================
   YubaoQiu 原创图标库（亚克力 / 液态玻璃风格）
   - YB_LOGO():   正版 YubaoQiu 标识（玻璃球 + Y 字徽标），原创设计
   - ICON(name):  返回对应图标的 SVG 字符串（带渐变磨砂底板）
   所有图标均为矢量，适配任意尺寸，不再使用卡通 emoji。
   ============================================================ */
(function(){
  var _u = 0;
  function S(inner, size){
    size = size || 24;
    return '<svg class="app-ic" width="'+size+'" height="'+size+'" viewBox="0 0 24 24" '+
      'xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">'+inner+'</svg>';
  }
  function grad(id,c1,c2){
    return '<defs><linearGradient id="'+id+'" x1="0" y1="0" x2="1" y2="1">'+
      '<stop offset="0" stop-color="'+c1+'"/><stop offset="1" stop-color="'+c2+'"/>'+
      '</linearGradient></defs>';
  }
  // 亚克力底板：渐变方块 + 顶部高光，glyph 为白色字形
  function tile(c1,c2,glyph,size,r){
    if(r==null) r=6.5;
    var id='tg'+(_u++);
    return S(grad(id,c1,c2)+
      '<rect x="1.6" y="1.6" width="20.8" height="20.8" rx="'+r+'" fill="url(#'+id+')"/>'+
      '<rect x="1.6" y="1.6" width="20.8" height="9.5" rx="'+r+'" fill="rgba(255,255,255,.16)"/>'+
      glyph, size);
  }
  // 兜底：未知名称用首字符做中性磨砂块
  function mono(label, size){
    var ch = (label||'?').replace(/^[\s\S]/,function(s){return s;}).slice(0,1);
    return tile('#64748b','#334155',
      '<text x="12" y="16.5" font-size="12" font-weight="600" fill="#fff" '+
      'text-anchor="middle" font-family="system-ui,sans-serif">'+ch+'</text>', size);
  }

  /* 字形（白色，24 视口内） */
  var GM = {
    /* ---- 应用启动器 ---- */
    "文件":'<path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5v8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" fill="#fff"/>',
    "终端":'<rect x="3.5" y="5" width="17" height="14" rx="2.5" fill="#0c1b12"/><path d="M6.5 9.5l3 2.6-3 2.6M12 15.2h4" stroke="#52f2a8" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "设置":'<circle cx="12" cy="12" r="3.1" fill="none" stroke="#fff" stroke-width="1.8"/><path d="M12 3.6v2.3M12 18.1v2.3M3.6 12h2.3M18.1 12h2.3M6 6l1.6 1.6M16.4 16.4 18 18M18 6l-1.6 1.6M7.6 16.4 6 18" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/>',
    "软件":'<path d="M7 8.2h10l1 11.3H6z" fill="none" stroke="#fff" stroke-width="1.7" stroke-linejoin="round"/><path d="M9.2 8.2a2.8 2.8 0 0 1 5.6 0" fill="none" stroke="#fff" stroke-width="1.7"/>',
    "虚拟机":'<rect x="3.8" y="5" width="16.4" height="11" rx="1.6" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M5.6 8h12.8M9 16.4h6M12 16.4V20" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/><path d="M7 12.5v2M10 12.5v2M14 12.5v2" stroke="#fff" stroke-width="1.3" stroke-linecap="round" opacity=".85"/>',
    "Firefox":'<circle cx="12" cy="12" r="8.4" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M12 3.6c4 2.4 4 11.4 2.6 14.4-3.2-1-3.2-5-1.6-7 .9 1.7.4 3.2-1 3.8-2.8 1.4-4.4-2-4.4-6.2 0-2.6 1.7-4.8 4.4-5" fill="none" stroke="#fff" stroke-width="1.3" opacity=".85"/>',
    "文本编辑器":'<path d="M6 3.2h8.2L19 7v13.8H6z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M8.4 9h7.2M8.4 12h7.2M8.4 15h4.6" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',
    "日志":'<path d="M6.5 3h7l4.5 4.5V21H6.5z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M13.5 3v5h5" fill="none" stroke="#fff" stroke-width="1.3"/><path d="M8.8 12.5h6.4M8.8 15.3h6.4M8.8 18.1h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round" opacity=".85"/>',
    "截图":'<rect x="3.6" y="7" width="16.8" height="11" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><circle cx="12" cy="12.5" r="3.1" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M9 7l1.1-2h3.8L15 7" stroke="#fff" stroke-width="1.4" fill="none"/>',
    "图像查看器":'<rect x="3.8" y="5" width="16.4" height="14" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><circle cx="9" cy="10" r="1.5" fill="#fff"/><path d="M4.8 16.6l4.2-4 3 3 3.2-3.2 4 4" stroke="#fff" stroke-width="1.4" fill="none" stroke-linejoin="round"/>',
    "磁盘":'<rect x="3.8" y="6" width="16.4" height="12" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><circle cx="16.6" cy="12" r="1.5" fill="#fff"/><path d="M6.6 9v6" stroke="#fff" stroke-width="1.4"/>',
    "帮助":'<circle cx="12" cy="12" r="8.4" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M9.6 9.4a2.4 2.4 0 0 1 4.4 1.3c0 1.6-2 2.1-2 3.2" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/><circle cx="12" cy="16.6" r="1.05" fill="#fff"/>',
    "磁盘使用分析器":'<path d="M12 12 12 4a8 8 0 0 1 8 8z" fill="#fff"/><path d="M12 12 20 8A8 8 0 0 0 12 4z" fill="#fff" opacity=".45"/><circle cx="12" cy="12" r="8.4" fill="none" stroke="#fff" stroke-width="1.5"/>',
    "日历":'<rect x="4" y="5" width="16" height="15" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M4 9.2h16M8 3.2v4M16 3.2v4" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/><circle cx="12" cy="14" r="1.4" fill="#fff"/>',
    "天气":'<circle cx="12" cy="12" r="4" fill="#fff"/><path d="M12 3.4v2.2M12 18.4v2.2M3.4 12h2.2M18.4 12h2.2M5.8 5.8l1.6 1.6M16.6 16.6l1.6 1.6M18.2 5.8l-1.6 1.6M7.4 16.6l-1.6 1.6" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',

    /* ---- 位置 / 导航 ---- */
    "主目录":'<path d="M3.8 11 11 4.6l7.2 6.4M5.8 10v8.6h12.4V10" stroke="#fff" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "下载":'<path d="M12 3.6v9M8.4 9.6 12 13.2l3.6-3.6M5 19.4h14" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "图片":'<rect x="3.8" y="5" width="16.4" height="14" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><circle cx="9" cy="10" r="1.5" fill="#fff"/><path d="M4.8 16.6l4.2-4 3 3 3.2-3.2 4 4" stroke="#fff" stroke-width="1.4" fill="none" stroke-linejoin="round"/>',
    "文档":'<path d="M6 3.2h8.2L19 7v13.8H6z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M8.4 9h7.2M8.4 12h7.2M8.4 15h4.6" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',
    "我的文件":'<path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5v8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" fill="#fff"/><path d="M11 11.5v5M8.5 14h5" stroke="#2b6cb0" stroke-width="1.6" stroke-linecap="round"/>',
    "系统盘":'<rect x="3.8" y="6" width="16.4" height="12" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><circle cx="16.6" cy="12" r="1.5" fill="#fff"/><path d="M6.6 9v6" stroke="#fff" stroke-width="1.4"/>',
    "workspace":'<path d="M14.5 7a2 2 0 1 0-5 0M12 9.4V12M10 12l-2.2 7M14 12l2.2 7M7.5 13.6h9" stroke="#fff" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "etc":'<circle cx="12" cy="12" r="3.1" fill="none" stroke="#fff" stroke-width="1.8"/><path d="M12 3.6v2.3M12 18.1v2.3M3.6 12h2.3M18.1 12h2.3M6 6l1.6 1.6M16.4 16.4 18 18M18 6l-1.6 1.6M7.6 16.4 6 18" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/>',
    "home":'<path d="M3.8 11 11 4.6l7.2 6.4M5.8 10v8.6h12.4V10" stroke="#fff" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "tmp":'<path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5v8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" fill="#fff"/>',
    "var":'<path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5v8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" fill="#fff"/>',
    "usr":'<path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5v8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" fill="#fff"/>',
    "其他位置":'<circle cx="12" cy="12" r="8.2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M12 7.6v8.8M7.6 12h8.8" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/>',
    "回收站":'<path d="M5 7h14M8.5 7V5h7v2M7 7l1 12.4h8L17 7M10 10v7M14 10v7" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',

    /* ---- 顶栏 / 状态 ---- */
    "活动":'<rect x="4" y="4" width="6.4" height="6.4" rx="1.6" fill="#fff"/><rect x="13.6" y="4" width="6.4" height="6.4" rx="1.6" fill="#fff"/><rect x="4" y="13.6" width="6.4" height="6.4" rx="1.6" fill="#fff"/><rect x="13.6" y="13.6" width="6.4" height="6.4" rx="1.6" fill="#fff" opacity=".45"/>',
    "屏幕共享":'<path d="M12 3.8l7 2.9v5.1c0 4-3 6.9-7 8-4-1.1-7-4-7-8V6.7z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>',
    "网络":'<path d="M4.8 9.2a11 11 0 0 1 14.4 0M8 12.4a6.4 6.4 0 0 1 8 0M11 15.6a2.2 2.2 0 0 1 2 0" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round"/><circle cx="12" cy="18.2" r="1.2" fill="#fff"/>',
    "蓝牙":'<path d="M8 7l8 10-4-3.2V7l4-3.2-8 10" stroke="#fff" stroke-width="1.6" fill="none" stroke-linejoin="round" stroke-linecap="round"/>',
    "声音":'<path d="M5 9.2v5.6h3l5 4V5.2L8 9.2z" fill="#fff"/><path d="M16.2 9.2a4 4 0 0 1 0 5.6" stroke="#fff" stroke-width="1.5" fill="none" stroke-linecap="round"/>',
    "电池":'<rect x="3.2" y="8" width="16" height="8" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><rect x="20" y="10.4" width="2" height="3.2" rx="1" fill="#fff"/><rect x="5.4" y="10.2" width="9" height="3.6" rx="1" fill="#fff"/>',
    "电源":'<path d="M12 3.8v7.2M8 7a7 7 0 1 0 8 0" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round"/>',
    "飞行":'<path d="M12 2.6l2 8.4 6.2 2.2-6.2 2.2-2 8.4-2-8.4L3.8 13.2l6.2-2.2z" fill="#fff"/>',
    "深色":'<path d="M15.5 4.6A7.4 7.4 0 1 0 19.4 16 5.8 5.8 0 0 1 15.5 4.6z" fill="#fff"/>',
    "专注":'<circle cx="12" cy="12" r="2.6" fill="#fff"/><g fill="#fff" opacity=".85"><circle cx="12" cy="5" r="1.9"/><circle cx="12" cy="19" r="1.9"/><circle cx="5" cy="12" r="1.9"/><circle cx="19" cy="12" r="1.9"/></g>',
    "方向":'<path d="M19 9A8 8 0 1 0 19 15" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><path d="M19 4.6V9h-4.4" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "音量":'<path d="M5 9.2v5.6h3l5 4V5.2L8 9.2z" fill="#fff"/><path d="M16.2 9.2a4 4 0 0 1 0 5.6" stroke="#fff" stroke-width="1.5" fill="none" stroke-linecap="round"/>',
    "亮度":'<circle cx="12" cy="12" r="4" fill="#fff"/><path d="M12 3.4v2.2M12 18.4v2.2M3.4 12h2.2M18.4 12h2.2M5.8 5.8l1.6 1.6M16.6 16.6l1.6 1.6M18.2 5.8l-1.6 1.6M7.4 16.6l-1.6 1.6" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',
    "锁":'<rect x="5" y="10" width="14" height="9.4" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M8 10V8a4 4 0 0 1 8 0v2" stroke="#fff" stroke-width="1.6" fill="none"/>',
    "挂起":'<path d="M15.5 4.6A7.4 7.4 0 1 0 19.4 16 5.8 5.8 0 0 1 15.5 4.6z" fill="#fff"/><path d="M11 10.5v3.6l2.6 1.6" stroke="#5b21b6" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "退出":'<path d="M14 4.6H7a2 2 0 0 0-2 2v10.8a2 2 0 0 0 2 2h7M14 12h6M18.6 9l-3 3 3 3" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "重启":'<path d="M19 9A8 8 0 1 0 19 15" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><path d="M19 4.6V9h-4.4" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "关机":'<path d="M12 3.8v7.2M8 7a7 7 0 1 0 8 0" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round"/>',
    "新建文件夹":'<path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5v8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" fill="#fff"/><path d="M11.2 12.4v3.6M9.4 14.2h3.6" stroke="#2b6cb0" stroke-width="1.6" stroke-linecap="round"/>',
    "粘贴":'<rect x="6" y="5" width="12" height="14" rx="2" fill="none" stroke="#fff" stroke-width="1.5"/><rect x="9" y="3" width="6" height="3.2" rx="1" fill="#fff"/>',
    "背景":'<path d="M7.5 4 16 12.5l-3.2 3.2L4.3 7.2z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M13.8 13.4 19 18.6l-2 2-5.2-5.2z" fill="#fff" opacity=".85"/>',
    "显示":'<rect x="3.8" y="5" width="16.4" height="11" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M9 20h6M12 16v4" stroke="#fff" stroke-width="1.5"/>',
    "概览":'<rect x="4" y="4" width="6.4" height="6.4" rx="1.6" fill="#fff"/><rect x="13.6" y="4" width="6.4" height="6.4" rx="1.6" fill="#fff"/><rect x="4" y="13.6" width="6.4" height="6.4" rx="1.6" fill="#fff"/><rect x="13.6" y="13.6" width="6.4" height="6.4" rx="1.6" fill="#fff" opacity=".45"/>',
    "无障碍":'<circle cx="12" cy="4.6" r="1.6" fill="#fff"/><path d="M5 8.2a7 7 0 0 1 14 0M12 8.2v5l-4.4 5.4M12 13.4l3.6 5.4" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "键盘":'<rect x="3" y="7" width="18" height="10" rx="2.2" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M7 11h.4M10 11h.4M13 11h.4M16 11h.4M7 14.4h10" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',
    "语言":'<circle cx="12" cy="12" r="8.4" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M3.6 12h16.8M12 3.6c3 3 3 13.8 0 16.8M12 3.6c-3 3-3 13.8 0 16.8" stroke="#fff" stroke-width="1.1" fill="none" opacity=".7"/>',
    "后退":'<path d="M14 6l-6 6 6 6" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "前进":'<path d="M10 6l6 6-6 6" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "上级":'<path d="M12 19V9M8 13l4-4 4 4M5 5h14" stroke="#fff" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "刷新":'<path d="M19 9A8 8 0 1 0 19 15" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><path d="M19 4.6V9h-4.4" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "主页":'<path d="M3.8 11 11 4.6l7.2 6.4M5.8 10v8.6h12.4V10" stroke="#fff" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "已安装":'<rect x="5" y="8" width="14" height="11" rx="1.6" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M5 12h14M9 8V6h6v2" stroke="#fff" stroke-width="1.5" fill="none" stroke-linejoin="round"/><path d="M9 15l2 2 4-4" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "收藏":'<path d="M12 4l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.8 7.2 19l.9-5.4L4.2 9.7l5.4-.8z" fill="#fff"/>',
    "搜索":'<circle cx="11" cy="11" r="6" fill="none" stroke="#fff" stroke-width="1.7"/><path d="M15.4 15.4 20 20" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/>',
    "书签":'<path d="M7 3.6h10v16.4l-5-3.8-5 3.8z" fill="#fff"/>',
    "历史":'<path d="M12 6.6v5.4l3.4 2" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M4.2 12a7.8 7.8 0 1 1 2.8 6" stroke="#fff" stroke-width="1.5" fill="none" stroke-linecap="round"/><path d="M4.2 12V8M4.2 12h4" stroke="#fff" stroke-width="1.4" fill="none" stroke-linecap="round"/>',
    "下载云":'<path d="M12 3.6v9M8.4 9.6 12 13.2l3.6-3.6M5 19.4h14" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "搜索浏览器":'<circle cx="11" cy="11" r="6" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M15.4 15.4 20 20" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>',

    /* ---- 应用内部 / 文件类型 ---- */
    "计算器":'<rect x="4" y="3.5" width="16" height="17" rx="2.4" fill="none" stroke="#fff" stroke-width="1.6"/><rect x="7" y="6.5" width="10" height="4" rx="1" fill="#fff"/><circle cx="9" cy="14.2" r="1" fill="#fff"/><circle cx="15" cy="14.2" r="1" fill="#fff"/><circle cx="9" cy="17.4" r="1" fill="#fff"/><circle cx="15" cy="17.4" r="1" fill="#fff"/>',
    "系统监视器":'<path d="M4 14.5l3.6-4.2 3 2.4L17.5 6" stroke="#fff" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="17.5" cy="6" r="1.7" fill="#fff"/><path d="M4 19.5h16" stroke="#fff" stroke-width="1.4" stroke-linecap="round" opacity=".7"/>',
    "文件夹":'<path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5v8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" fill="#fff"/>',
    "代码":'<path d="M9 8.5 5 12l4 3.5M15 8.5 19 12l-4 3.5" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "视频":'<rect x="4" y="6" width="16" height="12" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M10 9.2l4.2 2.8-4.2 2.8z" fill="#fff"/>',
    "音乐":'<path d="M9 16.5V6l8-2v10.5" stroke="#fff" stroke-width="1.6" fill="none" stroke-linejoin="round"/><circle cx="7" cy="16.5" r="2.1" fill="#fff"/><circle cx="15" cy="14.5" r="2.1" fill="#fff"/>',
    "压缩包":'<rect x="5" y="7" width="14" height="12" rx="1.8" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M8 7v-1.4h8V7M8 19v-1.4h8V19" stroke="#fff" stroke-width="1.4"/><path d="M12 10v6M9.5 12.5h5" stroke="#fff" stroke-width="1.3" stroke-linecap="round"/>',
    "邮件":'<rect x="4" y="6" width="16" height="12" rx="2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M5 7.5l7 5 7-5" fill="none" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>',
    "游戏":'<rect x="3" y="9" width="6.5" height="6" rx="3.2" fill="none" stroke="#fff" stroke-width="1.5"/><rect x="14.5" y="9" width="6.5" height="6" rx="3.2" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M11 6.5v4M9 8.5h4M16.5 12v1.5M15.5 13h2" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',
    "小游戏":'<path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5v8A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><circle cx="11" cy="13" r="1.4" fill="#fff"/><path d="M14.4 12.4v2M13.4 13.4h2" stroke="#fff" stroke-width="1.3" stroke-linecap="round"/>',
    "2048":'<text x="12" y="16.4" font-size="8.5" font-weight="800" fill="#fff" text-anchor="middle" font-family="system-ui,sans-serif">2048</text>',
    "贪吃蛇":'<path d="M5 13c0-3 3-3 3 0s3 3 6 3 3-3 3-3" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/><circle cx="5" cy="13" r="1.5" fill="#fff"/><circle cx="17" cy="13" r="1.3" fill="#fff"/>',
    "俄罗斯方块":'<path d="M8 6h3v3h-3zM11 6h3v3h-3zM8 9h3v3h-3zM11 9h3v3h-3z" fill="#fff"/>',
    "扫雷":'<circle cx="12" cy="13.6" r="5" fill="#fff"/><path d="M13.6 9 15.6 6.4" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/><circle cx="10.4" cy="12.2" r=".9" fill="#0b132b"/><circle cx="13.6" cy="12.2" r=".9" fill="#0b132b"/><circle cx="12" cy="14.6" r=".9" fill="#0b132b"/><path d="M10.4 12.2h3.2M12 12.2v2.4" stroke="#0b132b" stroke-width=".6"/>',
    "井字棋":'<rect x="5" y="5" width="14" height="14" rx="2.2" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M9.7 5v14M14.3 5v14M5 9.7h14M5 14.3h14" stroke="#fff" stroke-width="1" opacity=".65"/><path d="M6.6 6.8 10.4 10.6M10.4 6.8 6.6 10.6" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/><circle cx="16.8" cy="16.8" r="2" fill="none" stroke="#fff" stroke-width="1.3"/>',
    "Minecraft":'<path d="M5 6h6v3H8v3H5zM13 6h6v6h-3v3h-3z" fill="#fff" opacity=".92"/><path d="M5 6h6M11 9H8M8 9v3M13 9h6M16 9v3h-3v3" stroke="#0b132b" stroke-width=".5"/>',
    "IDE":'<rect x="4" y="4" width="16" height="16" rx="2.2" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M9 9l-2.5 3 2.5 3M15 9l2.5 3-2.5 3M11.5 8l1 8" stroke="#fff" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "办公":'<path d="M6 3.2h8.2L19 7v13.8H6z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M8.4 9h7.2M8.4 12h7.2M8.4 15h4.6" stroke="#fff" stroke-width="1.3" stroke-linecap="round"/>',
    "通讯":'<path d="M4 6h16v10H10l-4 3.5V16H4z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>',
    "窗口":'<rect x="4" y="5" width="14" height="13" rx="2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M4 8.6h16" stroke="#fff" stroke-width="1.4"/><path d="M7 6.6h.4M9 6.6h.4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/>',
    "裁剪":'<path d="M7 4v11a3 3 0 0 0 3 3h11M17 20V9a3 3 0 0 0-3-3H4" stroke="#fff" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',

    /* ---- 本轮补齐 ---- */
    "全屏":'<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "更新":'<path d="M19 9A8 8 0 1 0 19 15" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><path d="M19 4.6V9h-4.4" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "安全":'<path d="M12 3.5 19 6v5.5c0 4.5-3 7.8-7 9-4-1.2-7-4.5-7-9V6z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M9.2 12l2 2 3.6-3.8" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "相机":'<rect x="4" y="7" width="16" height="11" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M8 7l1.5-2h5L16 7" fill="none" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/><circle cx="12" cy="12.5" r="3" fill="none" stroke="#fff" stroke-width="1.5"/>',
    "关于":'<ellipse cx="12" cy="14" rx="6" ry="7" fill="#fff"/><circle cx="12" cy="8" r="5" fill="#0f172a"/><path d="M12 7l-1.6 3h3.2z" fill="#f59e0b"/><circle cx="10" cy="7" r="1" fill="#fff"/><circle cx="14" cy="7" r="1" fill="#fff"/>',
    "会议":'<rect x="4" y="6" width="16" height="13" rx="2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M8 3.5v3M16 3.5v3M4 11h16" stroke="#fff" stroke-width="1.4"/><path d="M8.5 14l2 1.5v-3z" fill="#fff"/><path d="M13 14l2 1.5v-3z" fill="#fff"/>',
    "生日":'<path d="M12 20a7 7 0 1 0 0-14 7 7 0 0 0 0 14z" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M12 6v2.5M9.4 5.6l2.6.9 2.6-.9M10.5 9.5l1.5 1.5 1.5-1.5" stroke="#fff" stroke-width="1.4" fill="none" stroke-linecap="round"/>',
    "上传":'<path d="M12 21v-9M8.4 14.4 12 10.8l3.6 3.6M5 4.6h14" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "更新记录":'<path d="M6 3.4h8.4L19 7.6V20.6H6z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M14.2 3.4v4.4h4.6" fill="none" stroke="#fff" stroke-width="1.3"/><path d="M9.2 12.2a2.9 2.9 0 1 1 4.1 2.6c-.8.4-.8 1.2-.8 1.9" fill="none" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/><circle cx="12.4" cy="18" r=".95" fill="#fff"/>',
    "问题反馈":'<path d="M4 6.2A2.2 2.2 0 0 1 6.2 4h11.6A2.2 2.2 0 0 1 20 6.2v7.6a2.2 2.2 0 0 1-2.2 2.2H9.2L5 19.6v-3.6h-.8A2.2 2.2 0 0 1 4 13.8z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M9.6 8.6a2.5 2.5 0 1 1 3.5 2.3c-.7.4-.7 1-.7 1.6" fill="none" stroke="#fff" stroke-width="1.3" stroke-linecap="round"/><circle cx="12.3" cy="14" r=".85" fill="#fff"/>',
    "聊天室":'<path d="M4 5.8A2.3 2.3 0 0 1 6.3 3.5h8.4A2.3 2.3 0 0 1 17 5.8v5.4a2.3 2.3 0 0 1-2.3 2.3H8.5L4.8 16.8v-3.3H6.3A2.3 2.3 0 0 1 4 11.2z" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/><path d="M14.5 9.5h3.2A2.3 2.3 0 0 1 20 11.8v4.4a2.3 2.3 0 0 1-2.3 2.3h-.7v2.8L13.6 18.5h-3.1a2.3 2.3 0 0 1-2.2-1.6" fill="none" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/>'
  };

  /* 配色（亚克力渐变） */
  var CM = {
    "文件":["#4a90d9","#2b6cb0"], "终端":["#1f2a24","#0c1b12"], "设置":["#7c6cf0","#5a3fd6"],
    "软件":["#f97316","#ea580c"], "虚拟机":["#2dd4bf","#0d9488"], "Firefox":["#2563eb","#1e3a8a"],
    "文本编辑器":["#14b8a6","#0b7d7c"], "日志":["#64748b","#334155"], "截图":["#6366f1","#4338ca"],
    "图像查看器":["#ec4899","#be185d"], "磁盘":["#475569","#1e293b"], "帮助":["#4f46e5","#3730a3"],
    "磁盘使用分析器":["#f59e0b","#b45309"], "日历":["#ef4444","#b91c1c"], "天气":["#f59e0b","#d97706"],
    "主目录":["#4a90d9","#2b6cb0"], "下载":["#0ea5e9","#0369a1"], "图片":["#ec4899","#be185d"],
    "文档":["#f59e0b","#b45309"], "我的文件":["#38bdf8","#0284c7"], "系统盘":["#475569","#1e293b"],
    "workspace":["#8b5cf6","#6d28d9"], "etc":["#a855f7","#7e22ce"], "home":["#4a90d9","#2b6cb0"],
    "tmp":["#64748b","#334155"], "var":["#64748b","#334155"], "usr":["#64748b","#334155"], "其他位置":["#14b8a6","#0b7d7c"],
    "回收站":["#94a3b8","#475569"],
    "更新记录":["#3b82f6","#1d4ed8"], "问题反馈":["#10b981","#047857"], "聊天室":["#22c1dc","#0e7490"],
    "活动":["#4b5563","#1f2937"], "屏幕共享":["#9333ea","#6b21a8"], "网络":["#0ea5e9","#0369a1"],
    "蓝牙":["#2563eb","#1d4ed8"], "声音":["#0ea5e9","#0369a1"], "电池":["#22c55e","#16a34a"],
    "电源":["#ef4444","#b91c1c"], "飞行":["#64748b","#334155"], "深色":["#4b5563","#1f2937"],
    "专注":["#ec4899","#be185d"], "方向":["#0891b2","#0e7490"], "音量":["#0ea5e9","#0369a1"],
    "亮度":["#f59e0b","#d97706"], "锁":["#6b7280","#374151"], "挂起":["#4b5563","#1f2937"],
    "退出":["#ef4444","#b91c1c"], "重启":["#0ea5e9","#0369a1"], "关机":["#ef4444","#b91c1c"],
    "新建文件夹":["#4a90d9","#2b6cb0"], "粘贴":["#4a90d9","#2b6cb0"], "背景":["#ec4899","#be185d"],
    "显示":["#4b5563","#1f2937"], "概览":["#4b5563","#1f2937"], "无障碍":["#0ea5e9","#0369a1"],
    "键盘":["#4b5563","#1f2937"], "语言":["#16a34a","#15803d"], "后退":["#6b7280","#374151"],
    "前进":["#6b7280","#374151"], "上级":["#6b7280","#374151"], "刷新":["#6b7280","#374151"],
    "主页":["#4a90d9","#2b6cb0"], "已安装":["#22c55e","#16a34a"], "收藏":["#f59e0b","#d97706"],
    "搜索":["#6b7280","#374151"], "书签":["#f59e0b","#d97706"], "历史":["#6b7280","#374151"],
    "下载云":["#0ea5e9","#0369a1"], "搜索浏览器":["#6b7280","#374151"],
    "计算器":["#0ea5e9","#0369a1"], "系统监视器":["#22c55e","#15803d"], "文件夹":["#4a90d9","#2b6cb0"],
    "代码":["#f59e0b","#b45309"], "视频":["#ec4899","#be185d"], "音乐":["#a855f7","#7e22ce"],
    "压缩包":["#94a3b8","#475569"], "邮件":["#f59e0b","#b45309"], "游戏":["#475569","#1e293b"],
    "IDE":["#0ea5e9","#0369a1"], "办公":["#ef4444","#b91c1c"], "通讯":["#6366f1","#4338ca"],
    "窗口":["#4b5563","#1f2937"], "裁剪":["#4b5563","#1f2937"]
  };

  window.ICON = function(name, size){
    var g = GM[name];
    if(g){ var c = CM[name] || ["#64748b","#334155"]; return tile(c[0],c[1],g,size); }
    return mono(name, size);
  };

  /* 文件列表图标：按类型返回专业 SVG（不再用 📄🐍🖼️ 等卡通 emoji） */
  window.FILE_ICON = function(type, size){
    var key, col;
    if(type==="dir"){ key="文件夹"; col=["#4a90d9","#2b6cb0"]; }
    else if(type==="link"){ key="其他位置"; col=["#14b8a6","#0b7d7c"]; }
    else if(["png","jpg","jpeg","gif","svg","webp","bmp"].indexOf(type)>=0){ key="图片"; col=["#ec4899","#be185d"]; }
    else if(["mp4","webm","mkv","mov","avi"].indexOf(type)>=0){ key="视频"; col=["#ec4899","#be185d"]; }
    else if(["mp3","wav","ogg","flac","m4a"].indexOf(type)>=0){ key="音乐"; col=["#a855f7","#7e22ce"]; }
    else if(["zip","tar","gz","bz2","xz","7z","rar"].indexOf(type)>=0){ key="压缩包"; col=["#94a3b8","#475569"]; }
    else if(["py","js","sh","css","html","json","c","cpp","go","rs","java","ts"].indexOf(type)>=0){ key="代码"; col=["#f59e0b","#b45309"]; }
    else if(["md","txt","pdf","doc","docx","rtf"].indexOf(type)>=0){ key="文档"; col=["#f59e0b","#b45309"]; }
    else if(type==="log"){ key="日志"; col=["#64748b","#334155"]; }
    else { key="文档"; col=["#f59e0b","#b45309"]; }
    return tile(col[0],col[1],GM[key],size);
  };

  /* 正版 YubaoQiu 标识：玻璃球 + Y 字徽标（原创） */
  window.YB_LOGO = function(size){
    size = size || 64;
    var id = 'yl' + (_u++);
    return '<svg class="yubaoqiu-logo" width="'+size+'" height="'+size+'" viewBox="0 0 64 64" '+
      'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'+
      '<defs><radialGradient id="'+id+'" cx="38%" cy="30%" r="78%">'+
        '<stop offset="0" stop-color="#8ff7ff"/>'+
        '<stop offset="42%" stop-color="#3aa0ff"/>'+
        '<stop offset="100%" stop-color="#5b21b6"/>'+
      '</radialGradient></defs>'+
      '<circle cx="32" cy="32" r="28" fill="url(#'+id+')"/>'+
      '<ellipse cx="23" cy="19" rx="11" ry="6.5" fill="rgba(255,255,255,.32)"/>'+
      '<path d="M23.5 21v21M23.5 23.5l12.5 17M23.5 31l11.5 13" stroke="#fff" stroke-width="3.6" '+
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'+
      '<circle cx="32" cy="32" r="28" fill="none" stroke="rgba(255,255,255,.28)" stroke-width="1.5"/>'+
    '</svg>';
  };
})();
