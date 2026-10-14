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
    --bg:#0c1018; --card:#151c29; --card2:#222d40; --line:#3a4860;
    --fg:#f1f5fc; --dim:#b0bdd1; --acc:#245dc7; --accHover:#2f70e6;
    --ok:#24a968; --warn:#f3c24f; --err:#ff7777; --focus:#9ac4ff;
  }
  *{box-sizing:border-box}
  html{min-width:0;scroll-behavior:smooth}
  body{margin:0 auto;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       padding:clamp(10px,2.2vw,24px);max-width:1100px;min-width:0;-webkit-text-size-adjust:100%}
  h1{font-size:19px;margin:0 0 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-weight:700;letter-spacing:-.2px}
  .badge{font-size:12px;font-weight:700;padding:4px 10px;border-radius:99px;background:var(--card2);color:#e0e8f5;border:1px solid #48566d}
  .badge.run{background:#17345d;color:#b9d6ff;border-color:#3769a8}
  .badge.err{background:#542b34;color:#ffd0d0;border-color:#97505b}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:clamp(12px,1.7vw,18px);margin:0 0 14px;min-width:0;box-shadow:0 8px 22px rgba(0,0,0,.14)}
  .row{display:flex;justify-content:space-between;align-items:baseline;gap:10px;min-width:0}
  .muted{color:var(--dim);font-size:13px}
  .big{font-size:clamp(27px,3vw,34px);font-weight:750;letter-spacing:-.7px;font-variant-numeric:tabular-nums}
  .bar{height:11px;background:#29364b;border:1px solid #41516a;border-radius:99px;overflow:hidden;margin:10px 0 8px}
  .bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,#4e92ff,#a28aff);transition:width .25s ease}
  .stage{font-weight:700;margin-top:8px;overflow-wrap:anywhere}
  .act{font-size:12.5px;color:#c0cce0;margin-top:7px;word-break:break-word;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace;max-height:76px;overflow:auto}
  button{font:inherit;font-weight:650;border:1px solid transparent;border-radius:11px;padding:11px 16px;min-height:44px;color:#fff;background:var(--acc);
         cursor:pointer;flex:1;touch-action:manipulation;transition:background-color .12s ease,border-color .12s ease,transform .08s ease}
  button:hover:not(:disabled){background:var(--accHover)}
  button:active:not(:disabled){transform:translateY(1px)}
  button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
  button.ghost{background:#2a3548;color:#f1f5fc;border-color:#48566d}
  button.ghost:hover:not(:disabled){background:#35445d;border-color:#7285a4}
  button.danger{background:#512832;color:#ffe2e4;border-color:#8d4652}
  button.danger:hover:not(:disabled){background:#6a2f3c}
  button:disabled{background:#252d3b;color:#b0bdd1;border-color:#3a4659;opacity:1;cursor:not-allowed;filter:saturate(.55)}
  .btns{display:flex;gap:9px;margin-top:12px;flex-wrap:wrap;min-width:0}
  .btns button{min-width:132px}
  input{font:inherit;background:#111a28;border:1px solid #50617b;color:var(--fg);
        border-radius:10px;padding:11px 12px;min-height:44px;width:100%;min-width:0;margin-bottom:9px}
  input::placeholder{color:#a8b6cc;opacity:1}
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
  .x:hover:not(:disabled){background:#3a4963;border-color:#8193af}
  pre{background:#090e16;border:1px solid #3b4a62;border-radius:10px;padding:12px;margin:8px 0 0;
      font-size:12px;line-height:1.55;max-height:clamp(200px,38vh,360px);overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
  details summary{cursor:pointer;color:#cfdaeb;font-size:14px;font-weight:650;min-height:34px;display:flex;align-items:center;outline:none}
  .sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
  .hide{display:none}
  @media(min-width:920px){
    body{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:16px;align-content:start}
    h1{grid-column:1/-1}
    #statusCard{grid-column:1;grid-row:2}
    body>.card:nth-of-type(2){grid-column:2;grid-row:2}
    body>.card:nth-of-type(3){grid-column:1/-1;grid-row:3}
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
  }
  @media(prefers-reduced-motion:reduce){
    *,*::before,*::after{scroll-behavior:auto!important;transition-duration:.01ms!important;animation-duration:.01ms!important;animation-iteration-count:1!important}
  }
</style>
</head>
<body>
<h1 id="pageTitle">VideoMaker <span id="state" class="badge" role="status" aria-live="polite" aria-atomic="true">…</span></h1>

<div class="card" id="statusCard">
  <div class="row"><span class="muted" id="series">—</span><span class="big" id="pct">0%</span></div>
  <div class="bar" id="progressBar" role="progressbar" aria-label="Прогресс создания видео" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i id="bar"></i></div>
  <div class="stage" id="stage">ожидание</div>
  <div class="muted" id="times">—</div>
  <div class="act" id="activity"></div>
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

<div class="card">
  <details open><summary>Журнал обработки</summary><pre id="log" role="log" aria-label="Журнал обработки" aria-live="off">…</pre></details>
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
    $("bar").style.width = pct + "%";
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
      api("/api/run", {method: "POST", body: {}, timeout: 180000}).then(done).catch(fail);
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
    } else { busy = false; return; }
  });

  if (!KEY) {
    $("activity").textContent = "нет токена: откройте ссылку из блока «Пульт» целиком (в ней ?key=…)";
    $("state").textContent = "нет доступа";
    $("state").className = "badge err";
  } else {
    refresh();
    setInterval(refresh, 2000);
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
