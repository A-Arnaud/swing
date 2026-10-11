# SWING

Software Workflow for Iterative Navigation and Generation.

Application locale pour suivre et piloter une boucle Ralph + Codex : consignes,
recherche de corrections, developpement, controles independants et application
au projet reel des modifications acceptees, avec sauvegarde.

## Disponible

- Accueil projets avec titre en caracteres et animation circulaire.
- Activite, modifications du code et historique des cycles.
- Consignes persistantes prises en compte dans le choix de la prochaine tache.
- Arret/reprise et suivi du quota Codex inclus dans ChatGPT.
- Theme sombre, noir ou clair et couleur personnalisee par projet.
- Memoire de code et contexte cible pour limiter les informations repetees.

Token Radar / RugsTracker est le premier projet connecte. Le bouton Importer
GitHub permet de connecter le compte dans le navigateur, de choisir un depot
accessible, prive compris, et de preparer un projet independant. Les controles
sont detectes automatiquement : aucune commande de test n'est obligatoire.
Importer et demarrer lance l'analyse initiale du projet, conservee pour les
iterations suivantes. Codex peut creer des sources et des tests, modifier et
ameliorer selon les consignes. Les changements valides restent dans la copie
locale du depot importe ; aucun push ou installation de dependances automatique.
Les idees sans depot peuvent encore etre enregistrees comme briefs.

## Structure

`supervisor/` contient l'orchestration et les gardes ; `dashboard.py`,
`dashboard_data.py`, `dashboard.html`, `dashboard.js` et `style.css` servent
l'interface locale. `prompts/` contient les instructions du transport Ralph.
`vendor/ralph/` conserve la reference amont et sa licence MIT.

## Installation locale actuelle

Ce depot est actuellement utilise dans `.local/supervisor-prototype` du projet
Token Radar. Les lanceurs d'integration supposent cette disposition et reutilisent
son `.venv`. Un clone seul ne contient ni runtime configure, ni donnees, ni secrets.
La separation du code en deux depots ne rend pas encore les lanceurs universels.

Depuis le dossier Token Radar, pour servir un runtime deja prepare :

```powershell
.venv/Scripts/python.exe .local/supervisor-prototype/dashboard.py --runtime .local/supervisor-prototype/runtime-chatgpt-plus
```

Pour les commandes Ralph et les conditions de reprise : [AUTONOMOUS.md](AUTONOMOUS.md).
Pour lancer l'application Token Radar et son rechargement : [APPLICATION.md](APPLICATION.md).
Le port du tableau de bord est indique dans `runtime-chatgpt-plus/reports/dashboard.json`.

## Verification

Depuis ce depot, avec l'environnement Python du projet :

```powershell
../../.venv/Scripts/python.exe -B -m unittest discover -s tests -v
node --check dashboard.js
```

Les tests utilisent des transports simules et ne consomment pas le quota IA.

## Limites

Le runtime reel demande `gpt-6.1-sol`, raisonnement `medium`, authentification
ChatGPT uniquement. Quota epuise ou inconnu : arret, sans bascule payante.
Les tests ne prouvent pas une amelioration predictive ou financiere du detecteur.
Les changements acceptes sont sauvegardes et appliques au dossier source ; un
conflit avec une modification externe bloque leur application.

Les donnees locales, identifiants Telegram, runtimes, bases, historiques de
session, captures et journaux sont exclus de Git. Les notes historiques sont
conservees dans [docs/PROTOTYPE-HISTORY.md](docs/PROTOTYPE-HISTORY.md) ; elles ne
decrivent pas toutes la configuration active. [SWING.md](SWING.md) detaille le
fonctionnement actuel des consignes et du contexte.
