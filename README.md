# Superviseur d'amélioration continue — prototype Windows

Ce dossier est indépendant du code principal de Token Radar. Il contient un superviseur déterministe Python, un adaptateur Codex CLI, un exemple d'adaptateur SDK TypeScript, une démo et une copie de recherche Git sans données locales ni secrets. **Aucune session IA réelle ni promotion dans le projet principal n'est activée.**

Le dossier est placé sous `.local/supervisor-prototype` parce que cette session peut écrire uniquement à l'intérieur de Token Radar. Il reste exclu du code applicatif et pourra être déplacé dans un dossier voisin depuis votre session Windows habituelle. Aucun fichier applicatif, README/STATUS principal, configuration Codex, secret ou ancienne automatisation n'a été modifié pour créer ce prototype.

Voir [COMPARISON.md](COMPARISON.md) pour l'étude de Ralph Loop et du SDK officiel, [ENVIRONMENT.md](ENVIRONMENT.md) pour l'installation observée et [VALIDATION.md](VALIDATION.md) pour les essais et limites.

## Fonctionnement

L'exécutant Ralph–Codex est désormais disponible en option (`init --executor ralph`).
Voir [RALPH.md](RALPH.md) pour la copie amont versionnée, l'adaptation Windows,
les commandes et la comparaison. Le superviseur conserve ses contrôles indépendants.

Un processus superviseur conserve son état dans SQLite et démarre des exécutants séparés. Chaque cycle :

1. Relit les commandes de contrôle, les budgets, la référence Git validée et les preuves antérieures.
2. Vérifie que la copie source n'a pas changé, puis crée une branche et un worktree isolés.
3. Exécute le contrat de validation et les mesures de référence avec des commandes définies par l'opérateur.
4. Demande des pistes nouvelles si la file n'a aucune proposition admissible ; compare impact/coût et exige une approbation spécifique pour les risques moyens/élevés.
5. Confie une seule proposition à l'exécutant, dans un périmètre de fichiers autorisé.
6. Refuse les suppressions, secrets détectables, modifications du contrat de test/benchmark et changements hors périmètre.
7. Relance le contrat et le benchmark ; exige le gain configuré et vérifie de nouveau la source et la référence Git.
8. Sauvegarde la référence précédente en bundle Git et conserve la modification **dans la copie isolée** seulement si les contrôles réussissent.
9. Archive les preuves et produit un rapport HTML autonome. Les essais rejetés restent consultables dans leur worktree ; ils ne remplacent pas la référence validée.
10. Recommence la recherche, même si les tâches prévues sont terminées. Trois cycles sans progrès provoquent une pause d'une heure ; trois fenêtres de blocage conduisent à une pause exigeant de nouvelles preuves ou une intervention.

La démo utilise un faux agent explicite pour vérifier l'orchestration ; modifications, processus, tests, timings, Git et sauvegardes sont réels. Elle essaie deux stratégies : un raccourci incorrect rejeté par le contrat puis une sommation linéaire acceptée. Après optimisation, elle continue à chercher et s'abstient d'inventer un bénéfice.

## Essayer la démo

Depuis le dossier du prototype, utiliser Python 3.12+ ; ici `../../.venv/Scripts/python.exe` :

```powershell
$pythonRadar = (Resolve-Path ../../.venv/Scripts/python.exe).Path
# runtime-demo existe déjà avec les essais de cette session.
& $pythonRadar -m supervisor --runtime runtime-demo status
& $pythonRadar -m supervisor --runtime runtime-demo report

# Nouvelle démo, dossier inexistant au préalable.
& $pythonRadar -m supervisor --runtime runtime-demo-2 init
& $pythonRadar -m supervisor --runtime runtime-demo-2 resume
& $pythonRadar -m supervisor --runtime runtime-demo-2 once
& $pythonRadar -m supervisor --runtime runtime-demo-2 once

# Boucle prolongée, à lancer au premier plan dans une fenêtre distincte.
& $pythonRadar -m supervisor --runtime runtime-demo-2 run
```

La boucle n'est pas installée comme service ou tâche planifiée. Aucun lancement persistant n'a été laissé actif. Le superviseur se lance depuis votre terminal habituel ; il ne demande jamais aux sessions Codex exécutantes de se relancer elles-mêmes.

## Pause, reprise et arrêt

Dans une autre fenêtre PowerShell, avec le même dossier de runtime :

```powershell
& $pythonRadar -m supervisor --runtime runtime-demo-2 pause
& $pythonRadar -m supervisor --runtime runtime-demo-2 resume
& $pythonRadar -m supervisor --runtime runtime-demo-2 stop
```

Pause et arrêt sont persistants. Pendant une commande, le superviseur les vérifie environ toutes les 200 ms et interrompt le processus qu'il possède ; à l'attente il vérifie l'arrêt toutes les secondes. Windows Job Objects lient les descendants à la durée de vie de la commande ; si l'attachement n'est pas possible, l'exécution échoue. Une reprise après crash marque le cycle interrompu et exige une revue. Le mutex/verrou est libéré par Windows lors de la fin du processus ; aucun PID historique n'est tué.

Une reprise manuelle ne lance pas automatiquement un superviseur arrêté : relancer `run` est nécessaire. `--max-cycles` limite le nombre de cycles effectués par une invocation, sans annuler les pauses manuelles.

## Copie du vrai projet et sauvegardes

`runtime-project` contient déjà une copie de recherche de Token Radar. Le projet principal n'a pas été initialisé en Git. La copie possède son propre Git et un bundle de référence ; elle démarre en pause, avec appels IA interdits, commandes de validation vides et aucun fichier modifiable autorisé.

