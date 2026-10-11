# Ralph autonome — configuration active

Ralph lance des sessions Codex CLI distinctes en arrière-plan. Aucun relais
avec cette conversation n'est nécessaire. Modèle `gpt-6.1-sol`, raisonnement
`medium`, connexion ChatGPT uniquement. Aucun achat de crédits ni clé API.
Quota vérifié avant chaque appel ; quota épuisé ou inconnu : arrêt persistant.
Pas de reprise automatique après réinitialisation du quota.

## Mission produit et niveau de preuve

L'objectif produit est un outil fiable d'alerte précoce et d'analyse des nouveaux
tokens Solana : évaluer uniquement les informations disponibles au moment du
signal, expliciter risques et incertitudes, puis améliorer les critères à partir
de résultats observés devenus matures. L'usage reste analyse et paper seulement.

La priorité est d'éviter les erreurs qui masquent de bons candidats ou font
remonter de mauvais candidats. Toute amélioration de score ou de paramètres
doit être comparée à la baseline sur des résultats indépendants, assez nombreux
et matures, avec une séparation chronologique purgée par mint. Rapporter au
minimum précision des alertes, rappel/couverture, volume d'alertes, périodes,
régimes, résultats censurés et taille des échantillons. Ne jamais régler un
paramètre sur l'échantillon de test ni utiliser une donnée future.

Les tests synthétiques établissent la correction du code, pas sa capacité à
prédire un token gagnant. Les rendements spot estimés ne sont pas des fills
exécutables. Quand les labels indépendants et matures manquent, Ralph doit
l'indiquer et se limiter à la fiabilité, à la qualité de collecte ou à la
mesurabilité ; il ne doit pas prétendre que le détecteur est devenu plus
rentable ou prédictif. Une donnée absente, périmée ou censurée reste inconnue,
jamais automatiquement sûre ou négative.

Le contrôle d'acceptation automatique actuel exécute les tests applicatifs et
Ruff : il ne mesure pas le pouvoir prédictif. Il n'autorise donc pas à lui seul
un réglage des seuils, poids ou paramètres du modèle. Pour automatiser cet
apprentissage, il faudra un évaluateur indépendant de confiance alimenté par
des labels matures et assez nombreux, avec baseline, validation chronologique
purgée et mesure de couverture/erreurs. Les évaluations paper/spot actuelles ne
sont pas des transactions réelles et ne règlent pas automatiquement la sélection.
Pour parler d'un « bon token », s'appuyer sur les cibles versionnées existantes
de `src/token_radar/research.py` : +30 % simulé en 2 min, +100 % en 10 min ou
+200 % en 30 min. Ce sont des cibles de recherche, pas une performance validée.

Le périmètre couvre les 44 fichiers existants sous `src/token_radar/`, y compris
le frontend. De nouveaux fichiers source et tests de régression peuvent aussi
être proposés, avec chaque chemin explicitement approuvé dans la tâche. Les
tests existants restent protégés ; les nouveaux tests acceptés le deviennent.
Ralph recherche de petites améliorations justifiées, implémente
une proposition par session, puis exécute les tests applicatifs indépendants
et Ruff. Les contrats de tests restent protégés. Les améliorations n'ont plus
à démontrer un gain de vitesse de 20 % : la configuration active utilise
`acceptance_mode=application_checks`. Passer les tests n'est pas une preuve
d'amélioration financière ou de fonctionnement en production.

Les modifications acceptées sont appliquées automatiquement au projet original.
Chaque fichier original est sauvegardé dans `runtime-chatgpt-plus/backups/`.
Un changement concurrent du projet bloque l'application ; un échec partiel
provoque un retour arrière sans écraser une modification concurrente. Un journal
incomplet après crash bloque toute nouvelle application jusqu'à résolution.
Un verrou Windows empêche deux lanceurs simultanés.

