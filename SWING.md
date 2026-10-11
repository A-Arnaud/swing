# SWING — version locale

Software Workflow for Iterative Navigation and Generation.

Une interface pour donner un cap, voir les itérations de Ralph + Codex et reprendre
la main. Token Radar est le premier projet connecté. L'accueil reprend la sobriété
de la référence claire et sa typographie en caractères ; l'atelier reprend le
terminal et les surfaces sombres des deux autres références. Aucun asset,
police ou service externe n'est chargé.

## Fonctionnement disponible

- Accueil, projet connecté, briefs de futurs projets enregistrés sur disque.
- Atelier : activité du superviseur, diff lu dans les fichiers courants, historique.
- Arrêt/reprise existants, consignes persistantes, statistiques dépliables.
- Navigation au clavier, disposition mobile, animations respectant reduced-motion.

Un nouveau brief n'est pas un runtime : il n'exécute aucun code ni appel IA.
Le bouton **Importer GitHub** propose une connexion dans le navigateur via Git
Credential Manager, puis liste les dépôts accessibles au compte (propriétaire,
collaborateur ou organisation), avec pagination et recherche parmi les résultats
chargés. Les dépôts privés sont inclus selon les droits du compte. La saisie
directe d'une URL GitHub reste disponible. Les identifiants restent dans le gestionnaire du poste ; aucun jeton
n'est envoyé à l'interface, à Codex ou au runtime. Cette connexion correspond
à la version Windows locale, pas à une plateforme hébergée avec des comptes web.
Le dépôt et les consignes préparent un runtime indépendant. **Importer et démarrer**
lance Ralph sur ce runtime, après vérification du quota. Aucune commande de test
n'est obligatoire : les manifests, scripts npm et tests Python, Go, Rust ou .NET
préparent un ensemble de contrôles automatiquement, y compris dans les sous-projets.
Les contrôles personnalisés restent disponibles dans les options facultatives.
Les projets connectés sont ouverts depuis leurs cartes.
Le choix de projet est conservé dans le navigateur et chaque API vise son runtime.
Le code reste dans `swing-projects/<id>/source`, les preuves et sauvegardes dans
`swing-projects/<id>/runtime`. Aucun push, installation de dépendances ou lancement
de l'application importée n'est automatique. **Reprendre** lance Ralph et revérifie le quota.
Un premier appel Codex lit le README, l'architecture et des sources ciblées ;
il conserve le but, les composants, les limites des tests et les prochaines priorités.
Cette analyse est réutilisée : elle sert d'orientation, les faits liés à la tâche
doivent toujours être vérifiés dans le code actuel. Les commandes
Python chargent le code de la copie candidate avant un éventuel paquet installé.
Les tests déjà présents, configurations et secrets restent protégés. Les sources
autorisées sont modifiables et chaque tâche peut créer des sources ou des tests
avec des chemins exacts approuvés. Les nouveaux tests deviennent protégés après
acceptation. Une création ne remplace jamais un fichier ajouté par l'utilisateur.
Les changements validés sont appliqués
à la copie locale. Les outils et dépendances du projet doivent être disponibles :
ils ne sont pas installés automatiquement. npm est résolu vers son CLI Node sans
shell ; les autres commandes utilisent un exécutable disponible directement.
Les sources de plusieurs langages et la documentation sont reconnues ; un dépôt
sans tests commence par des contrôles de structure, signalés comme tels. Une
modification de code ne peut pas être acceptée sur leur seul résultat : des tests
de comportement doivent être ajoutés. Les échecs initiaux peuvent être réparés ;
les contrôles finaux doivent réussir. Images et polices usuelles restent disponibles
dans la copie candidate, protégées ; les liens et sous-modules sont exclus.
Un dépôt vide ou sans fichiers pris en charge nécessite une préparation initiale.
Importer un dépôt ne garantit pas qu'il peut déjà
être compilé, testé ou amélioré sur ce poste. Les briefs restent séparés.

## Consignes : des effets vérifiables

Des consignes non vides deviennent un objectif persistant. Un premier bilan
définit des critères observables et jusqu'à trois tâches en ordre de dépendance.
Ralph exécute ces tâches avant toute recherche générale, puis fait un nouveau
bilan sur le code validé. Les critères restent identiques jusqu'à un changement
des consignes : il ne peut pas les supprimer pour déclarer le travail terminé.
Le plan, les tâches et le bilan survivent à une reprise. La recherche générale
commence seulement après des preuves pour chaque critère et un contrôle
indépendant réussi. Aucun changement artificiel n'est demandé pour ce bilan.

Une liste vide, des données manquantes, un périmètre insuffisant ou une tâche
attendant approbation ne valent pas réussite. Sans prochaine tâche justifiable,
la boucle s'arrête sur cet objectif plutôt que de consommer du quota ailleurs.
L'interface expose les critères et l'état de l'objectif dans un détail compact.
« Consignes vérifiées » décrit le bilan du modèle et les contrôles configurés ;
ce n'est pas une garantie de perfection ni une preuve de rentabilité. Des tests
généraux ne couvrent pas nécessairement chaque exigence en langage naturel.

Le modèle reçoit un contexte ciblé pour chaque tâche. Un bilan est demandé
seulement lorsque la file de l'objectif est vide, jamais à chaque itération.
Les gardes de quota et les protections existantes s'appliquent aussi aux bilans.

Les propositions issues de Codex portent l'empreinte des consignes utilisées.
Si les consignes changent, les anciennes pistes passent en attente et un nouvel
objectif reçoit la nouvelle intention. Elles ne sont pas supprimées. Les pistes
sans anciennes consignes peuvent être réutilisées lorsque les consignes sont vides.
La concurrence entre sélection et préparation d'un appel bloque la tâche obsolète.
Le périmètre autorisé, les tests et le quota restent imposés par le superviseur.

La réception affichée correspond à l'envoi du prompt au transport Codex : heure,
étape, empreinte et longueur du prompt. Elle ne prétend pas prouver que le modèle
a correctement interprété toute consigne en langage naturel. Les contrôles
indépendants et la lecture des changements restent les preuves du comportement.

## Consommation

Le contexte de recherche conserve une carte compacte et six résultats récents.
La carte est limitée à 160 fichiers, avec priorité aux points d'entrée ; les
autres modules restent accessibles par recherches ciblées. Après l'analyse
initiale, son résumé remplace la répétition du README complet dans chaque appel.
L'implémentation reçoit les fichiers ciblés, leurs tests correspondants et au plus
trois résultats pertinents. Les résultats ne sont plus dupliqués dans le prompt
Ralph. Les consignes et le brief ne sont pas coupés pour réduire artificiellement
la mesure. Les sorties d'outils sont demandées sous forme de recherches ciblées.

Les tokens affichés viennent des événements `turn.completed.usage` de Codex,
conformément à la [documentation du mode non interactif](https://learn.chatgpt.com/docs/non-interactive-mode).
Les tokens d'entrée en cache sont une partie des tokens d'entrée, pas une somme
supplémentaire. Les appels sans mesure restent signalés comme inconnus. Le quota
ChatGPT est un instantané séparé ; aucun taux de conversion euros/tokens/quota
n'est inventé. Modèle gpt-6.1-sol, medium ; ChatGPT uniquement, arrêt sur quota.

## Vérification

`python -B -m unittest discover -s tests -v` couvre le superviseur, les changements
de consignes, la transmission à Ralph, la mesure d'usage, les diffs et les briefs.
Les tests de transport utilisent un faux modèle, sans consommer de quota.
Les captures de contrôle de l'interface sont dans `previews/`.
