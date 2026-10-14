# VideoMaker API | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api: новый файл. Веб-страница пульта: статус, прогресс, стадия, ETA,
#            текущее действие, логи, очередь. Один файл, без внешних ресурсов,
#            обработчики data-act="..." (как в панели Orchestrator),
#            режим embed=1 для встраивания в чужую панель.
#   r57-api.1: таймаут запроса (зависший вызов не блокирует кнопки), понятные
#              надписи в простое, подсказка при отсутствии токена.
# PREV: (нет файла)
# NEW FILE: video_maker/api/webui.py

"""Веб-страница пульта VideoMaker (телефон + встраивание в Orchestrator)."""
from __future__ import annotations

_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>VideoMaker — пульт</title>
<style>
  :root{
    color-scheme:dark;
    --bg:#070b13;
    --card:rgba(24,32,48,.52);
    --card2:rgba(255,255,255,.10);
    --line:rgba(255,255,255,.14);
    --fg:#f4f7fd; --dim:#b9c5da; --acc:#2f6fed; --accHover:#3d7cf2;
    --ok:#2fbf77; --warn:#f3c24f; --err:#ff8080; --focus:#9cc2ff;
    --glass-hi:rgba(255,255,255,.22);
    --ease-out:cubic-bezier(0.23,1,0.32,1);
    --ease-in-out:cubic-bezier(0.77,0,0.175,1);
  }
  *{box-sizing:border-box}
  html{min-width:0;scroll-behavior:smooth;-webkit-tap-highlight-color:transparent}
  body{margin:0 auto;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       padding:clamp(10px,2.2vw,24px);max-width:1100px;min-width:0;-webkit-text-size-adjust:100%}
  body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
    background:
      radial-gradient(1100px 760px at 12% 8%, rgba(64,105,235,.34), transparent 62%),
      radial-gradient(1000px 700px at 88% 16%, rgba(128,72,232,.28), transparent 58%),
      radial-gradient(900px 900px at 72% 92%, rgba(12,166,120,.20), transparent 60%),
      radial-gradient(720px 560px at 6% 88%, rgba(232,89,12,.16), transparent 62%),
      linear-gradient(160deg,#0a0f1a 0%,#0b1120 45%,#090d16 100%)}
  h1{font-size:20px;margin:0 0 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-weight:750;letter-spacing:-.02em}
  .badge{font-size:12px;font-weight:700;padding:4px 10px;border-radius:99px;color:#e8eefb;border:1px solid var(--glass-hi);
         background:rgba(255,255,255,.09);letter-spacing:.01em;
         backdrop-filter:blur(12px) saturate(150%);-webkit-backdrop-filter:blur(12px) saturate(150%)}
  .badge.run{background:rgba(47,111,237,.30);color:#cfe1ff;border-color:rgba(120,170,255,.45)}
  .badge.err{background:rgba(150,48,64,.40);color:#ffd7d7;border-color:rgba(255,130,140,.5)}
  .card{background:linear-gradient(180deg,rgba(255,255,255,.085),rgba(255,255,255,.035));
        border:1px solid var(--line);border-top-color:var(--glass-hi);border-radius:20px;
        padding:clamp(12px,1.7vw,18px);margin:0 0 14px;min-width:0;
        backdrop-filter:blur(26px) saturate(165%);-webkit-backdrop-filter:blur(26px) saturate(165%);
        box-shadow:0 14px 44px rgba(0,0,0,.38), inset 0 1px 0 rgba(255,255,255,.07)}
  .row{display:flex;justify-content:space-between;align-items:baseline;gap:10px;min-width:0}
  .muted{color:var(--dim);font-size:13px;letter-spacing:.01em}
  .big{font-size:clamp(27px,3vw,34px);font-weight:750;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
  .bar{height:11px;background:rgba(10,15,24,.6);border:1px solid var(--line);border-radius:99px;overflow:hidden;margin:10px 0 8px;
       box-shadow:inset 0 1px 3px rgba(0,0,0,.4)}
  .bar>i{display:block;height:100%;width:100%;background:linear-gradient(90deg,#5b9bff,#a28aff);
         box-shadow:0 0 14px rgba(120,150,255,.55);transform:scaleX(0);transform-origin:left center;
         transition:transform .25s var(--ease-out)}
  .stage{font-weight:700;margin-top:8px;overflow-wrap:anywhere}
  .act{font-size:12.5px;color:#c0cce0;margin-top:7px;word-break:break-word;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace;max-height:76px;overflow:auto}
  button{font:inherit;font-weight:650;border:1px solid rgba(255,255,255,.18);border-top-color:rgba(255,255,255,.32);
         border-radius:13px;padding:11px 16px;min-height:44px;color:#fff;cursor:pointer;flex:1;touch-action:manipulation;
         user-select:none;-webkit-user-select:none;
         background:linear-gradient(180deg,#3b82f6,#2563eb);
         box-shadow:0 8px 22px rgba(37,99,235,.35), inset 0 1px 0 rgba(255,255,255,.28);
         transition:background-color 150ms var(--ease-out),border-color 150ms var(--ease-out),transform 150ms var(--ease-out),box-shadow 150ms var(--ease-out)}
  button:active:not(:disabled){transform:scale(.97)}
  button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
  button.ghost{background:rgba(255,255,255,.09);color:#f1f5fc;border-color:var(--glass-hi);
               box-shadow:inset 0 1px 0 rgba(255,255,255,.12);
               backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
  button.danger{background:linear-gradient(180deg,rgba(210,70,90,.85),rgba(150,44,60,.9));border-color:rgba(255,130,140,.5);
                box-shadow:0 8px 22px rgba(150,44,60,.3), inset 0 1px 0 rgba(255,255,255,.2)}
  button:disabled{background:rgba(255,255,255,.06);color:#9fadc4;border-color:rgba(255,255,255,.10);opacity:1;cursor:not-allowed;
                  box-shadow:none;filter:saturate(.5)}
  @media (hover:hover) and (pointer:fine){
    button:hover:not(:disabled){background:linear-gradient(180deg,#4b8ef8,#2f70e6)}
    button.ghost:hover:not(:disabled){background:rgba(255,255,255,.15);border-color:rgba(255,255,255,.35)}
    button.danger:hover:not(:disabled){background:linear-gradient(180deg,rgba(225,84,104,.9),rgba(165,50,66,.95))}
    .x:hover:not(:disabled){background:#3a4963;border-color:#8193af}
  }
  .btns{display:flex;gap:9px;margin-top:12px;flex-wrap:wrap;min-width:0}
  .btns button{min-width:132px}
  input,select,textarea{font:inherit;background:rgba(9,14,24,.62);border:1px solid rgba(255,255,255,.16);color:var(--fg);
        border-radius:12px;padding:11px 12px;min-height:44px;width:100%;min-width:0;margin-bottom:9px;
        box-shadow:inset 0 1px 2px rgba(0,0,0,.35);transition:border-color 150ms var(--ease-out),box-shadow 150ms var(--ease-out)}
  input::placeholder{color:#9aa9c0;opacity:1}
  select{cursor:pointer}
  textarea{min-height:96px;resize:vertical;line-height:1.45}
  .sw{display:flex;align-items:center;gap:9px;min-height:44px;margin:0 0 6px;font-size:14px}
  .sw input{width:20px;height:20px;min-height:0;margin:0;flex:0 0 auto;accent-color:var(--acc)}
  .fld{margin:0 0 6px}
  .fld>span{display:block;font-size:12.5px;color:var(--dim);margin:4px 0 3px;overflow-wrap:anywhere;letter-spacing:.01em}
  #settingsSections details{margin:4px 0 10px;border:1px solid rgba(255,255,255,.10);border-radius:14px;
        padding:4px 12px 8px;background:rgba(255,255,255,.035)}
  #settingsSections>details>summary{font-weight:700}
  #settingsSections details[open] .sgrid{animation:materialize 260ms var(--ease-out)}
  @keyframes materialize{from{opacity:0;transform:translateY(-3px) scale(.99);filter:blur(5px)}to{opacity:1;transform:none;filter:none}}
  .sgrid{display:grid;grid-template-columns:1fr;gap:0 16px;align-items:start}
  @media(min-width:760px){.sgrid{grid-template-columns:1fr 1fr}}
  .fld.wide{grid-column:1/-1}
  .fld.dirty>span{color:var(--focus);font-weight:650}
  .fld.dirty input,.fld.dirty select,.fld.dirty textarea{border-color:#7aa2ff;box-shadow:0 0 0 2px rgba(122,162,255,.16)}
  #settingsBar{position:sticky;bottom:0;padding-top:10px;
        background:linear-gradient(180deg,rgba(10,14,22,0) 0%,rgba(10,14,22,.72) 38%);
        backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}
  #settingsSearch{margin-bottom:0}
  ul{list-style:none;margin:12px 0 0;padding:0;min-width:0}
  li{display:flex;gap:9px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px;min-width:0}
  li:last-child{border-bottom:0}
  .dot{width:9px;height:9px;border-radius:99px;background:var(--dim);flex:0 0 auto;outline:1px solid #0f1622;outline-offset:1px}
  .dot.pending{background:var(--warn)} .dot.running{background:#66a3ff}
  .dot.done{background:var(--ok)} .dot.failed{background:var(--err)} .dot.canceled{background:var(--dim)}
  .grow{flex:1;min-width:0}
  .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
  .mini{font-size:12px;color:#bdc9dc;overflow-wrap:anywhere}
  .x{background:#273246;border:1px solid #48566d;color:#e8effa;font-size:17px;line-height:1;padding:0;width:44px;height:44px;min-width:44px;min-height:44px;flex:0 0 44px;cursor:pointer;border-radius:10px}
  pre{background:#090e16;border:1px solid #3b4a62;border-radius:10px;padding:12px;margin:8px 0 0;
      font-size:12px;line-height:1.55;max-height:clamp(200px,38vh,360px);overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
  details summary{cursor:pointer;color:#cfdaeb;font-size:14px;font-weight:650;min-height:34px;display:flex;align-items:center;gap:8px;outline:none;
                  user-select:none;-webkit-user-select:none}
  details>summary::after{content:"";width:7px;height:7px;flex:0 0 auto;margin-left:auto;
                  border-right:2px solid var(--dim);border-bottom:2px solid var(--dim);
                  transform:rotate(45deg) translateY(-1px);
                  transition:transform 200ms var(--ease-out)}
  details[open]>summary::after{transform:rotate(225deg) translateY(-1px)}
  .sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
  .hide{display:none}
  @media(min-width:720px){
    /* Планшет (портрет): статус и очередь рядом. */
    .col-left{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
    .col-left>.card{margin:0}
  }
  @media(min-width:1024px){
    /* Ноутбук и планшет-ландшафт: две панели — управление слева (закреплено),
       настройки и журнал справа. */
    body{display:grid;grid-template-columns:minmax(340px,400px) minmax(0,1fr);column-gap:16px;align-content:start;max-width:1260px}
    h1{grid-column:1/-1}
    .col-left{grid-column:1;grid-row:2;display:block;position:sticky;top:clamp(10px,2.2vw,24px);
              align-self:start;max-height:calc(100vh - 64px);overflow:auto;padding-right:2px}
    .col-left>.card{margin:0 0 14px}
    .col-right{grid-column:2;grid-row:2;min-width:0}
  }
  @media(max-width:480px){
    h1{font-size:18px;gap:8px}
    .card{border-radius:13px}
    .row{align-items:flex-start}
    .big{font-size:28px}
    .btns{gap:7px}
    .btns button{min-width:0;flex:1 1 132px;padding:11px 10px}
    .btns[style*="width:100%"] button{flex-basis:100%}
    li{gap:7px}
    .mini{font-size:11.5px}
    /* iOS не зумит страницу при фокусе поля, только если шрифт ≥16px. */
    input,select,textarea{font-size:16px}
  }
  @media(prefers-reduced-motion:reduce){
    *,*::before,*::after{scroll-behavior:auto!important;transition-duration:.01ms!important;animation-duration:.01ms!important;animation-iteration-count:1!important}
  }
  @media(prefers-reduced-transparency:reduce){
    body::before{background:var(--bg)}
    .card{background:rgba(18,24,38,.97);backdrop-filter:none;-webkit-backdrop-filter:none}
    .badge,button.ghost,#settingsBar{backdrop-filter:none;-webkit-backdrop-filter:none}
    input,select,textarea{background:#0d1420}
  }
  @media(prefers-contrast:more){
    .card{border-color:rgba(255,255,255,.45)}
    .fld>span,.muted{color:#e2e9f6}
    input,select,textarea{border-color:rgba(255,255,255,.45)}
  }
</style>
</head>
<body>
<h1 id="pageTitle">VideoMaker <span id="state" class="badge" role="status" aria-live="polite" aria-atomic="true">…</span></h1>

<div class="col-left">
<div class="card" id="statusCard">
  <div class="row"><span class="muted" id="series">—</span><span class="big" id="pct">0%</span></div>
  <div class="bar" id="progressBar" role="progressbar" aria-label="Прогресс создания видео" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i id="bar"></i></div>
  <div class="stage" id="stage">ожидание</div>
  <div class="muted" id="times">—</div>
  <div class="act" id="activity"></div>
  <label class="sr-only" for="runSeries">Серия (необязательно)</label>
  <input id="runSeries" aria-label="Серия для запуска (необязательно)" placeholder="Серия (необязательно)" autocomplete="off" style="margin-top:8px">
  <div class="btns">
    <button data-act="start" id="btnStart">Создать видео</button>
    <button data-act="cancel" class="danger" id="btnCancel" disabled>Отмена</button>
  </div>
</div>

<div class="card">
  <div class="row"><b>Очередь</b><span class="muted" id="queueInfo">—</span></div>
  <div class="btns" style="margin-top:10px">
    <button data-act="pause" class="ghost" id="btnPause">Пауза</button>
    <button data-act="clear" class="ghost" aria-label="Убрать завершённые задания из очереди">Убрать завершённые</button>
  </div>
  <div style="margin-top:12px">
    <label class="sr-only" for="qAudio">Аудио: файл или папка</label>
    <input id="qAudio" aria-label="Аудио: файл или папка" placeholder="Аудио: файл или папка" autocomplete="off">
    <label class="sr-only" for="qOutput">Папка вывода</label>
    <input id="qOutput" aria-label="Папка вывода" placeholder="Папка вывода (пусто = как в настройках)" autocomplete="off">
    <label class="sr-only" for="qSeries">Название серии</label>
    <input id="qSeries" aria-label="Название серии (необязательно)" placeholder="Название серии (необязательно)" autocomplete="off">
    <button data-act="add" class="ghost" style="width:100%" aria-label="Добавить серию в очередь обработки">Добавить в очередь</button>
  </div>
  <ul id="queue" aria-label="Очередь обработки" role="list"></ul>
</div>
</div>

<div class="col-right">

<div class="card" id="settingsCard">
  <details>
    <summary>Настройки (всё управление)</summary>
    <div id="settingsSections"></div>
    <div id="settingsBar">
      <label class="sr-only" for="settingsSearch">Поиск по настройкам</label>
      <input id="settingsSearch" placeholder="Поиск по настройкам…" autocomplete="off" aria-label="Поиск по настройкам">
      <div class="btns" style="margin-top:10px">
        <button data-act="saveSettings" id="btnSaveSettings">Сохранить настройки</button>
        <button data-act="reloadSettings" class="ghost" id="btnReloadSettings">Перечитать</button>
      </div>
      <div class="muted" id="settingsMsg" style="margin-top:8px" role="status" aria-live="polite"></div>
    </div>
  </details>
</div>

<div class="card" id="logCard">
  <details open><summary>Журнал обработки</summary><pre id="log" role="log" aria-label="Журнал обработки" aria-live="off">…</pre></details>
</div>
</div>

<script>
(function(){
  "use strict";
  var KEY = new URLSearchParams(location.search).get("key") || "";
  // The legacy launcher bootstraps the page with ?key=TOKEN. Remove it from
  // browser history/address immediately; subsequent API calls use a header.
  if (KEY && window.history && window.history.replaceState) {
    try {
      var cleanUrl = new URL(window.location.href);
      cleanUrl.searchParams.delete("key");
      window.history.replaceState({}, document.title, cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
    } catch(e) {}
  }
  var $ = function(id){ return document.getElementById(id); };
  var busy = false;

  function api(path, opts){
    opts = opts || {};
    var ms = opts.timeout || 25000;
    delete opts.timeout;
    var ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    if (ctrl) {
      opts.signal = ctrl.signal;
      setTimeout(function(){ try { ctrl.abort(); } catch(e){} }, ms);
    }
    // Keep credentials out of request URLs, browser history and access logs.
    var url = path;
    opts.headers = Object.assign({"X-Api-Key": KEY}, opts.headers || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(url, opts).catch(function(e){
      if (e && e.name === "AbortError") throw new Error("пульт не ответил за " + Math.round(ms/1000) + " с");
      throw new Error("нет связи с пультом");
    }).then(function(r){
      return r.text().then(function(t){
        var data = null;
        try { data = t ? JSON.parse(t) : null; } catch(e){ data = {raw: t}; }
        if (!r.ok) { throw new Error((data && (data.error || data.detail)) || ("HTTP " + r.status)); }
        return data;
      });
    });
  }

  function hms(sec){
    if (sec === null || sec === undefined) return "—";
    sec = Math.max(0, Math.round(sec));
    var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
    var mm = (h ? (m<10?"0":"")+m : m);
    return (h ? h + ":" + mm : mm) + ":" + (s<10?"0":"") + s;
  }

  function renderStatus(st){
    var running = !!st.running;
    var stateLabel = running ? "работает" : "свободен";
    if ($("state").textContent !== stateLabel) $("state").textContent = stateLabel;
    $("state").className = "badge" + (running ? " run" : "");
    var pct = Math.max(0, Math.min(100, Number(st.progress)||0));
    $("pct").textContent = pct.toFixed(0) + "%";
    $("bar").style.transform = "scaleX(" + (pct / 100) + ")";
    $("progressBar").setAttribute("aria-valuenow", String(Math.round(pct)));
    $("series").textContent = st.series || "—";
    var s = st.stage || {};
    $("stage").textContent = s.name ? (s.name + (s.hint ? " — " + s.hint : "")) : "ожидание";
    $("times").textContent = running
      ? ("прошло " + hms(st.elapsed_sec) + " · осталось ~" + hms(st.eta_sec))
      : (st.activity ? "простой · последнее событие выше" : "готов к запуску");
    $("activity").textContent = st.activity || "";
    $("btnStart").disabled = running;
    $("btnCancel").disabled = !running;
  }

  function renderQueue(q){
    $("queueInfo").textContent = "ждёт " + q.pending + " · готово " + q.done +
      (q.failed ? " · ошибок " + q.failed : "");
    $("btnPause").textContent = q.paused ? "Продолжить" : "Пауза";
    var ul = $("queue");
    ul.innerHTML = "";
    (q.items || []).forEach(function(it){
      var li = document.createElement("li");
      var dot = document.createElement("span");
      dot.className = "dot " + it.status;
      var box = document.createElement("div");
      box.className = "grow";
      var nm = document.createElement("div");
      nm.className = "nm";
      nm.textContent = it.series || (it.audio || "").split("/").pop();
      var sub = document.createElement("div");
      sub.className = "mini";
      var extra = "";
      if (it.status === "running" && it.running_sec) extra = " · идёт " + hms(it.running_sec);
      if (it.took_sec) extra = " · заняло " + hms(it.took_sec);
      if (it.result && it.result.files) extra += " · файлов " + it.result.files;
      sub.textContent = it.status + extra + (it.error ? " · " + it.error.slice(0, 120) : "");
      box.appendChild(nm); box.appendChild(sub);
      li.appendChild(dot); li.appendChild(box);
      if (it.status === "failed" || it.status === "canceled") {
        var rb = document.createElement("button");
        rb.className = "x"; rb.textContent = "↻"; rb.title = "повторить";
        rb.setAttribute("data-act", "retry"); rb.setAttribute("data-id", it.id); rb.setAttribute("aria-label", "Повторить серию " + (it.series || it.id));
        li.appendChild(rb);
      }
      if (it.status !== "running") {
        var xb = document.createElement("button");
        xb.className = "x"; xb.textContent = "×"; xb.title = "удалить";
        xb.setAttribute("data-act", "remove"); xb.setAttribute("data-id", it.id); xb.setAttribute("aria-label", "Удалить серию " + (it.series || it.id));
        li.appendChild(xb);
      }
      ul.appendChild(li);
    });
    if (!ul.children.length) {
      var li = document.createElement("li");
      li.className = "muted";
      li.textContent = "пусто";
      ul.appendChild(li);
    }
  }

  function renderLog(entries){
    var pre = $("log");
    var atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
    pre.textContent = (entries || []).join("\n");
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  }

  function toast(msg, bad){
    var el = $("state");
    el.textContent = (bad ? "ошибка: " : "") + msg;
    el.className = "badge" + (bad ? " err" : "");
    setTimeout(refresh, 1200);
  }

  var savedValues = {};

  function readField(el){
    var type = el.getAttribute("data-type");
    if (type === "bool") return el.checked;
    var v = el.value;
    if (type === "number") return v === "" ? "" : Number(v);
    return v;
  }

  function buildSettingsForm(schema, values){
    var box = $("settingsSections");
    box.innerHTML = "";
    savedValues = {};
    (schema.sections || []).forEach(function(section, idx){
      var det = document.createElement("details");
      if (idx === 0) det.open = true;
      var sum = document.createElement("summary");
      sum.textContent = (section.icon ? section.icon + " " : "") + (section.title || "Настройки");
      det.appendChild(sum);
      var grid = document.createElement("div");
      grid.className = "sgrid";
      (section.fields || []).forEach(function(field){
        var row = document.createElement("div");
        row.className = "fld";
        if (field.type === "path" || field.type === "text" || field.type === "textarea" || field.type === "secret") {
          row.className += " wide";
        }
        row.setAttribute("data-label", String(field.label || field.name).toLowerCase());
        var lab = document.createElement("span");
        lab.textContent = field.label || field.name;
        row.appendChild(lab);
        var val = values[field.name];
        var el;
        if (field.type === "bool") {
          var wrap = document.createElement("label");
          wrap.className = "sw";
          el = document.createElement("input");
          el.type = "checkbox";
          el.checked = !!val;
          var txt = document.createElement("span");
          txt.textContent = "включено";
          wrap.appendChild(el);
          wrap.appendChild(txt);
          row.appendChild(wrap);
        } else if (field.type === "select") {
          el = document.createElement("select");
          var opts = (field.options || []).slice();
          if (val !== undefined && val !== null && val !== "" && opts.indexOf(String(val)) < 0) opts.push(String(val));
          opts.forEach(function(opt){
            var o = document.createElement("option");
            o.value = opt; o.textContent = opt;
            el.appendChild(o);
          });
          el.value = (val === undefined || val === null) ? "" : String(val);
          row.appendChild(el);
        } else if (field.type === "textarea" || field.type === "secret") {
          el = document.createElement("textarea");
          el.value = (val === undefined || val === null) ? "" : String(val);
          if (field.type === "secret") el.placeholder = "*** = не менять";
          row.appendChild(el);
        } else if (field.type === "number") {
          el = document.createElement("input");
          el.type = "number"; el.step = "any";
          el.value = (val === undefined || val === null) ? "" : String(val);
          row.appendChild(el);
        } else {
          el = document.createElement("input");
          el.type = "text";
          el.value = (val === undefined || val === null) ? "" : String(val);
          row.appendChild(el);
        }
        el.setAttribute("data-field", field.name);
        el.setAttribute("data-type", field.type);
        savedValues[field.name] = readField(el);
        el.addEventListener("input", updateDirty);
        el.addEventListener("change", updateDirty);
        grid.appendChild(row);
      });
      det.appendChild(grid);
      box.appendChild(det);
    });
    updateDirty();
    applySettingsFilter();
  }

  function updateDirty(){
    var count = 0;
    var els = document.querySelectorAll("#settingsSections [data-field]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var name = el.getAttribute("data-field");
      var type = el.getAttribute("data-type");
      var val = readField(el);
      var changed;
      if (type === "secret" && (val === "***" || val === "")) changed = false;
      else changed = JSON.stringify(val) !== JSON.stringify(savedValues[name]);
      var row = el.closest(".fld");
      if (row) { if (changed) row.classList.add("dirty"); else row.classList.remove("dirty"); }
      if (changed) count++;
    }
    var btn = $("btnSaveSettings");
    if (btn) btn.textContent = count ? "Сохранить (" + count + ")" : "Сохранить настройки";
    return count;
  }

  function applySettingsFilter(){
    var input = $("settingsSearch");
    var q = (input ? input.value : "").trim().toLowerCase();
    var sections = document.querySelectorAll("#settingsSections > details");
    for (var s = 0; s < sections.length; s++) {
      var rows = sections[s].querySelectorAll(".fld");
      var visible = 0;
      for (var r = 0; r < rows.length; r++) {
        var label = rows[r].getAttribute("data-label") || "";
        var show = !q || label.indexOf(q) >= 0;
        rows[r].classList.toggle("hide", !show);
        if (show) visible++;
      }
      sections[s].classList.toggle("hide", !!q && visible === 0);
    }
  }

  function loadSettings(){
    return Promise.all([api("/api/settings/schema"), api("/api/settings")]).then(function(res){
      buildSettingsForm(res[0].schema || {}, res[1].settings || {});
      $("settingsMsg").textContent = "";
    });
  }

  function saveSettings(){
    var changed = {};
    var els = document.querySelectorAll("#settingsSections [data-field]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var name = el.getAttribute("data-field");
      var type = el.getAttribute("data-type");
      var val = readField(el);
      if (type === "secret" && (val === "***" || val === "")) continue;
      if (type === "number" && val === "") continue;
      if (JSON.stringify(val) === JSON.stringify(savedValues[name])) continue;
      changed[name] = val;
    }
    var names = Object.keys(changed);
    if (!names.length) { $("settingsMsg").textContent = "изменений нет"; return Promise.resolve(); }
    return api("/api/settings", {method: "POST", body: changed, timeout: 60000}).then(function(res){
      var applied = (res && res.applied) ? res.applied.length : names.length;
      $("settingsMsg").textContent = "сохранено полей: " + applied + " — применится к следующему запуску";
      return loadSettings();
    });
  }

  function refresh(){
    api("/api/status?log_tail=0").then(renderStatus).catch(function(e){ toast(e.message, true); });
    api("/api/queue").then(renderQueue).catch(function(){});
    api("/api/log?tail=120").then(renderLog).catch(function(){});
  }

  document.addEventListener("click", function(ev){
    var el = ev.target.closest("[data-act]");
    if (!el || busy) return;
    var act = el.getAttribute("data-act");
    busy = true;
    var done = function(){ busy = false; refresh(); };
    var fail = function(e){ busy = false; toast(e.message, true); };

    if (act === "start") {
      if (!confirm("Запустить создание видео с текущими настройками?")) { busy = false; return; }
      api("/api/run", {method: "POST", body: {series: $("runSeries").value.trim()}, timeout: 180000}).then(done).catch(fail);
    } else if (act === "cancel") {
      if (!confirm("Отменить текущий прогон?")) { busy = false; return; }
      api("/api/cancel", {method: "POST", body: {}, timeout: 60000}).then(done).catch(fail);
    } else if (act === "add") {
      var body = {audio: $("qAudio").value.trim(), output: $("qOutput").value.trim(), series: $("qSeries").value.trim()};
      if (!body.audio) { toast("укажите аудио", true); busy = false; return; }
      api("/api/queue", {method: "POST", body: body}).then(function(){ $("qAudio").value=""; $("qOutput").value=""; $("qSeries").value=""; done(); }).catch(fail);
    } else if (act === "pause") {
      var paused = $("btnPause").textContent === "Пауза";
      api("/api/queue/pause", {method: "POST", body: {paused: paused}}).then(done).catch(fail);
    } else if (act === "clear") {
      api("/api/queue/clear", {method: "POST", body: {}}).then(done).catch(fail);
    } else if (act === "remove") {
      api("/api/queue/" + el.getAttribute("data-id"), {method: "DELETE"}).then(done).catch(fail);
    } else if (act === "retry") {
      api("/api/queue/" + el.getAttribute("data-id") + "/retry", {method: "POST", body: {}}).then(done).catch(fail);
    } else if (act === "saveSettings") {
      saveSettings().then(done).catch(fail);
    } else if (act === "reloadSettings") {
      loadSettings().then(done).catch(fail);
    } else { busy = false; return; }
  });

  if (!KEY) {
    $("activity").textContent = "нет токена: откройте ссылку из блока «Пульт» целиком (в ней ?key=…)";
    $("state").textContent = "нет доступа";
    $("state").className = "badge err";
  } else {
    var settingsSearch = $("settingsSearch");
    if (settingsSearch) settingsSearch.addEventListener("input", applySettingsFilter);
    refresh();
    setInterval(refresh, 2000);
    loadSettings().catch(function(e){ $("settingsMsg").textContent = e.message; });
  }
})();
</script>
</body>
</html>
"""


def render(embed: bool = False) -> str:
    """Страница пульта. embed=True — урезанный вид для встраивания."""
    if not embed:
        return _HTML
    return (_HTML
            # Keep the state node in the DOM: JavaScript still updates it in
            # embedded mode, while the visible page heading is hidden.
            .replace('<h1 id="pageTitle">', '<h1 id="pageTitle" class="hide">')
            .replace('padding:clamp(10px,2.2vw,24px);max-width:1100px;min-width:0;',
                     'padding:8px;max-width:100%;min-width:0;'))
