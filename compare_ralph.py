"""Run the same real fixture with two explicit fake-agent transports. No AI calls."""
import html
import json
import uuid
from pathlib import Path

from supervisor.engine import Supervisor, initialize
from supervisor.reports import render
from supervisor.safety import git


def main():
    root = Path(__file__).resolve().parent
    results = {'model_calls': 0, 'fixture': 'synthetic summation; fake agents, real validation', 'backends': {}}
    for backend in ('direct', 'ralph'):
        runtime = root / ('runtime-comparison-' + backend + '-' + uuid.uuid4().hex[:8])
        initialize(runtime, execution_backend=backend)
        supervisor = Supervisor(runtime)
        try:
            supervisor.control('running')
            cycles = [supervisor.cycle() for _ in range(5)]
            assert [c['status'] for c in cycles] == ['rejected', 'accepted_in_snapshot'] + ['rejected'] * 3
            assert cycles[0]['reason'] == 'independent_validation_failed'
            assert supervisor.store.get('control')['mode'] == 'auto_paused'
            for bundle in (runtime / 'backups').glob('*.bundle'):
                git(supervisor.repo, 'bundle', 'verify', str(bundle))
            report = render(supervisor)
            accepted = cycles[1]
            results['backends'][backend] = {
                'runtime': str(runtime), 'report': str(report),
                'statuses': [c['status'] for c in cycles],
                'before_ms': accepted['before_measurement']['median'],
                'after_ms': accepted['after_measurement']['median'],
                'improvement_percent': accepted['improvement_percent'],
                'cycle_elapsed_seconds': [c['elapsed_seconds'] for c in cycles],
                'control': supervisor.store.get('control'), 'bundles_verified': 3,
            }
        finally:
            supervisor.close()
    (root / 'ralph-comparison-results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    rows = ''.join('<tr><td>' + backend + '</td><td>' + f"{data['before_ms']:.3f}" +
                   '</td><td>' + f"{data['after_ms']:.3f}" + '</td><td>' +
                   f"{data['improvement_percent']:.3f}%" + '</td></tr>'
                   for backend, data in results['backends'].items())
    page = ('<!doctype html><html lang="fr"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Comparaison Ralph / superviseur</title>'
            '<style>body{font:17px system-ui;max-width:850px;margin:auto;padding:20px}'
            'td,th{padding:10px;border-bottom:1px solid #aaa}table{width:100%}</style>'
            '<h1>Comparaison des deux exécutants</h1><p>Fixture synthétique. Agents simulés ; '
            'tests, mesures et sauvegardes Git réels. Aucun appel IA.</p>'
            '<table><tr><th>Exécutant</th><th>Avant (ms)</th><th>Après (ms)</th><th>Gain</th></tr>' +
            rows + '</table><p>Les deux rejettent le changement incorrect, acceptent '
            'l’alternative puis cherchent encore et se mettent en pause après trois cycles sans progrès.</p>'
            '<p>Ces mesures ne comparent pas la qualité de deux IA. Elles ne prouvent pas que Ralph '
            'produit de meilleures améliorations. Les temps de cycle incluent Git et les tests.</p>'
            '<details><summary>Résultats complets</summary><pre style="overflow:auto">' +
            html.escape(json.dumps(results, indent=2)) + '</pre></details></html>')
    (root / 'ralph-comparison.html').write_text(page, encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
