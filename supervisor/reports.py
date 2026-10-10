import html
import json
import os
from datetime import datetime, timezone


def render(supervisor):
    root, store = supervisor.root, supervisor.store
    directory = root / "reports"
    directory.mkdir(exist_ok=True)
    summary = {"updated_at": datetime.now(timezone.utc).isoformat(),
               "control": store.get("control"), "mode": supervisor.config["mode"],
               "model": supervisor.config.get("model"),
               "reasoning_effort": supervisor.config.get("reasoning_effort", "medium"),
               "chatgpt_quota": store.get("chatgpt_quota"),
               "model_calls": store.db.execute("SELECT count(*) FROM calls").fetchone()[0],
               "production_promotion": "manual_only_not_implemented",
               "no_progress_cycles": store.get("no_progress", 0),
               "cycles": store.history(30), "proposals": store.proposals()}
    data = json.dumps(summary, ensure_ascii=False, indent=2)
    temporary = directory / f"summary.{os.getpid()}.tmp"
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, directory / "summary.json")
    def escape(value):
        return html.escape(str(value), quote=True)
    cards = []
    for cycle in summary["cycles"]:
        before, after = cycle.get("before_measurement") or {}, cycle.get("after_measurement") or {}
        measure = "Mesure indisponible"
        if before and after:
            measure = f"{before['median']:.3f} → {after['median']:.3f} {before['metric']}"
        title = cycle.get("proposal", {}).get("strategy", "Recherche de nouvelles pistes")
        cards.append(f"<article><h2>{escape(title)}</h2><p>{escape(cycle['status'])}</p>"
                     f"<p>{escape(cycle.get('reason', 'Critères satisfaits dans la copie isolée'))}</p>"
                     f"<p>{escape(measure)}</p><small>{escape(cycle['id'])}</small></article>")
    approvals = [p for p in summary["proposals"] if p["status"] == "awaiting_approval"]
    page = f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Superviseur — rapport</title><style>
body{{font:16px system-ui;background:#101827;color:#e8eef7;margin:0;padding:20px}}
main{{max-width:850px;margin:auto}}article,.state{{padding:18px;background:#1c2a40;border-radius:12px;margin:14px 0}}
h1{{font-size:26px}}h2{{font-size:19px}}small,pre{{overflow-wrap:anywhere;white-space:pre-wrap}}
p{{line-height:1.5}}details{{margin-top:20px}}a{{color:#b6d8ff}}
</style><main><h1>Amélioration continue — prototype</h1>
<div class="state"><p>Mode : {escape(summary['mode'])} · État : {escape(summary['control'])}</p>
<p>Modèle : {escape(summary['model'])} · Raisonnement : {escape(summary['reasoning_effort'])} · Appels IA : {summary['model_calls']}</p>
<p>Quota ChatGPT : {escape(summary['chatgpt_quota'] if summary['chatgpt_quota'] else 'Lecture indisponible')}</p>
<p>Validations en attente : {len(approvals)} · Cycles sans progrès : {summary['no_progress_cycles']}</p>
<p>Projet principal conservé. Les changements acceptés restent dans la copie Git isolée.</p>
<small>Actualisé : {escape(summary['updated_at'])}. Rapport statique : recharger pour actualiser.</small></div>
{''.join(cards) or '<p>Aucun cycle exécuté.</p>'}
<details><summary>État et preuves détaillés</summary><pre>{escape(data)}</pre></details>
</main></html>"""
    temporary = directory / f"index.{os.getpid()}.tmp"
    temporary.write_text(page, encoding="utf-8")
    os.replace(temporary, directory / "index.html")
    return directory / "index.html"
