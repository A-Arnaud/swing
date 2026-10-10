# Installation observée

Observation du 10 octobre 2026, session de développement actuelle.

- Projet : `C:\Users\Administrator\Downloads\d\token-radar` ; aucun dépôt Git à sa racine.
- Codex CLI : **0.160.0**, installation npm existante, exécutable natif Windows disponible.
- Configuration publique pertinente : modèle **gpt-6.1-sol**, raisonnement **medium**, sandbox Windows **unelevated**. Aucune autre valeur de configuration ou authentification exportée.
- Node : **v24.19.0** ; npm **11.17.0** ; Git **2.53.0.windows.2**.
- Python local du projet : 3.12 ; le prototype utilise seulement la bibliothèque standard au runtime.
- SDK Codex non installé dans les modules globaux observés. Aucun installateur SDK lancé.
- Jeton Windows de cette session **restreint** ; HTTP/HTTPS/ALL proxy configurés (valeurs non exposées). Les essais HTTPS du cycle précédent ont échoué ; aucune disponibilité de fournisseur inférée.
- Les services applicatifs étaient indisponibles au dernier contrôle ; PostgreSQL `pg_ctl` échoue avec erreur de création du jeton restreint 87. Le superviseur prototype n'a pas besoin de ces services.
- L'ancienne reprise reste activée : `schedule.json` indique `enabled=true`, intervalle documenté cinq heures ; heartbeat du minuteur observé à 09:52:54 UTC, état `waiting`. Cette observation n'est pas une preuve d'exécution future. Aucun arrêt/modification de cette automatisation effectué.
- Le dossier voisin ne figure pas parmi les racines d'écriture autorisées à cette session ; le prototype autonome est donc sous `.local`, séparé des sources applicatives. Son déplacement éventuel est manuel.

Les fichiers secrets `.env`, `auth.json`, mots de passe DB, tokens et contenus des bases/journaux n'ont pas été copiés dans la recherche ni modifiés pour ce travail.
