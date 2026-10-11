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
    --bg:#0f1115; --card:#181b22; --card2:#1f232c; --line:#2a2f3a;
    --fg:#e8eaf0; --dim:#8b93a7; --acc:#4c8dff; --ok:#2ecc71; --warn:#f0b429; --err:#ff5c5c;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       padding:14px;max-width:760px;margin:0 auto;-webkit-text-size-adjust:100%}
  h1{font-size:17px;margin:0 0 12px;display:flex;align-items:center;gap:8px;font-weight:600}
  .badge{font-size:12px;font-weight:600;padding:3px 9px;border-radius:99px;background:var(--card2);color:var(--dim)}
  .badge.run{background:rgba(76,141,255,.18);color:#9cc0ff}
  .badge.err{background:rgba(255,92,92,.18);color:#ffb3b3}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
  .row{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
  .muted{color:var(--dim);font-size:13px}
  .big{font-size:30px;font-weight:700;letter-spacing:-.5px}
  .bar{height:10px;background:var(--card2);border-radius:99px;overflow:hidden;margin:10px 0 6px}
  .bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--acc),#8f6cff);transition:width .4s}
  .stage{font-weight:600;margin-top:6px}
  .act{font-size:12.5px;color:var(--dim);margin-top:6px;word-break:break-word;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace;max-height:52px;overflow:hidden}
  button{font:inherit;font-weight:600;border:0;border-radius:11px;padding:12px 16px;color:#fff;background:var(--acc);
         cursor:pointer;flex:1}
  button:active{transform:scale(.98)}
  button.ghost{background:var(--card2);color:var(--fg)}
  button.danger{background:#3a2226;color:#ffb3b3}
  button:disabled{opacity:.45;cursor:not-allowed}
  .btns{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .btns button{min-width:120px}
  input{font:inherit;background:var(--card2);border:1px solid var(--line);color:var(--fg);
        border-radius:10px;padding:11px 12px;width:100%;margin-bottom:8px}
  ul{list-style:none;margin:0;padding:0}
  li{display:flex;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line);font-size:14px}
  li:last-child{border-bottom:0}
  .dot{width:9px;height:9px;border-radius:99px;background:var(--dim);flex:0 0 auto}
  .dot.pending{background:var(--warn)} .dot.running{background:var(--acc)}
  .dot.done{background:var(--ok)} .dot.failed{background:var(--err)} .dot.canceled{background:var(--dim)}
  .grow{flex:1;min-width:0}
  .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mini{font-size:12px;color:var(--dim)}
  .x{background:none;border:0;color:var(--dim);font-size:17px;padding:4px 8px;flex:0 0 auto;cursor:pointer}
  pre{background:#0b0d11;border:1px solid var(--line);border-radius:10px;padding:10px;margin:0;
      font-size:11.5px;line-height:1.5;max-height:260px;overflow:auto;white-space:pre-wrap;word-break:break-word}
  details summary{cursor:pointer;color:var(--dim);font-size:13px;outline:none}
  .hide{display:none}
</style>
</head>
<body>
<h1>VideoMaker <span id="state" class="badge">…</span></h1>

<div class="card" id="statusCard">
  <div class="row"><span class="muted" id="series">—</span><span class="big" id="pct">0%</span></div>
  <div class="bar"><i id="bar"></i></div>
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
    <button data-act="clear" class="ghost">Убрать завершённые</button>
  </div>
  <div style="margin-top:12px">
    <input id="qAudio" placeholder="Аудио: файл или папка">
    <input id="qOutput" placeholder="Папка вывода (пусто = как в настройках)">
    <input id="qSeries" placeholder="Название серии (необязательно)">
    <button data-act="add" class="ghost" style="width:100%">Добавить в очередь</button>
  </div>
  <ul id="queue"></ul>
</div>

<div class="card">
  <details open><summary>Лог</summary><pre id="log">…</pre></details>
</div>

<script>
(function(){
  "use strict";
  var KEY = new URLSearchParams(location.search).get("key") || "";
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
    var sep = path.indexOf("?") >= 0 ? "&" : "?";
    var url = path + sep + "key=" + encodeURIComponent(KEY);
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
    $("state").textContent = running ? "работает" : "свободен";
    $("state").className = "badge" + (running ? " run" : "");
    $("pct").textContent = (st.progress||0).toFixed(0) + "%";
    $("bar").style.width = Math.max(0, Math.min(100, st.progress||0)) + "%";
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
        rb.setAttribute("data-act", "retry"); rb.setAttribute("data-id", it.id);
        li.appendChild(rb);
      }
      if (it.status !== "running") {
        var xb = document.createElement("button");
        xb.className = "x"; xb.textContent = "×"; xb.title = "удалить";
        xb.setAttribute("data-act", "remove"); xb.setAttribute("data-id", it.id);
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
            .replace("<h1>VideoMaker <span id=\"state\" class=\"badge\">…</span></h1>", "")
            .replace('padding:14px;max-width:760px', 'padding:8px;max-width:100%'))
