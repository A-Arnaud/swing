# Vérifications du prototype — 10 octobre 2026

## Résultats exécutés

- **20 tests du superviseur réussis**, Python `unittest`, véritables processus et opérations Git Windows. Dernière exécution : 32,591 s. Ruff sur `supervisor`/`tests` (`E4,E7,E9,F,I`) passe.
- Syntaxe de `sdk-adapter.mjs` validée par Node. **Package SDK absent, import/exécution SDK non testés.** Aucun appel modèle/API réel effectué.
- Projet principal : **139 tests réussis, 8 PostgreSQL ignorés** ; aucun changement de code principal réalisé dans ce travail. L'absence de PostgreSQL explique la limite de validation, pas une réussite live.
- Copie de recherche : **125 fichiers importés** ; empreintes de la source vérifiées identiques après construction/tests. `.env`, données, clés, journaux et binaires exclus. Git et bundle créés dans la copie uniquement.
- Codex local : `login status` indique **Logged in using ChatGPT**, sans export de jeton. Des avertissements d'accès à son dossier temporaire persistent dans cette session restreinte ; authentification disponible n'implique pas que l'exécution modèle fonctionnerait ici.

## Comparaison réelle sur fixture synthétique

La démo est dans `runtime-demo`. L'agent est **simulé** ; contrat, chronométrage, worktrees, commits et sauvegardes sont réels.

| Cycle | Proposition | Décision indépendante | Mesure |
|---|---|---|---|
| `3816837fbbfa4e44839fb863ae7cbf10` | Raccourci `cache-count` | Rejet : contrat de sommation incorrect | Référence médiane 104,0423 ms ; aucune mesure après présentée comme valide |
| `325277aa26f0481b9cb2c77fe829696f` | Alternative `linear-sum` | Acceptée dans la copie, contrat inchangé | Médiane **103,8863 → 0,0404 ms**, réduction **99,9611 %** |
| Trois cycles suivants | Recherche après optimisation terminée | Aucune nouvelle hypothèse utile ; pause automatique | Aucun gain inventé ; référence acceptée conservée |

Chaque mesure avant/après est la médiane de trois invocations indépendantes du benchmark ; chaque invocation chronomètre trois répétitions internes. C'est une fixture de sommation, sur cette machine, sans projection sur la performance de Token Radar ou les résultats financiers. Aucune représentativité statistique d'un marché n'est revendiquée.

Le commit accepté est `e2543098441930bca9f1b642740f1f897eba0cde`. Les bundles de référence, avant et après acceptation existent ; `git bundle verify` passe dans le test. Le patch du cycle accepté a été exporté dans `runtime-demo/325277aa26f0481b9cb2c77fe829696f.patch`, sans application au projet principal.

## Contrôles de sécurité et de durée

Les tests exercent :

- Rejet réel d'une régression et adoption d'une stratégie alternative après mesure.
- Poursuite de découverte après achèvement, déduplication des propositions, limite de tentatives et pauses bornant les répétitions.
- Persistance de pause/reprise/arrêt, arrêt donné depuis une autre connexion SQLite pendant une mesure, absence de promotion après interruption.
- Approbation spécifique pour un risque élevé ; identifiant inconnu refusé.
- Réservation atomique d'appels/tokens, conservation des usages inconnus et refus d'un budget nul avant lancement.
- Pause sur limite de disque sans suppression du bundle ; verrou refusant un second superviseur.
- Crash enregistré comme interrompu et reprise soumise à revue.
- Import sans secrets/données, refus de contenu ressemblant à une clé privée, refus de traversée de chemin, y compris chemin Windows enraciné.
- Refus des modifications du contrat ou d'une suppression de fichier dans la fixture.
- Timeout et interruption manuelle de processus ; limites de sorties ; descendant effectivement terminé par Windows Job Object lorsque sa commande parente finit.
- Échappement de texte non fiable dans le HTML et présence des paramètres responsive. **Aucun test sur un vrai téléphone ou rendu Chromium revendiqué.**

Une première exécution des tests a rencontré les ACL privées de `tempfile` dans la sandbox actuelle. Les fixtures utilisent désormais des dossiers à identifiant unique héritant des permissions du workspace, sans modification d'ACL ni suppression des dossiers problématiques. Un test a révélé qu'un chemin `/absolute` n'est pas `is_absolute()` sous Windows ; la validation refuse désormais aussi les chemins ancrés. Ces défauts ont été corrigés avant la suite finale.

## Ressources et limites non résolues

Les commandes ont un délai, une sortie cumulée bornée à 2 Mo et, sur Windows, une limite de mémoire engagée par processus de 1 Gio dans un Job Object. Le superviseur ne purgera pas automatiquement les essais ou bundles : le quota disque provoque une pause. L'attachement au Job Object se fait immédiatement après création du processus ; ce mécanisme n'est pas une sandbox de lecture/écriture et ne remplace pas une VM ou un compte isolé.

La validation des contrats exécutant du code candidat se déroule dans l'environnement du superviseur pour la démo. **Avant le mode réel, exécuter l'ensemble exécutant + tests dans une frontière OS effectivement isolée.** Le drapeau d'attestation de configuration ne crée pas cette frontière. L'adaptateur CLI, les erreurs réseau, les quotas modèle et un fonctionnement prolongé réel avec IA restent à tester après cette préparation.

Le contrôle financier exact de l'abonnement n'est pas disponible ici ; la réservation de tokens est conservatrice mais peut être dépassée dans un appel. Le rapport HTML fonctionne hors ligne ; consultation distante continue/authentifiée et contrôles depuis téléphone restent à connecter avec validation. Ancienne automatisation principale conservée, pas remplacée silencieusement.
