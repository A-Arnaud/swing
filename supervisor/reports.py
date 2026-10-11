import html
import json
import os
import difflib
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .objectives import current

_render_lock = threading.Lock()

def render(supervisor):
    with _render_lock:
        return _render(supervisor)


def _render(supervisor):
    root, store = supervisor.root, supervisor.store
    directory = root / "reports"
    directory.mkdir(exist_ok=True)
    summary = {"updated_at": datetime.now(timezone.utc).isoformat(),
               "control": store.get("control"), "mode": supervisor.config["mode"],
               "model": supervisor.config.get("model"),
               "reasoning_effort": supervisor.config.get("reasoning_effort", "medium"),
               "chatgpt_quota": store.get("chatgpt_quota"),
               "conversation_task": store.get("conversation_task"),
               "phase": store.get("phase", "En attente"),
               "memory_status": store.get("memory_status"),
               "objective": current(store),
               "next_cycle_at": store.get("next_cycle_at"),
               "model_calls": store.db.execute("SELECT count(*) FROM calls").fetchone()[0],
               "production_promotion": "automatic_after_checks" if supervisor.config.get("auto_promote_source") else "manual_only_not_implemented",
               "no_progress_cycles": store.get("no_progress", 0),
               "cycles": store.history(30), "proposals": store.proposals()}
    data = json.dumps(summary, ensure_ascii=False, indent=2)
    temporary = directory / f"summary.{os.getpid()}.{uuid.uuid4().hex}.tmp"
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
    live_code = ""
    active = next((cycle for cycle in summary["cycles"] if cycle.get("status") == "running"), None)
    live_running = active is not None
    if active is None:
        active = next((cycle for cycle in summary["cycles"]
                       if cycle.get("status") == "accepted_in_snapshot"
                       and (cycle.get("promotion") or {}).get("status") == "applied"), None)
    if active:
        if live_running:
            worktree = Path(active.get("worktree", ""))
            base_tree = supervisor.repo
            caption = "Code en cours — diff de la copie de travail Codex"
        else:
            worktree = Path((active.get("promotion") or {}).get("backup", ""))
            base_tree = worktree
            worktree = Path(supervisor.store.get("source"))
            caption = "Dernier code accepté — appliqué au projet"
        allowed = set(supervisor.config.get("allowed_files", []))
        sections = []
        if worktree.is_dir():
            for name in (active.get("proposal") or {}).get("files", []):
                if name not in allowed and name not in active.get('new_files', []):
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_./-]{1,180}", name):
                    continue
                old, new = base_tree / name, worktree / name
                try:
                    if live_running:
                        old.resolve().relative_to(supervisor.repo.resolve())
                    else:
                        old.resolve().relative_to((supervisor.root / "backups").resolve())
                    new.resolve().relative_to(worktree.resolve())
                    if not new.is_file() or old.is_symlink() or new.is_symlink():
                        continue
                    diff = list(difflib.unified_diff(old.read_text(encoding="utf-8").splitlines() if old.exists() else [],
                                                    new.read_text(encoding="utf-8").splitlines(),
                                                    fromfile="validated/" + name,
                                                    tofile="Codex-worktree/" + name, lineterm="", n=2))
                    if diff:
                        sections.append("\n".join(diff[:600]))
                except (OSError, UnicodeError, ValueError):
                    continue
        diff_text = "\n\n".join(sections) if sections else "Codex analyse la proposition. Les premières modifications apparaîtront ici."
        live_code = (f"<details open><summary>{escape(caption)}</summary>"
                     f"<pre>{escape(diff_text[:100000])}</pre></details>")
    page = f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Superviseur — rapport</title><style>
body{{font:16px system-ui;background:#101827;color:#e8eef7;margin:0;padding:20px}}
main{{max-width:850px;margin:auto}}article,.state{{padding:18px;background:#1c2a40;border-radius:12px;margin:14px 0}}
h1{{font-size:26px}}h2{{font-size:19px}}small,pre{{overflow-wrap:anywhere;white-space:pre-wrap}}
p{{line-height:1.5}}details{{margin-top:20px}}a{{color:#b6d8ff}}
</style><main><h1>Amélioration continue — prototype</h1>
<div class="state"><p>Mode : {escape(summary['mode'])} · État : {escape(summary['control'])}</p>
<p>Modèle : {escape(summary['model'])} · Raisonnement : {escape(summary['reasoning_effort'])} · Appels IA : {summary['model_calls']}</p>
<p>Activité : {escape(summary['phase'])}</p>
<p>Quota ChatGPT : {escape(summary['chatgpt_quota'] if summary['chatgpt_quota'] else 'Lecture indisponible')}</p>
<p>Approbations demand?es : {len(approvals)} ? ?checs cons?cutifs : {summary['no_progress_cycles']} (retour ? z?ro apr?s un succ?s)</p>
<p>{'Changements validés appliqués automatiquement au projet, avec sauvegarde.' if supervisor.config.get('auto_promote_source') else 'Les changements acceptés restent dans la copie Git.'}</p>
<small>Actualisé : {escape(summary['updated_at'])}. Rechargement toutes les 5 secondes.</small></div>
{live_code}
{''.join(cards) or '<p>Aucun cycle exécuté.</p>'}
<details><summary>État et preuves détaillés</summary><pre>{escape(data)}</pre></details>
</main></html>"""
    temporary = directory / f"index.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(page, encoding="utf-8")
    os.replace(temporary, directory / "index.html")
    return directory / "index.html"
