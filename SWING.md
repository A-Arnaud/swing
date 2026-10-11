# SWING — version locale

Software Workflow for Iterative Navigation and Generation.

Une interface pour donner un cap, voir les itérations de Ralph + Codex et reprendre
la main. Token Radar est le premier projet connecté. L'accueil reprend la sobriété
de la référence claire et sa typographie en caractères ; l'atelier reprend le
terminal et les surfaces sombres lavande des deux autres références. Aucun asset,
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
chargés. La saisie directe d'une URL GitHub reste disponible pour les dépôts
publics. Les identifiants restent dans le gestionnaire du poste ; aucun jeton
n'est envoyé à l'interface, à Codex ou au runtime. Cette connexion correspond
à la version Windows locale, pas à une plateforme hébergée avec des comptes web.
Le dépôt et les consignes préparent un runtime indépendant. Une commande de test
peut être détectée pour Python, npm, Go, Rust ou .NET, ou saisie explicitement.
Les projets connectés sont ouverts depuis leurs cartes.
Le choix de projet est conservé dans le navigateur et chaque API vise son runtime.
Le code reste dans `swing-projects/<id>/source`, les preuves et sauvegardes dans
`swing-projects/<id>/runtime`. Aucun push, installation de dépendances ou lancement
du projet importé n'est automatique. **Reprendre** lance Ralph et revérifie le quota.
Les tests de référence doivent réussir avant une recherche IA. Les commandes
Python chargent le code de la copie candidate avant un éventuel paquet installé.
Les tests, configurations et secrets restent protégés ; seules les sources
existantes reconnues sont modifiables. Les changements validés sont appliqués
à la copie locale. Les outils et dépendances du projet doivent être disponibles :
ils ne sont pas installés automatiquement. npm est résolu vers son CLI Node sans
shell ; les autres commandes utilisent un exécutable disponible directement.
Les sources de plusieurs langages et la documentation sont reconnues ; un dépôt
sans sources textuelles prises en charge ou sans validation exploitable nécessite
une configuration adaptée. Importer un dépôt ne garantit pas qu'il peut déjà
être compilé, testé ou amélioré sur ce poste. Les briefs restent séparés.

## Consignes : des effets vérifiables

Les propositions issues de Codex portent l'empreinte des consignes utilisées.
Si les consignes changent, les anciennes pistes passent en attente et une nouvelle
recherche reçoit la nouvelle intention. Elles ne sont pas supprimées. Les pistes
sans anciennes consignes peuvent être réutilisées lorsque les consignes sont vides.
La concurrence entre sélection et préparation d'un appel bloque la tâche obsolète.
Le périmètre autorisé, les tests et le quota restent imposés par le superviseur.

La réception affichée correspond à l'envoi du prompt au transport Codex : heure,
étape, empreinte et longueur du prompt. Elle ne prétend pas prouver que le modèle
a correctement interprété toute consigne en langage naturel. Les contrôles
indépendants et la lecture des changements restent les preuves du comportement.

## Consommation

Le contexte de recherche conserve une carte compacte et six résultats récents.
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
