"""Feuille de style et script du tableau de bord : servis localement, aucune ressource externe (EX-UI-04)."""

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#5b6570;--border:#d9dee5;--accent:#2457d6;--ok:#17803d;--warn:#a15c00;--crit:#b4231b;
--paper:#2563eb;--testnet:#b45309;--live:#b91c1c;--unknown:#6b7280;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1318;--card:#171c23;--text:#e7ebf0;--muted:#9aa5b1;--border:#2a323c;
--accent:#7aa2ff;--ok:#4ade80;--warn:#fbbf24;--crit:#f87171;--chip:#222a33;--paper:#3b82f6;--testnet:#d97706;--live:#dc2626}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--border)}
.banner{padding:.4rem 1rem;color:#fff;font-weight:700;letter-spacing:.02em;display:flex;gap:1rem;flex-wrap:wrap;align-items:center}
.banner.paper{background:var(--paper)}.banner.testnet{background:var(--testnet)}.banner.live{background:var(--live)}.banner.unknown{background:var(--unknown)}
.chips{display:flex;gap:.5rem;flex-wrap:wrap;margin-left:auto;font-weight:600;font-size:.85rem}
.chip{background:rgba(255,255,255,.18);padding:.1rem .6rem;border-radius:999px}
nav{display:flex;gap:.25rem;overflow-x:auto;padding:.4rem .75rem}nav a{padding:.35rem .75rem;border-radius:8px;text-decoration:none;color:var(--muted);white-space:nowrap}
nav a.active{background:var(--chip);color:var(--text);font-weight:700}main{max-width:1100px;margin:0 auto;padding:1rem 1rem 3rem}
h1{font-size:1.35rem;margin:.5rem 0 1rem}h2{font-size:1.05rem;margin:1.5rem 0 .5rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.75rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:.85rem 1rem}
.card .label{color:var(--muted);font-size:.8rem}.card .value{font-size:1.5rem;font-weight:700;margin-top:.1rem}.card .sub{color:var(--muted);font-size:.8rem}
.badge{display:inline-block;padding:.05rem .55rem;border-radius:999px;font-size:.78rem;font-weight:700;border:1px solid currentColor}
.badge.ok{color:var(--ok)}.badge.warn{color:var(--warn)}.badge.crit{color:var(--crit)}.badge.muted{color:var(--muted)}
.table-wrap{overflow-x:auto;background:var(--card);border:1px solid var(--border);border-radius:12px}
table{border-collapse:collapse;width:100%;min-width:560px}th,td{text-align:left;padding:.5rem .75rem;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.msg{background:var(--chip);border-left:4px solid var(--accent);padding:.6rem .9rem;border-radius:8px;margin-bottom:1rem}
form.inline{display:inline}button,.btn{font:inherit;border:1px solid var(--border);background:var(--card);color:var(--text);padding:.3rem .8rem;border-radius:8px;cursor:pointer}
button.danger{background:var(--crit);border-color:var(--crit);color:#fff;font-weight:700}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
input[type=text],input[type=password],textarea{font:inherit;width:100%;padding:.5rem .7rem;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)}
.danger-zone{border:1px solid var(--crit);border-radius:12px;padding:1rem;margin-top:1.5rem}.danger-zone h2{margin-top:0;color:var(--crit)}
svg.chart{width:100%;height:auto;background:var(--card);border:1px solid var(--border);border-radius:12px}
svg .l0{stroke:var(--accent);fill:none;stroke-width:2.2}svg .l1{stroke:var(--muted);fill:none;stroke-width:1.6;stroke-dasharray:5 4}svg text{fill:var(--muted);font-size:11px}
.legend{display:flex;gap:1rem;flex-wrap:wrap;color:var(--muted);font-size:.85rem;margin:.4rem 0}.muted{color:var(--muted)}.small{font-size:.85rem}
.mt{margin-top:2rem;display:inline-block}.narrow{max-width:12rem}.login{max-width:360px;margin:12vh auto;padding:0 1rem}pre{white-space:pre-wrap;word-break:break-word;margin:0;font:inherit}
@media (max-width:560px){main{padding:.75rem .75rem 3rem}.card .value{font-size:1.25rem}}
"""

JS = """
(function () {
  var badge = document.getElementById('hb');
  function refresh() {
    fetch('/api/status', {credentials: 'same-origin', cache: 'no-store'}).then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
      if (!s || !badge) { return; }
      var age = s.tick_age;
      badge.textContent = 'Dernier tick : ' + (age === null || age === undefined ? 'inconnu' : age + ' s');
    }).catch(function () {});
  }
  setInterval(refresh, 10000);
  if (document.body.dataset.autorefresh) { setInterval(function () { location.reload(); }, 30000); }
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) { if (!window.confirm(f.dataset.confirm)) { e.preventDefault(); } });
  });
})();
"""