Tests PostgreSQL ignorés en l'absence d'une base jetable. Le test de récupération
des services Windows reste exclu : les scripts du poste sont protégés.
Les secrets, dépendances, migrations, déploiements, transactions et opérations
de services ne font pas partie du périmètre autonome. Les propositions à risque
moyen ou élevé restent en attente ; les pauses après échecs répétés sont conservées.
L'exécution locale est autorisée, l'isolation externe n'est pas attestée.

Le tableau de bord local montre l'activité, le quota et les cycles, avec un bouton
« Arrêter Ralph ». L'arrêt interrompt proprement l'appel Codex en cours. Le rapport
du code reste intégré et se recharge toutes les cinq secondes.
Le rythme actif est de cinq secondes après un succès ou lorsqu'une piste attend.
Une recherche infructueuse sans autre piste conserve deux minutes d'attente.
Le carnet de pistes survit aux commits : chaque hypothèse est revérifiée sur le
code courant avant modification, avec la liste des fichiers changés depuis sa
découverte. Les approbations à risque moyen/élevé ne sont pas transférées à une
nouvelle baseline. Les échecs et les résultats acceptés restent archivés.

`runtime-chatgpt-plus/project-brief.md` décrit la mission et l'architecture.
`runtime-chatgpt-plus/memory/project.json` conserve la carte du code, les empreintes
et les résultats des contrôles. L'indexation n'utilise aucun appel IA ; seules les
fiches de fichiers modifiés sont recalculées. Le contexte envoyé aux deux étapes
(recherche et implémentation Ralph) inclut cette mémoire, les consignes de
l'interface et les résultats récents. La recherche s'arrête au premier défaut
utile démontré ; elle peut conserver deux pistes voisines déjà étayées.
Le tableau de bord indique les pistes conservées et les temps de recherche/code.
Ce carnet n'est pas un apprentissage de paramètres prédictifs ni une preuve de
performance ; ses indications sont vérifiées sur les fichiers de la tâche.

L'arrêt quota,
les conflits, les limites de budget/disque et les blocages répétés priment.
Le processus continue pendant que le poste reste allumé et la session ouverte ;
aucun démarrage Windows ni redémarrage après crash n'est enregistré.

Depuis le dossier du projet :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .local\supervisor-prototype\ralph.ps1 -Action Start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .local\supervisor-prototype\ralph.ps1 -Action Report
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .local\supervisor-prototype\ralph.ps1 -Action Stop
```

Cette note décrit l'état actif et remplace les anciennes notes de préparation
VM ou d'attente manuelle présentes dans les documents historiques.


## Travail utile et consommation

Les consignes actives passent avant toute recherche générale. Leur plan et ses
critères sont conservés entre les itérations ; une file de tâches n'est pas
replanifiée tant qu'elle contient du travail exécutable. Après validation des
critères, seules des améliorations justifiées par des faits sont proposées.

Pour les projets réels, une recherche sans tâche exécutable arrête Ralph dès
le premier bilan vide. Il ne se relance pas après un délai pour refaire la même
recherche. Une tâche en attente d'approbation arrête également la recherche.
Une reprise manuelle permet un nouveau bilan ; modifier les consignes renouvelle
le plan. Les démonstrations synthétiques conservent leur ancien comportement.

Quand les contrôles indépendants échouent après une modification, une seule
correction ciblée peut reprendre le candidat existant, avec un diagnostic court
et filtré. Elle ne recommence ni l'audit ni le développement depuis zéro. Elle
compte dans le plafond de deux tentatives par tâche et utilise les mêmes contrôles
de quota, d'arrêt, de périmètre et de consignes que le premier appel. Aucun
réessai pour une modification protégée, un quota épuisé ou une interruption.
Si les tests échouent encore, le candidat reste rejeté et n'est pas appliqué.

Le contexte d'implémentation omet l'inventaire général des dossiers et recettes ;
il garde les fichiers ciblés, la compréhension persistante et les consignes.
Les profils, tests et sorties réelles priment sur les déclarations du modèle.
Un passage des tests établit le contrat testé, pas la perfection du produit.
