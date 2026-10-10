# Exécution avec ChatGPT Plus

Runtime préparé : `runtime-chatgpt-plus`. Modèle exact `gpt-6.1-sol`,
`model_reasoning_effort="medium"`, transport Ralph–Codex. L'authentification
existante est réutilisée par Codex ; aucune clé API n'est héritée, aucune
authentification n'est copiée ou modifiée. `forced_login_method="chatgpt"`
interdit la bascule vers une clé API. La sandbox Windows reste explicitement
activée en mode `unelevated`, même lorsque la configuration utilisateur est ignorée.

## Arrêt sur quota

Avant **chaque appel au modèle**, un processus app-server distinct lit uniquement
`account/read` (`refreshToken=false`) et `account/rateLimits/read`. Il ne démarre
aucun thread IA. Le compte doit être de type ChatGPT. Les fenêtres primaire et
secondaire du bucket Codex sont vérifiées : l'une à 100 % ou un état de limite
atteinte provoque un arrêt persistant. Les quotas inconnus, erreurs de réseau
ou changements de schéma arrêtent également l'exécution, sans appel au modèle.
L'email et les réponses brutes ne sont ni publiés ni sauvegardés.

Une erreur d'exécution Codex `UsageLimitExceeded` ou message de quota atteint
arrête également la boucle. Le processus superviseur sort immédiatement après
le rapport, sans attendre le prochain cycle. Il n'achète ni n'active de crédits,
ne consomme pas de crédit de remise à zéro et ne reprend pas automatiquement
après réinitialisation. Une reprise manuelle revérifie le quota.

Il s'agit du quota **Codex inclus dans le plan ChatGPT**, pas d'un compteur de
messages Chat ordinaire. Les tokens ne sont pas convertis en un quota Plus
inventé. La consultation avant appel évite d'engager de nouvelles tâches lorsque
la limite incluse est connue comme atteinte, même si des crédits supplémentaires
sont disponibles. Elle ne peut pas garantir une coupure exacte en cours de tour
si d'autres sessions consomment simultanément le quota : les informations sont
un instantané et le fournisseur peut laisser un tour déjà commencé se terminer.

Sources officielles : [quotas et crédits](https://learn.chatgpt.com/docs/pricing),
[app-server et rateLimits](https://learn.chatgpt.com/docs/app-server),
[authentification imposée et configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

Les plafonds de secours restent actifs : 256 appels/jour, 25 millions de tokens
réservés/jour, 100 000 tokens réservés par appel, 600 s par appel, 1 500 Mio de
disque. Ils peuvent arrêter le système avant le quota pour limiter une anomalie.
Les pauses sur absence de progrès et les commandes manuelles restent prioritaires.

## Projet réel et validation

Le runtime contient une copie assainie de Token Radar. Le premier périmètre
modifiable est `src/token_radar/features.py`, pour chercher des optimisations
hors ligne qui préservent le comportement. Tous les autres fichiers importés
sont protégés ; aucun changement n'est promu automatiquement dans le principal.
Les futurs périmètres demandent des contrats et mesures adaptés.

`project_checks.py`, conservé hors des worktrees, vérifie cinq jeux d'observations
contre les sorties de référence et exécute les tests des features et de leurs
consommateurs applicatifs, puis Ruff. Les tests d'installation/récupération du
poste ne sont pas exécutables dans la copie sans `.venv` et `.local` ; ils ne
font pas partie de ce contrat. La référence applicative a passé **45 tests**,
avec **1 test PostgreSQL ignoré**, et Ruff. Le benchmark synthétique est disponible.
Il mesure un temps de calcul, pas une performance financière ou un résultat live.

## État du lancement

Le contrôle effectué ensuite par l'utilisateur depuis son PowerShell habituel
a passé le contexte Windows, l'authentification ChatGPT et la lecture du quota.
Il s'est arrêté sur `external_isolation_not_confirmed`. L'isolation est donc
le prochain prérequis réel ; elle n'a pas encore été installée. Le script
`check-isolation.ps1` fournit un inventaire en lecture seule des fonctionnalités
de virtualisation et des informations CPU/mémoire nécessaires pour choisir
l'environnement. Il n'active aucune fonctionnalité et ne redémarre pas Windows.

Le lancement a été tenté le 10 octobre 2026 et **refusé** par le précontrôle :
`restricted_windows_execution_context`. La consultation réelle du quota depuis
cette session a aussi échoué (`chatgpt_quota_unknown`). Le runtime est arrêté,
zéro appel au modèle a été lancé. Le jeton Windows et le réseau gérés de la
session ne sont pas modifiés ou contournés.

L'isolation externe des exécutants **et des tests** n'a pas été attestée.
`isolated_execution_confirmed` reste donc faux. Un PowerShell normal résout
le contexte restreint du lanceur mais ne constitue pas à lui seul cette isolation.
Préparer l'environnement isolé prévu dans README et vérifier ses restrictions
avant de renseigner cette attestation. L'ancienne automatisation directe doit
aussi être suspendue lors de la transition ; elle n'a pas été modifiée ici.

Après préparation et vérification de cet environnement, depuis le dossier principal :

```powershell
.venv/Scripts/python.exe .local/supervisor-prototype/launch_project.py --check
# Si le contrôle retourne ready :
.venv/Scripts/python.exe .local/supervisor-prototype/launch_project.py
```

Le lanceur n'atteste jamais l'isolation à votre place. Il vérifie le contexte,
la source et le quota puis refuse si l'attestation manque. Aucun service Windows
ou lancement en arrière-plan n'a été installé. Pour les contrôles manuels,
depuis le dossier du prototype :

```powershell
../../.venv/Scripts/python.exe -m supervisor --runtime runtime-chatgpt-plus pause
../../.venv/Scripts/python.exe -m supervisor --runtime runtime-chatgpt-plus stop
../../.venv/Scripts/python.exe -m supervisor --runtime runtime-chatgpt-plus status
```

Rapport autonome : `runtime-chatgpt-plus/reports/index.html`.
