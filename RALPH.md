> Configuration active : voir [AUTONOMOUS.md](AUTONOMOUS.md). Les notes de pr?paration ci-dessous sont historiques.

# Intégration Ralph–Codex pour Windows

Le prototype contient désormais une **adaptation Python de Ralph**, utilisable
comme exécutant du superviseur existant. La copie de référence de
[snarktank/ralph](https://github.com/snarktank/ralph) est dans `vendor/ralph` :
script, prompt et licence MIT, à la révision
`6c53cb0b831ebe8739c6a003e22af14902d8b0b5`. Les empreintes et la méthode
d'import sont dans `PROVENANCE.json`. Le réseau empêchait un clonage Git ;
il s'agit d'un import des trois fichiers publics, pas du dépôt complet.

Le script Bash original reste une référence. L'exécution sous Windows passe
par `supervisor/ralph.py`, sans Bash, jq, Amp ni Claude. Le transport réel
est le `CodexExecutor` déjà présent : une nouvelle invocation `codex exec`
par tâche, avec les mêmes réservations de budget, délais, contrôles d'arrêt
et conditions d'activation. Aucun appel IA n'a été lancé pour cette intégration.

## Répartition des responsabilités

| Élément | Responsabilité |
| --- | --- |
| Superviseur | Recherche, classement impact/coût/risque, approbations, branches, budgets, tests, mesures, Git, rapports et pauses |
| Adaptation Ralph | Une story par itération, PRD, contexte neuf, progression vérifiée entre sessions |
| Codex | Implémentation dans le worktree autorisé |

Ralph reçoit le PRD et les résumés vérifiés dans le prompt. Ses fichiers d'état
sont conservés hors du worktree, sous `runtime.../ralph/<cycle>/` : `prd.json`,
`progress.json`, puis `result.json`. L'agent ne les modifie pas. `passes` reste
faux jusqu'à l'acceptation indépendante. Un signal `<promise>COMPLETE</promise>`
est une déclaration de l'agent ; il ne dispense jamais des contrôles.

Les commits, modifications des tests et changements de branche demandés dans
le prompt original ont été remplacés par les décisions du superviseur.
Les options amont qui élargissent les permissions ne sont pas reprises.
Le journal contient les résultats mesurés, pas des affirmations libres de
l'agent. Les tentatives rejetées restent archivées. Il n'y a pas de seconde
boucle de réessais cachée : chaque itération passe par la validation du
superviseur. Une story terminée relance la recherche de nouvelles pistes ;
une absence répétée de progrès déclenche les pauses déjà prévues.

## Utiliser la démonstration

Depuis le dossier du prototype :

```powershell
$pythonRadar = (Resolve-Path ../../.venv/Scripts/python.exe).Path
& $pythonRadar -m supervisor --runtime runtime-ralph-essai init --executor ralph
& $pythonRadar -m supervisor --runtime runtime-ralph-essai resume
& $pythonRadar -m supervisor --runtime runtime-ralph-essai run --max-cycles 5
& $pythonRadar -m supervisor --runtime runtime-ralph-essai report
& $pythonRadar -m supervisor --runtime runtime-ralph-essai pause
& $pythonRadar -m supervisor --runtime runtime-ralph-essai resume
& $pythonRadar -m supervisor --runtime runtime-ralph-essai stop
```

Utiliser un nouveau nom de runtime si celui-ci existe déjà. Cette commande
initialise une fixture synthétique et un faux transport IA explicite ; les
changements, processus, tests, mesures et sauvegardes sont réels.
La démo ne travaille pas sur Token Radar.

Pour créer une copie du projet avec cet exécutant, ajouter `--source ../..`
à `init --executor ralph`. Elle commence en pause, les appels IA sont désactivés,
les budgets sont nuls et tous les fichiers importés sont protégés. Ce n'est
pas une commande d'activation réelle. La checklist de préparation existante
dans README reste applicable : isolation vérifiée, budget choisi, périmètre
autorisé, commandes de validation et mesures adaptées au projet. L'ancienne
automatisation Codex ne doit pas concurrencer ce système lors d'une adoption.

## Comparer

```powershell
& $pythonRadar compare_ralph.py
& $pythonRadar -m unittest discover -s tests -v
```

`ralph-comparison.html` est un rapport autonome adapté au téléphone ; transférer
le fichier pour le consulter. Le JSON voisin contient les mesures et les
chemins des rapports détaillés. Chaque exécution crée deux runtimes distincts
sans supprimer les résultats précédents. La même fixture et les mêmes tests
indépendants sont utilisés pour les exécutants direct et Ralph.

Cette comparaison vérifie l'orchestration. Elle ne compare pas la qualité
d'implémentation de deux modèles et ne démontre pas une supériorité de Ralph.
Le critère actuel reste un gain mesurable de performance d'au moins 20 % ;
les améliorations fonctionnelles demandent encore des critères adaptés.
L'intégration réelle de Codex et le fonctionnement prolongé restent à valider.
Les worktrees et Job Objects ne constituent pas à eux seuls une frontière de
sécurité pour exécuter du code non fiable ; les limites Windows documentées
dans README restent valables.
