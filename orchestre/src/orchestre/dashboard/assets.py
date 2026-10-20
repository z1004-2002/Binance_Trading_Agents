"""Feuille de style et script du tableau de bord : servis localement, aucune ressource externe (EX-UI-04)."""

CSS = """
:root{--bg:#f4f6fb;--card:#fff;--text:#1c2433;--muted:#667085;--border:#e3e8f0;--accent:#3b5bdb;--accent-soft:#e8edff;--ok:#12805c;--ok-soft:#e3f6ee;
--warn:#a35d00;--warn-soft:#fff3dc;--crit:#c0291f;--crit-soft:#fde8e6;--paper:#3b5bdb;--testnet:#b45309;--live:#c0291f;--unknown:#667085;--chip:#eef1f7;--side:#fff;--shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.08)}
*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:240px 1fr;min-height:100vh}
.sidebar{background:var(--side);border-right:1px solid var(--border);padding:1rem .75rem;position:sticky;top:0;height:100vh;overflow-y:auto}
.brand{font-weight:800;font-size:1.15rem;letter-spacing:.04em;padding:.25rem .6rem 1rem;color:var(--accent)}
.brand small{display:block;color:var(--muted);font-weight:500;font-size:.72rem;letter-spacing:0}
.sidebar h3{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:1.1rem .6rem .3rem}
.sidebar nav a{display:flex;align-items:center;gap:.55rem;padding:.42rem .65rem;border-radius:8px;color:var(--text);text-decoration:none;font-size:.93rem}
.sidebar nav a:hover{background:var(--chip)}.sidebar nav a.active{background:var(--accent-soft);color:var(--accent);font-weight:700}
.sidebar .ico{width:1.2rem;text-align:center;opacity:.8}
.main{min-width:0}
.top{display:flex;gap:.75rem;align-items:center;flex-wrap:wrap;padding:.55rem 1.25rem;background:var(--card);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:5}
.menu{display:none;padding:.25rem .6rem;font-size:1.1rem}
.env{color:#fff;font-weight:700;letter-spacing:.02em;padding:.2rem .8rem;border-radius:999px;font-size:.85rem}
.env.paper{background:var(--paper)}.env.testnet{background:var(--testnet)}.env.live{background:var(--live)}.env.unknown{background:var(--unknown)}
.chips{display:flex;gap:.5rem;flex-wrap:wrap;margin-left:auto;font-size:.82rem}
.chip{background:var(--chip);color:var(--muted);padding:.12rem .65rem;border-radius:999px;font-weight:600}
.chip.warn{background:var(--warn-soft);color:var(--warn)}.chip.crit{background:var(--crit-soft);color:var(--crit)}.chip.ok{background:var(--ok-soft);color:var(--ok)}
main{max-width:1180px;margin:0 auto;padding:1.25rem 1.25rem 3rem}
h1{font-size:1.45rem;margin:.2rem 0 1.1rem}h2{font-size:1.02rem;margin:1.7rem 0 .6rem}
.sub-title{color:var(--muted);margin:-.8rem 0 1.1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:.85rem}
.grid.wide{grid-template-columns:repeat(auto-fill,minmax(310px,1fr))}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:.95rem 1.1rem;box-shadow:var(--shadow)}
.card .label{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}.card .value{font-size:1.55rem;font-weight:750;margin-top:.15rem}.card .sub{color:var(--muted);font-size:.8rem}
.agent-card{display:flex;flex-direction:column;gap:.45rem}.agent-card h3{margin:0;font-size:1rem}.agent-card .code{color:var(--muted);font-weight:600;font-size:.8rem}
.agent-card p{margin:0;color:var(--muted);font-size:.87rem}.agent-card .actions{margin-top:auto;display:flex;gap:.4rem;flex-wrap:wrap;padding-top:.5rem}
.agent-card.off{background:#fafbfd}.agent-card .meta{display:flex;gap:.9rem;font-size:.82rem;color:var(--muted);flex-wrap:wrap}
.badge{display:inline-block;padding:.08rem .6rem;border-radius:999px;font-size:.76rem;font-weight:700}
.badge.ok{background:var(--ok-soft);color:var(--ok)}.badge.warn{background:var(--warn-soft);color:var(--warn)}.badge.crit{background:var(--crit-soft);color:var(--crit)}.badge.muted{background:var(--chip);color:var(--muted)}
.table-wrap{overflow-x:auto;background:var(--card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;min-width:560px}th,td{text-align:left;padding:.55rem .8rem;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--muted);font-size:.74rem;text-transform:uppercase;letter-spacing:.05em;background:#fafbfd}tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.msg{background:var(--accent-soft);border-left:4px solid var(--accent);padding:.65rem .95rem;border-radius:8px;margin-bottom:1rem}
form.inline{display:inline}button,.btn{font:inherit;font-size:.85rem;border:1px solid var(--border);background:var(--card);color:var(--text);padding:.28rem .8rem;border-radius:8px;cursor:pointer;text-decoration:none;display:inline-block}
button:hover,.btn:hover{background:var(--chip);text-decoration:none}
button.danger{background:var(--crit);border-color:var(--crit);color:#fff;font-weight:700}button.primary,.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.soft-danger{color:var(--crit);border-color:#f3c2be}
input[type=text],input[type=password],textarea{font:inherit;width:100%;padding:.5rem .7rem;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--text)}
label{display:block;font-size:.82rem;color:var(--muted);margin:.6rem 0 .2rem}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.2rem .9rem}
.danger-zone{border:1px solid #f3c2be;background:#fff8f7;border-radius:14px;padding:1rem;margin-top:1.5rem}.danger-zone h2{margin-top:0;color:var(--crit)}
svg.chart{width:100%;height:auto;background:var(--card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}
svg .l0{stroke:var(--accent);fill:none;stroke-width:2.2}svg .l1{stroke:#98a2b3;fill:none;stroke-width:1.7;stroke-dasharray:5 4}
svg .l2{stroke:#0e9f6e;fill:none;stroke-width:2.2}svg .l3{stroke:#d97706;fill:none;stroke-width:1.7}svg text{fill:var(--muted);font-size:11px}
.legend{display:flex;gap:1rem;flex-wrap:wrap;color:var(--muted);font-size:.85rem;margin:.4rem 0}.muted{color:var(--muted)}.small{font-size:.85rem}
.mt{margin-top:2rem;display:inline-block}.narrow{max-width:12rem}.login{max-width:360px;margin:12vh auto;padding:0 1rem}pre{white-space:pre-wrap;word-break:break-word;margin:0;font:inherit}
.tabs{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:1rem}.tabs a{padding:.25rem .8rem;border-radius:999px;background:var(--chip);color:var(--muted);font-size:.85rem;text-decoration:none}
.tabs a.active{background:var(--accent);color:#fff}
.twin-gap.ok{color:var(--ok)}.twin-gap.warn{color:var(--warn)}.twin-gap.crit{color:var(--crit)}
@media (max-width:860px){.app{grid-template-columns:1fr}.sidebar{position:fixed;left:0;top:0;bottom:0;width:250px;z-index:20;transform:translateX(-105%);transition:transform .15s;box-shadow:var(--shadow)}
body.nav-open .sidebar{transform:none}.menu{display:inline-block}main{padding:1rem .85rem 3rem}.card .value{font-size:1.3rem}}
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
  var menu = document.getElementById('menu');
  if (menu) { menu.addEventListener('click', function () { document.body.classList.toggle('nav-open'); }); }
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) { if (!window.confirm(f.dataset.confirm)) { e.preventDefault(); } });
  });
})();
"""