L'import choisit seulement les dossiers de code, tests, documentation, scripts, migrations et exemples, avec quelques fichiers de configuration publics. `.env`, données `.local`, binaires `.tools`, environnements, clés/certificats, journaux, bases et métadonnées Git sont exclus. Les liens/reparse points, contenus binaires, clés privées reconnaissables et limites de taille provoquent un refus. L'import n'est **pas** une sauvegarde des données live : les données originales restent à leur emplacement.

Un nouvel import exige un nouveau runtime : aucun écrasement automatique. Un changement de la source après import bloque l'acceptation et demande une nouvelle référence.

```powershell
# Depuis le prototype, copier seulement le code autorisé, sans modifier la source.
& $pythonRadar -m supervisor --runtime runtime-project-2 init --source ../..
# Exporter une modification acceptée pour revue ; aucun patch appliqué automatiquement.
& $pythonRadar -m supervisor --runtime runtime-demo export ID_DU_CYCLE_ACCEPTE
```

La branche `validated` est la référence de la copie ; chaque cycle possède `cycle/<identifiant>`. Les bundles avant/après acceptation permettent une restauration dans **un autre dossier**. Aucune suppression de worktree ou donnée n'est automatisée.

## Budgets et décisions risquées

Les valeurs sont dans `runtime/config.json`, contrôlé par l'opérateur, hors worktree de l'exécutant. Par défaut : zéro appel IA/jour, zéro token/jour, appel limité à 600 s, disque du runtime limité à 500 Mio, intervalle 15 min. Une réservation SQLite précède chaque appel IA ; une consommation inconnue conserve la réservation au lieu de compter zéro. Un cycle peut appeler l'IA pour la découverte et l'implémentation, au plus deux fois avec les adaptateurs fournis.

Le plafond d'appels est strict au lancement. La sortie cumulée des commandes est bornée à 2 Mo ; Windows Job Objects limitent la mémoire engagée de chaque processus à 1 Gio. Le plafond de tokens est **une limite de réservation et une mesure après appel**, pas une limite de génération garantie : `codex exec` ne fournit pas ici de plafond absolu de tokens ni de coût en euros. Un dépassement de réservation provoque une pause. Aucun prix n'est inventé et aucune facturation API n'a été engagée. Pour une limite monétaire absolue, ajouter un plafond externe au compte/fournisseur compatible et vérifier sa sémantique avant activation.

Une proposition de risque moyen/élevé attend une validation dans le rapport. Après revue :

```powershell
& $pythonRadar -m supervisor --runtime runtime-project approve ID_DE_PROPOSITION
```

Cette validation concerne cette proposition uniquement. Elle ne permet pas de modifier les secrets, le projet principal, le contrat de test, de supprimer des fichiers, d'accéder au réseau ou de sortir du périmètre. Promotion principale, déploiement, migration réelle, trade, abonnement, notification externe et configuration des services restent des opérations manuelles hors prototype.

## Rapports depuis un téléphone

Le rapport `runtime-demo/reports/index.html` est autonome, responsive, sans script ni ressource distante. Il peut être transféré et ouvert sur un téléphone. `summary.json` conserve les preuves structurées. Aucun serveur public, email ou message n'est envoyé.

Pour une lecture locale actualisée :

```powershell
& $pythonRadar -m supervisor --runtime runtime-demo serve --port 8765
```

Le serveur est volontairement limité à `127.0.0.1` et n'expose que le dossier des rapports. **Cette adresse n'est pas accessible directement depuis un autre téléphone.** Pour une consultation distante continue, connecter ultérieurement un canal privé authentifié/VPN, avec votre validation, plutôt que d'exposer le disque ou une API de contrôle publiquement. Aucun contrôle distant n'est implémenté ; les ordres sont donnés au terminal.

## Conditions avant activation réelle

L'adaptateur CLI est implémenté mais non essayé avec le modèle. `sdk-adapter.mjs` illustre le SDK officiel TypeScript ; son import n'est pas installé ni validé en réseau. Il n'est pas branché à l'orchestrateur Python : c'est un chemin de transport alternatif à intégrer et tester, pas une capacité annoncée comme active.

Avant activation prolongée sur Token Radar :

- Valider la démo et le rapport, puis choisir budgets, commandes de validation, métrique et périmètre admissibles.
- Préparer une isolation effective des exécutants **et des tests du code généré** : VM/compte isolé avec restrictions vérifiées et sans accès au projet/données/secrets. Un simple worktree est une séparation Git, pas une frontière de sécurité.
- La sandbox Windows actuelle `unelevated` ne garantit pas des refus de lecture sur tous les secrets du disque. Les exclusions d'import et heuristiques de secrets ne remplacent pas cette isolation. `isolated_execution_confirmed` est une attestation opérateur, pas un mécanisme d'isolation automatique.
- Préserver l'authentification Codex existante ; ne pas copier ou modifier `auth.json`. L'adaptateur CLI ignore les intégrations utilisateur pour éviter de charger des MCP/hooks non nécessaires, garde les règles applicables, désactive réseau et recherches, et utilise le modèle explicitement configuré.
- Suspendre les anciens lanceurs directs après validation de la transition : l'ancienne automatisation `Relance Codex` était toujours activée à l'observation. Ce prototype ne la désactive pas et ne prétend pas contrôler ses exécutions.
- Réaliser un seul cycle IA supervisé, vérifier arrêt/budgets/contrats, puis autoriser une période bornée avant tout fonctionnement permanent.

Tests du prototype : `python -m unittest discover -s tests -v`. Les fixtures et essais restent sous `test-scratch` pour inspection ; aucun nettoyage des données du projet n'est réalisé.
