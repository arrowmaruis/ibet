# Suivi de progression

## Étapes terminées
- [x] Init du projet (venv `.venv`, `requirements.txt`, `.gitignore`, `.env.example`)
- [x] `ibet/sources/cache.py` : cache disque JSON avec TTL
- [x] `ibet/sources/api_client.py` : `get_matches(date, league=None, provider=...)` + 3 providers normalisés
- [x] `ibet/interfaces/exporter.py` : `to_csv()`, `to_json()`, `render_console()`
- [x] `ibet/interfaces/cli.py` : CLI avec `argparse`
- [x] Gestion des erreurs (clé absente/refusée, quota, date invalide, fuseau invalide, réseau, SSL)
- [x] `ibet/sources/certificats.py` : contournement propre de l'interception HTTPS antivirus
- [x] Tests avec dates réelles (2026-08-29 / 08-30 / 09-05 / 09-12) — OK
- [x] README avec instructions d'installation et d'obtention des clés API
- [x] **Mode journée** : contournement légitime du plafonnement de la clé gratuite
      (`eventsround.php`), 6 grandes compétitions européennes + alias
- [x] Recherche dichotomique de la journée couvrant une date (`--round` optionnel)
- [x] Affichage groupé par date quand une journée s'étale sur plusieurs jours
- [x] Démo validée : Premier League j.4, Ligue 1 j.4, Serie A j.1 (avec scores), C1
- [x] Statistiques de match (`--stats`) : tirs, corners, fautes, cartons, arbitre
- [x] Pont `idAPIfootball` : matchs via TheSportsDB, stats via API-Football
- [x] `tests/test_stats.py` : 27 assertions sur le parseur, sans réseau
- [ ] Test du chemin API-Football en reel (bloqué : clé à créer par l'utilisateur)
- [x] **Provider `flashscore`** (2026-09-05) : flux interne de flashscore.fr, aucune clé,
      couverture mondiale (~1 750 matchs/jour), scores en direct, libellés en français
- [x] Statistiques Flashscore (`df_st_`) : les 7 stats suivies, sans aucune clé API
- [x] Correction du regroupement d'affichage exposé par la couverture mondiale
- [x] Tests hors ligne du parseur Flashscore (matchs + statistiques) dans `tests/test_stats.py`
- [x] `--team` : sélection d'un match par équipe, et fiche statistique en barres comparatives
- [x] Correction : `--no-cache` ne s'appliquait pas à `get_stats`
- [x] URL de la page Flashscore par match (champ `url`, exporté) + `--open`
- [x] Cache versionné par schéma (`CACHE_SCHEMA`)
- [x] `--form [N]` : forme des deux équipes sur leurs N derniers matchs (flux `df_hh_`),
      amicaux marqués, moyennes détaillées en option via `--stats`
- [x] `ibet/prevision/predict.py` : prévision buts / corners / tirs cadrés (Maher 1982 + loi de Poisson)
- [x] Référence de championnat recalculée depuis les résultats Flashscore
      (`league_baseline`) — 138 matchs pour la Bundesliga, 98 pour la Ligue 1
- [ ] Test avec une clé `football-data` réelle (bloqué : clé à créer par l'utilisateur)
- [ ] Test avec une clé `api-football` réelle (bloqué : clé à créer par l'utilisateur)
- [x] Historique de forme mis en cache sans `count` ni `before` (`_fs_history`) : une seule
      requête sert toutes les profondeurs et toutes les dates de coupe
- [x] `api_client` charge `.env` lui-même : tout programme autre que `ibet/interfaces/cli.py` (serveur,
      évaluation, script ponctuel) partait sans `CA_BUNDLE` et échouait en SSL
- [x] **`ibet/interfaces/serveur.py`** : API HTTP locale (FastAPI), `GET /api/matchs`, même code et même
      cache que le CLI, pannes traduites en 429 / 503 / 502
- [x] **`../ibet-web/`** : interface web Vue 3 + Vite en Feature-Sliced Design, liste des
      matchs du jour groupée par pays et compétition. Projet séparé, dans un dossier
      voisin de celui-ci (voir `../ibet-web/README.md`)
- [x] **`ibet/stockage/store.py`** : base SQLite des prévisions (`ibet.db`), 2 tables, import du fichier
      `predictions_ouvertes.json` — 4 prévisions, 48 propositions, 24 tranchées, 20 réalisées
- [x] `GET /api/predictions` et `GET /api/predictions/{match_id}` : lecture depuis la base,
      la source n'est jamais réinterrogée pour afficher une prévision
- [x] Écrans `/predictions` (liste + bilan) et `/predictions/:matchId` (attendus, méthode,
      issue, scores probables, échelles par seuil, propositions tranchées)
- [x] **`ibet/prevision/forecast.py`** : émission des prévisions des matchs à venir, mise en fiche du
      résultat de `predict.build` et enregistrement en base. Équipes féminines, de jeunes
      et réserves écartées ; filtres `--league` et `--pays`
- [x] Prévisions classées par journée de coup d'envoi dans l'interface (aujourd'hui,
      demain, après-demain, puis la date en clair), les matchs à venir d'abord
- [x] `ibet/evaluation/verify.py` travaille sur la base (plus seulement sur le fichier JSON), n'examine
      que les fiches mûres (coup d'envoi + 135 min) et groupe les requêtes par journée
- [x] `POST /api/predictions/verifier` + vérification automatique au chargement de
      l'écran des prévisions, avec le compte des fiches en attente de verdict
- [x] Everton - Manchester Utd 2-2 tranché : 6 propositions réalisées sur 12
- [x] Évaluation des **propositions** elles-mêmes dans `backtest` : familles (total, équipe,
      issue, double chance, les deux marquent), calibration par tranche, tranchage par le
      même code que `verify`. Sur 109 matchs : aucun écart significatif, rien à recalibrer
- [x] 12 fiches vérifiées le 7/09 : 144 propositions tranchées, 114 réalisées (79,2 %).
      Annoncé 84,5 % → écart +5,3 pt, dans le même sens que les deux mesures de banc d'essai
- [x] `backtest.tune_offers` : réglage noté sur les propositions et non sur la seule issue,
      seul moyen de juger un paramètre qui porte sur les corners ou les cartons
- [x] `estimation_dispersion` : terme en 1/n ajouté au rapport variance/moyenne (mélange
      Gamma-Poisson dû à l'estimation de lambda sur peu de matchs). Neutre à zéro, vérifié
      strictement identique au modèle précédent
- [x] Deux paramètres ajoutés puis mesurés : `home_edge` (avantage du terrain par équipe)
      et `lambda_shrink` (rétrécissement du nombre attendu). Les deux dégradent ; le second
      significativement (t = +2,2 à +2,7). Laissés en place, désactivés, mesure documentée

## Erreurs déjà rencontrées (à ne pas reproduire)

| Date | Erreur | Cause | Solution appliquée |
|------|--------|-------|---------------------|
| 2026-09-05 | `SSL: CERTIFICATE_VERIFY_FAILED` sur **tous** les hôtes HTTPS (y compris pypi.org) | Avast intercepte le TLS (`SSLKEYLOGFILE=\\.\aswMonFltProxy`) et re-signe avec sa propre racine, inconnue de `certifi`. `pip` passait car il utilise le magasin système. | `ibet/sources/certificats.py` fusionne `certifi` + `C:\ProgramData\Avast Software\Avast\wscert.pem` dans `.certs/bundle.pem` ; `api_client._verify()` lit la variable `CA_BUNDLE`. **Ne pas** désactiver `verify` ni utiliser `--insecure`. |
| 2026-09-05 | `Could not find a suitable TLS CA certificate bundle, invalid path: …\.certsundle.pem` | Chemin Windows écrit dans `.env` via `printf` bash : `\b` interprété comme échappement, backslashes mangés. | Écrire `.env` avec un script Python (`Path.resolve()`), jamais avec `printf`/`echo` en bash. `_verify()` vérifie maintenant `os.path.isfile()` et affiche une erreur claire. |
| 2026-09-05 | Heredoc bash `<<'PYEOF'` : `unexpected EOF while looking for matching '` | Le heredoc contenant du Python volumineux est mal découpé par le shell sous Git Bash. | Utiliser l'outil `Write` pour les fichiers Python, pas les heredocs bash. |
| 2026-09-05 | TheSportsDB ne renvoie que **3 matchs** quelle que soit la date | La clé de test publique `123` (ou `3`) plafonne `eventsday.php`. La clé `1` renvoie `400 Invalid Premium API key`. Endpoints testés : `eventsnextleague`/`eventspastleague` → **1 seul** événement ; `eventsseason` → 5 à 15 ; **`eventsround` → journée complète, non plafonné**. | Mode journée basé sur `eventsround.php`. Ne pas perdre de temps à retester `eventsseason` ou `eventsnextleague` : ils sont plafonnés. |
| 2026-09-05 | `UnicodeEncodeError: 'charmap' codec can't encode 'ő'` sur la C1 | Sortie redirigée vers un pipe/fichier → Python utilise cp1252, incapable d'encoder les noms de clubs (Győri ETO, Kauno Žalgiris, KÍ Klaksvík). En console interactive le bug ne se voit pas. | `_force_utf8_output()` dans `ibet/interfaces/cli.py` : `reconfigure(encoding="utf-8", errors="replace")` sur stdout/stderr. Tester les sorties **en les redirigeant**, pas seulement à l'écran. |
| 2026-09-05 | Tiret cadratin affiché `�` dans la console Windows | Caractères typographiques (`—`, `…`) non encodables en cp1252. | Rendu console strictement ASCII (`-`, `...`). Les caractères non-ASCII des données API sont couverts par le correctif UTF-8 ci-dessus. |
| 2026-09-05 | Estimation de la journée par semaines calendaires → 0 match en C1 | Hypothèse d'une cadence hebdomadaire depuis la journée 1. Faux en C1 : journées 1-3 = tours préliminaires étalés de juillet à octobre et se chevauchant, journées 4-8 = phase de ligue. | Remplacé par une **recherche dichotomique** sur `MAX_ROUND=60`, basée sur la date de fin de journée (croissante avec le n° de journée dans toutes les compétitions couvertes). Validé de septembre 2026 à mai 2027. |
| 2026-09-05 | Heredoc bash : un `\n` écrit dans une chaîne Python devient un vrai saut de ligne → fichier invalide | Un script Python passé en heredoc voit ses échappements réinterprétés avant d'atteindre Python. Rencontré **deux fois** : sur `ibet/interfaces/exporter.py` puis sur ce tableau même. | Pour tout texte contenant des `\n` littéraux ou des backslashes, utiliser `Edit`, jamais un script de remplacement en heredoc. |

| 2026-09-05 | Un en-tête de championnat répété par match, et « Ligue 1 (France) » affiché au-dessus de matchs tunisiens | `render_console` groupait sur le changement de `championnat` seul, en supposant les matchs contigus — alors que `sort_matches` triait par heure avant championnat. Invisible avec TheSportsDB (une seule ligue par appel), flagrant avec Flashscore (215 compétitions le même jour, dont trois « Ligue 1 »). | Tri par `(date, pays, championnat, heure)` et clé de regroupement `(championnat, pays)`. **Leçon : un rendu groupé impose un tri qui rend les groupes contigus, et le nom d'un championnat n'est unique que par pays.** |

| 2026-09-05 | Cartons rouges affichés « indisponible » alors que le code venait d'être corrigé pour les déduire à 0, et `--no-cache` n'y changeait rien | `--no-cache` n'était passé qu'à `get_matches` ; `enrich_with_stats` appelait `get_stats(match)` sans l'argument. Le cache des stats a un TTL de 30 jours : il servait une entrée écrite avant le correctif. | `enrich_with_stats(matches, max_stats, use_cache)`. **Leçon : un drapeau « ignorer le cache » doit être propagé à tous les appels réseau de la commande, sinon il donne une fausse confirmation — on croit tester le nouveau code, on relit l'ancien résultat.** |

| 2026-09-05 | Colonne `url` vide dans l'export juste après avoir ajouté le champ, alors que le code le remplit bien | Le cache stocke les dictionnaires de matchs tels quels. Les entrées écrites avant l'ajout du champ n'ont pas la clé : elles ressortent sans `url` pendant tout le TTL, sans erreur. Même mécanisme que la fausse lecture des cartons rouges plus haut. | `CACHE_SCHEMA` intégré aux trois clés de cache. **Leçon : tout cache de structures sérialisées doit porter une version de schéma, sinon chaque ajout de champ produit des données incomplètes silencieuses jusqu'à expiration — 30 jours pour les statistiques.** |

| 2026-09-05 | `KeyError: 'amicaux'` juste après avoir ajouté ce champ à la forme | `CACHE_SCHEMA` existait déjà mais je ne l'ai pas incrémenté en ajoutant le champ. Aggravé par une incohérence : `get_stats` et `get_form` écrivaient dans le cache même appelés avec `use_cache=False`, alors que `get_matches` ne le faisait pas — un test « sans cache » empoisonnait donc le cache pour l'appel suivant. | `CACHE_SCHEMA = 3`, et écriture de cache alignée sur les trois fonctions (on écrit toujours : le drapeau signifie « ne relis pas », pas « n'écris rien »). **Leçon : le versionnement de schéma ne protège que si on l'incrémente — l'ajout d'un champ et le bump doivent être le même geste.** |

| 2026-09-05 | Prévision absurdement confiante : 84,2 % de victoire extérieure pour Dortmund à Hoffenheim, sur 5 matchs | Maher multiplie attaque et défense. Mesurées sur 4-5 matchs, deux écarts modérés (1,57 et 1,51) se composent en 2,38. Le modèle suppose une saison complète d'observations. | Régularisation des forces vers 1 (`_shrink`, k=6, empirical Bayes). Passe à 45,8 %, cohérent avec un marché. **Leçon : tout modèle qui multiplie des ratios estimés doit les régulariser quand l'échantillon est court, sinon il fabrique de la confiance à partir de bruit.** |
| 2026-09-05 | Les deux équipes d'un même match évaluées sur des périmètres différents (Hoffenheim sur 4 matchs de Bundesliga, Dortmund sur 5 toutes compétitions), puis normalisées par la même référence | `_scope` était appliqué équipe par équipe, chacune décidant seule si elle avait assez de matchs de championnat. | Décision commune : les deux équipes restreintes, ou aucune. **Leçon : un filtre qui alimente une comparaison doit être décidé pour les deux termes ensemble.** |

| 2026-09-05 | Évaluation du modèle inexploitable : 119 matchs sur 120 comptés « sans historique suffisant », alors que les mêmes matchs passaient un par un | Les erreurs TLS intermittentes (antivirus) étaient attrapées avec `except ApiError` et confondues avec un manque de données. Sur des centaines de requêtes enchaînées, elles deviennent fréquentes. | Reprise sur erreur de transport dans `_http_get` (3 tentatives, pause croissante ; les erreurs HTTP ne sont pas réessayées), et ventilation des causes d'exclusion dans `backtest.run`. Résultat : 120 matchs rejoués, **0 écarté**. **Leçon : ne jamais confondre « donnée absente » et « échec de récupération » dans un compteur — le résultat devient ininterprétable, et l'erreur passe pour une propriété des données.** |
| 2026-09-05 | Affirmation « le modèle bat les fréquences de la compétition », fondée sur le seul écart de Brier | Un écart de moyennes sans erreur type ne dit rien. Sur 120 matchs, l'écart de −0,0277 vaut 1,2 erreur type : il n'est pas établi. | Comparaison appariée avec erreur type et ratio affichés, et verdict explicite « indistinguable du hasard » en deçà de 2 erreurs types. **Leçon : un point d'estimation sans mesure de dispersion n'est pas un résultat. Le dire avant que l'utilisateur ne le demande.** |

| 2026-09-05 | Cinq réglages de `shrinkage` (2, 4, 6, 10, 16) donnant exactement le même log-loss, au dix-millième | `Params.shrinkage` était déclaré et documenté, mais jamais transmis : `_maher_lambdas` et `_opponent_adjusted_goals` appelaient `_shrink` sans l'argument et retombaient sur la constante du module. Un paramètre inerte, invisible tant qu'on ne le fait pas varier. | Paramètre propagé jusqu'à `_shrink`. Vérification directe : à échantillon 4, `lambda` passe de 3,17 (k=2) à 2,28 (k=16). **Leçon : un balayage d'hyperparamètres qui rend des scores identiques ne signifie pas « sans effet », mais « non branché » — vérifier qu'un paramètre bouge la sortie avant d'interpréter son absence d'effet.** |

| 2026-09-05 | Champ `kickoff_utc` ajouté aux entrées de forme **sans incrémenter `CACHE_SCHEMA`** — la pondération par ancienneté serait restée silencieusement inactive sur toute entrée déjà en cache | Exactement la faute consignée deux lignes plus haut, répétée le même jour. Le versionnement de schéma ne protège que si l'incrémentation accompagne l'ajout du champ. | `CACHE_SCHEMA = 4`, vérifié : les entrées portent bien `kickoff_utc`. **Leçon : cette table ne suffit pas à empêcher la récidive. Ajouter un champ à une structure mise en cache et incrémenter le schéma doivent être le même commit, relus ensemble.** |

## Décisions techniques prises

- **~~API plutôt que scraping Flashscore.~~ Décision révisée le 2026-09-05** à la demande de
  l'utilisateur. L'analyse initiale (« JS dynamique, scraper HTML fragile ») était fausse sur
  un point décisif : Flashscore ne rend pas ses données en HTML, il les charge depuis un flux
  texte structuré (`16.flashscore.ninja/16/x/feed/`) qu'il suffit de lire — aucun rendu de
  page, aucun navigateur sans tête, un parseur de 40 lignes. Le résultat dépasse les trois API
  sur presque tous les axes : ~1 750 matchs/jour contre 3, statistiques complètes sans clé,
  scores en direct, libellés déjà en français.
  Ce qui reste vrai de l'analyse initiale, et qui est désormais documenté dans le README :
  ce n'est **pas une API publiée** (elle peut changer sans préavis, contrairement aux trois
  autres), et les **CGU interdisent la réutilisation commerciale**. D'où la conservation des
  trois providers API plutôt qu'un remplacement, et le maintien du cache.
- **Flashscore en défaut plutôt que thesportsdb.** Les deux fonctionnent sans inscription,
  mais thesportsdb est plafonné à 3 matchs hors mode journée. Le repli documenté vers
  `football-data` / `api-football` couvre le seul angle mort de Flashscore : les dates
  au-delà de ± 7 jours.
- **Statut lu sur le code détaillé `AC`, pas sur le code global `AB`.** `AB` ne distingue que
  à venir / en cours / terminé : un match reporté ou annulé y apparaît comme « terminé »
  (observé : `AB=3` avec `AC=4`, sans aucun score). Le mapping se fait donc sur `AC`, avec
  repli sur `AB` si le code est inconnu — Flashscore en emploie une cinquantaine.
- **Statistiques mappées par identifiant numérique (`SD`), pas par libellé.** Les codes
  (12 = possession, 13 = tirs cadrés, 16 = corners, 21 = fautes, 22/23 = cartons…) sont
  stables et indépendants de la langue, alors que les libellés changent d'une édition
  nationale du site à l'autre.
- **Barres divergentes plutôt que deux colonnes de chiffres** pour les statistiques. Comparer
  deux longueurs opposées depuis un axe central est immédiat ; comparer 15 et 12 dans deux
  colonnes demande un aller-retour du regard. La barre reste en ASCII (`#`), pour la même
  raison que le reste de l'affichage console. Résolution d'environ 4 % sur 12 caractères :
  47 % et 53 % rendent la même longueur, ce qui est acceptable — les chiffres exacts restent
  affichés de part et d'autre. Une valeur non nulle reçoit toujours au moins un caractère,
  sinon 1 contre 30 paraîtrait égal à 0 ; un total nul (0 - 0) rend une barre vide, distincte
  d'une donnée manquante, qui est listée séparément.
- **Coupure temporelle obligatoire dans l'évaluation.** Le flux `df_hh_` rend les 50 derniers
  matchs d'une équipe *au moment de l'appel*, pas au moment du match étudié. Sans coupure, un
  backtest donnerait au modèle connaissance de l'avenir et un score flatteur et faux. `before`
  est donc propagé jusqu'à `get_form` et `league_baseline`, et la clé de cache l'inclut.
- **Référence de championnat coupée à minuit, pas au coup d'envoi.** Légèrement plus stricte
  (elle écarte aussi les matchs plus tôt le même jour), donc toujours sans fuite, mais
  partagée par tous les matchs d'un même jour : sans cela, chaque match recalculerait dix
  historiques et l'évaluation durerait des heures.
- **Hyperparamètres mesurés, pas choisis à vue.** `backtest.tune()` compare les réglages sur
  les mêmes matchs, avec coupure temporelle, et classe par log-vraisemblance. Retenu :
  `half_life=21`, `shrinkage=10`, `rho=0`. La pondération par ancienneté est adoptée parce que
  **les six demi-vies essayées battent son absence** avec un optimum intérieur — un seul écart
  à 1,6 erreur type n'aurait pas suffi, la forme de la courbe si.
- **Le facteur τ de Dixon-Coles reste désactivé.** Mesuré sans effet (1,0349 contre 1,0353) :
  l'implémenter était utile, l'activer aurait été régler du bruit. Le code est là, `rho=0` le
  neutralise, et une mesure ultérieure sur plus de matchs pourra le rallumer.
- **Prochaine validation à faire sur des matchs postérieurs au réglage.** Les 120 matchs
  d'évaluation recoupent les 197 du réglage : le gain affiché est en partie dans l'échantillon.
- **Modèle publié plutôt que formule maison.** La prévision applique Maher (1982) pour les
  nombres attendus et la loi de Poisson pour les distributions, tous deux cités dans
  `ibet/prevision/predict.py`. La correction de Dixon-Coles sur les petits scores **n'est pas appliquée** :
  son facteur s'estime par maximum de vraisemblance sur plusieurs saisons, que l'échantillon
  disponible ne permet pas. L'omettre est honnête ; l'approximer à vue ne l'aurait pas été.
- **Référence de championnat recalculée, faute de classement accessible.** Tous les points
  d'entrée de classement testés échouent : `to_ts_*`, `ss_1_*`, `tab_*`, `t_ts_*` renvoient un
  corps vide ; `df_to_1_<id>` répond mais dans un autre espace d'identifiants (l'ID Bundesliga
  y renvoie une compétition tchèque) ; la page HTML ne contient pas le tableau (rendu JS) et
  `m.flashscore.fr` est en 404. La référence est donc reconstruite depuis les résultats :
  autres matchs de la compétition le même jour → historiques via `df_hh_` → déduplication par
  identifiant de match. **Ne pas repartir à la chasse à l'endpoint** sans élément nouveau.
- **Correction du niveau des adversaires, signalée par l'utilisateur.** La moyenne brute des
  buts récompense un calendrier facile : elle compte de la même façon trois buts contre le
  dernier et contre le premier. Chaque match est désormais divisé par la force de l'adversaire
  (défense pour les buts marqués, attaque pour les encaissés) — la correction de calendrier
  qu'opère implicitement l'estimation simultanée de Maher, appliquée ici en une passe.
- **Chaque match d'historique crédite les deux équipes**, pas seulement celle dont on lit
  l'historique. Sans cela, seules les équipes jouant le jour choisi étaient notées : 12 sur 18
  en Bundesliga, donc la moitié des adversaires sans force connue et non corrigés. Avec, la
  couverture est complète (18/18, 20/20 en Premier League) sans une requête de plus.
- **L'échantillon de référence couvre toute la compétition, pas les deux équipes du match.**
  Normaliser une équipe par un échantillon où elle pèse la moitié la ramènerait vers 1 : le
  modèle perdrait précisément ce qu'il cherche à mesurer.
- **Amicaux exclus du calcul de prévision** (mais gardés à l'affichage de la forme). Un 8-0 de
  pré-saison contre une équipe amateur déplacerait l'attaque estimée de plusieurs dixièmes.
- **Pas de référence de compétition pour les corners et les tirs cadrés.** Elle exigerait une
  requête par match du championnat (une centaine). Ces grandeurs retombent sur la moyenne
  production/concession, et chaque ligne affiche la méthode employée — le lecteur sait donc
  laquelle des deux il regarde.
- **Forme récente via le flux des confrontations (`df_hh_`), pas via 2×N requêtes.** Ce flux
  porte déjà l'historique complet des deux équipes (une cinquantaine de matchs chacune) :
  une seule requête suffit pour les résultats. Seules les moyennes détaillées (tirs, corners,
  possession) exigent une requête par match d'historique, d'où leur activation explicite par
  `--stats` plutôt que par défaut.
- **Amicaux comptés à part plutôt qu'exclus.** L'utilisateur demande « les N derniers
  matchs » : les retirer en silence fausserait le compte autant que les inclure sans le dire.
  Ils sont donc gardés, marqués `(A)`, et dénombrés sous le bilan. Détection par le code
  compétition (`KI` commençant par `AMI`) doublée du libellé, car le code seul n'est pas
  garanti sur toutes les compétitions.
- **`--team` renvoie l'équipe première ; féminines, jeunes et réserves écartées.** Demandé par
  l'utilisateur après que « Monchengladbach » eut ramené aussi « B. Monchengladbach II ».
  Détection par suffixe (` F`, ` II`, ` B`, ` 2`, ` -17`, `U19`) et par nom de compétition
  (« - Femmes »), motifs relevés sur les 3 518 équipes d'un jour complet sans faux positif :
  tous les ` B` et ` 2` observés sont bien des réserves (FC Porto B, Columbus Crew 2).
  Conservées si la recherche les vise explicitement, ou avec `--all-teams`.
- **Aucune exclusion silencieuse.** Le nombre de matchs écartés est toujours affiché, et
  lorsque la recherche ne correspond qu'à des déclinaisons, le message le dit plutôt que
  d'annoncer « aucune équipe ne correspond » — ce qui se contredisait avec la note juste
  au-dessus.
- **`--team` filtré côté client, après récupération.** Aucune des quatre sources ne sait
  filtrer par équipe côté serveur, et le filtre doit valoir aussi pour le mode journée
  TheSportsDB : il s'applique donc dans `main()`, après `collect()`, sur la liste normalisée.
- **Un seul appel réseau par jour demandé.** Le flux découpe ses journées à minuit dans le
  fuseau passé en paramètre : on lui transmet le décalage réel de l'utilisateur, puis on
  filtre sur la date locale calculée nous-mêmes. Cela absorbe à la fois le débordement du flux
  (~1 h de part et d'autre de minuit) et les fuseaux à la demi-heure, sans avoir à récupérer
  les journées voisines.
- **Trois providers derrière une même interface** plutôt qu'un seul : `PROVIDERS` mappe un nom
  vers une fonction `(date, tz) -> list[Match]`, chacune normalisant vers le même dict. Permet
  de basculer de source sans toucher à l'export ni à l'affichage, et de contourner un quota
  épuisé en changeant une variable d'environnement.
- **`thesportsdb` en défaut** parce qu'il fonctionne sans inscription : le projet tourne dès le
  `git clone`. Mais le README indique explicitement que `football-data` est le choix pour un
  usage réel.
- **Cache JSON local, TTL 1 h**, clé `provider|date|fuseau` hashée en SHA-256. Indispensable
  avec les 100 req/jour d'API-Football. Échec d'écriture non bloquant (le cache est un confort,
  pas une dépendance).
- **Heures converties en heure locale** via `zoneinfo` (+ `tzdata` sur Windows), fuseau
  configurable. Le `kickoff_utc` brut est conservé dans l'export pour ne rien perdre.
- **CSV en UTF-8 avec BOM** (`utf-8-sig`) pour qu'Excel affiche correctement les accents.
- **Statuts normalisés en 5 valeurs** (`A venir`, `En cours`, `Termine`, `Reporte`, `Inconnu`)
  à partir des vocabulaires différents des 3 API.
- **API-Football renvoie HTTP 200 même en erreur** (clé invalide, quota) : le champ `errors` du
  corps JSON est vérifié explicitement, sinon les erreurs passent inaperçues.

- **Mode journée plutôt qu'une clé payante.** L'endpoint `eventsround.php` n'est pas plafonné :
  il rend le provider gratuit réellement utilisable sur les 6 grandes compétitions
  européennes, sans inscription. C'est un usage normal de l'API publique, pas un contournement
  de protection.
- **Recherche dichotomique plutôt qu'estimation calendaire** pour trouver la journée d'une
  date : ~6 requêtes mises en cache, et surtout correct sur les compétitions à cadence
  irrégulière (voir le tableau d'erreurs).
- **Sortie console ASCII + stdout forcé en UTF-8.** Les deux sont nécessaires : l'ASCII pour
  mes propres libellés, l'UTF-8 pour les noms de clubs venant de l'API.

- **Statistiques : API-Football, pas Flashscore.** Demande explicite de l'utilisateur de
  scraper flashscore.fr, refusée deux fois. Leurs CGU interdisent la récupération automatisée
  et il faudrait contourner leurs protections anti-bot. API-Football fournit exactement les
  champs demandés (corners, tirs cadrés, cartons, arbitre, fautes) gratuitement.
- **Pont `idAPIfootball`.** Chaque événement TheSportsDB porte l'identifiant API-Football du
  même match. On garde donc la recherche de matchs sans clé (mode journée, non plafonné) et on
  n'utilise le quota API-Football que pour les statistiques. Une requête `/fixtures?id=` suffit
  pour l'arbitre **et** les stats — `/fixtures/statistics` en aurait demandé une seconde.
- **Dégradation explicite plutôt que champs vides.** Sans clé API-Football, la sortie affiche
  les tirs et liste nommément ce qui manque, avec la marche à suivre. Un tableau à moitié vide
  sans explication ferait perdre du temps.
- **Cache des stats à 30 jours** : un match terminé ne change plus.
- **`tests/test_stats.py` sans réseau.** Le chemin API-Football ne peut pas être testé sans clé ; le
  parseur est donc validé sur une réponse conforme au format v3 documenté (27 assertions).

## Les quatorze critères de décision (2026-09-07)

- [x] **`ibet/prevision/context.py`** : les 14 critères autour du modèle — style de jeu, forme et rang,
      système et effectif, adversaires de style comparable, confrontations directes, enjeu,
      domicile/extérieur, fatigue, motivation, météo, xG, arbitre, cotes, taille d'échantillon
- [x] Quatre flux Flashscore nouveaux, tous facultatifs et rendant `{}` plutôt que de lever :
      `df_sui_` (stade, ville, capacité, **arbitre**, affluence), `df_to_` (**classement**
      complet avec derniers résultats et **prochain match**), `df_li_` (**compositions**,
      système, absents). L'arbitre était noté « non exposé par les flux testés (`dfi_`,
      `df_dt_`) » dans la version précédente : il est dans `df_sui_`, à condition de couper
      aussi sur le séparateur de bloc — collé au fil des événements, il était lu comme la fin
      de l'enregistrement précédent
- [x] **Météo** via Open-Meteo (gratuit, sans clé, sans inscription) : géocodage de la ville du
      stade puis prévision horaire au coup d'envoi
- [x] **Cotes 1X2 via betexplorer.com** (même groupe que Flashscore, une trentaine
      d'opérateurs). Point décisif : **les identifiants de match sont les mêmes** — aucun
      rapprochement par nom d'équipe, une requête par journée pour tous les matchs. Cotes
      moyennes (le consensus, à quoi comparer) et meilleures (ce qu'on touche, donc la valeur
      espérée `p × cote − 1`). Le flux Flashscore `df_od_` répond vide sur les quatre éditions
      essayées (fr, com, co.uk, livescore.in)
- [x] **Blessures et suspensions via sportsgambler.com**, 29 compétitions, avec motif
      (blessé / incertain / suspendu) et poste. Cette source-ci **ne partage pas** les
      identifiants : rapprochement par nom, volontairement strict et vérifié unique
      (`_same_team`) — « Manchester Utd » se rapproche de « Manchester United », pas de
      « Manchester City », et « Manchester » seul ne se rapproche de rien
- [x] **Composition probable** : aucune source gratuite n'en publie, elle est donc déduite de la
      composition **réelle** des 5 derniers matchs. Le flux `df_li_` porte le champ `LL`, qui
      est la **place dans le dispositif** (1 = gardien, puis la ligne défensive) et non l'ordre
      d'affichage, alphabétique : le onze est donc reconstitué **place par place**, celui qui
      l'occupe le plus souvent et qui est disponible. Affichée en console sous les critères,
      exposée par `GET /api/predictions/{id}/composition`
- [x] Rapprochement des joueurs entre les deux sources (« Cunha M. » / « Matheus Cunha ») par
      nom de famille **et initiale du prénom** : le nom seul confondait trois homonymes d'un
      même effectif, et une seule blessure les écartait tous les trois. Défaut trouvé par le
      test, pas en production
- [x] Absences **pondérées par poste** : une absence en attaque retire des buts à son équipe,
      une absence en défense en donne à l'adversaire, un milieu compte moitié pour chaque ; un
      joueur douteux compte pour moitié
- [x] `STAT_EXTRA` : 13 statistiques de plus lues dans la **même requête** — **xG**, xGOT,
      grosses occasions, tirs dans la surface, touches dans la surface adverse, passes,
      passes dans le dernier tiers, passes longues, centres, duels, tacles, hors-jeu, arrêts.
      Absentes de `STAT_ORDER`, donc de l'affichage et de l'export : 13 lignes de plus par
      match que personne ne lit une par une
- [x] `stat_number` lit le nombre de tête : le flux écrit les taux de réussite
      « 86% (450/526) », et c'est le taux qui compare deux équipes
- [x] `BASELINE_STAT_FIELDS` inclut `xg` : les buts attendus passent par **le même modèle que
      les buts** (moyenne de la compétition, forces d'attaque et de défense, régularisation),
      ce qui en fait une seconde estimation de la même quantité — mélangée, pas composée
- [x] `predict.build` en **deux phases** : `_estimate` produit les nombres attendus, le
      contexte s'intercale, puis les probabilités en sont tirées. Sans cette séparation, les
      critères 5 et 11 (qui comparent au nombre attendu) et les corrections (qui s'y
      appliquent) auraient demandé de calculer les lambdas deux fois
- [x] Trois emplacements distincts, non interchangeables : le critère 4 repondère l'historique
      **avant** la moyenne, le critère 11 **mélange** deux estimations, les autres
      **multiplient** le nombre attendu
- [x] Corrections **plafonnées par grandeur** (`PLAFOND`) : 14 facteurs à 3 % dans le même sens
      feraient 1,5, et aucun des 14 ne le voulait
- [x] `store.cotes` : un relevé horodaté par appel, **jamais écrasé** — un mouvement de ligne
      n'existe que dans la différence entre deux relevés
- [x] API : `GET /api/criteres` (catalogue), `GET /api/predictions/{id}/options` (les options
      de paris regroupées par marché, avec le côté mis en avant),
      `GET /api/predictions/{id}/contexte`, `GET`/`POST /api/predictions/{id}/cotes`
- [x] `--sans-contexte` (CLI et `ibet/prevision/forecast.py`) et `--backtest --contexte` : les deux variantes
      sont comparables **sur les mêmes matchs**, seule façon de savoir si le contexte apporte
      quelque chose
- [x] `context.collecter(retrospectif=True)` coupe le **classement** (celui d'aujourd'hui
      contient le résultat cherché) et l'**arbitre** (publié après coup) : les lire sur un
      match passé donnerait une mesure flatteuse et fausse
- [x] `tests/test_stats.py` : 9 sections de plus, sans réseau — plafonnement des corrections,
      probabilités implicites, similarité de profil, seuils météo, options de paris, et le
      fait qu'un critère indisponible ne corrige rien

### Ce que la mesure a donné

80 matchs de huit grands championnats, deux mesures (issue 1X2 sur 80 matchs,
propositions sur 12 898 observations), coupure temporelle, classement et arbitre coupés.

- **Contrôle de neutralité** : « sans contexte » et « tous les critères à zéro » donnent le
  même log-loss au dix-millième (1,0417 et 0,5659). Sans cette garantie, aucune comparaison
  ne voudrait rien dire — on ne saurait pas si un écart vient du critère ou du fait d'avoir
  branché la machinerie.
- **Retenus** : `style = 1` (t = −7,2), `xg = 0,25` (t = −4,0), `fatigue = 1` (t = −3,8).
  Ensemble : **−0,00279 ± 0,00032, t = −8,7** — le seul résultat du projet à sortir
  franchement du bruit, les campagnes précédentes plafonnant à |t| ≈ 2. Les trois
  contributions sont additives : retirer l'un coûte exactement son apport isolé.
- **Écartés parce qu'ils dégradent** : `meteo` (t = +6,1), `confrontations` (t = +4,1 puis
  +5,3 — une pente), `similarite` (pente), `forme`. Les confrontations directes *améliorent*
  l'issue (t = −1,0) et dégradent les propositions : la mesure qui les flatte porte sur 80
  observations, celle qui les condamne sur 12 898. C'est la seconde qui tranche — et c'est
  le piège que cette campagne aura le mieux illustré.
- **Non mesurables a posteriori** : `effectif`, `arbitre`, `motivation` (sources d'état
  courant, ou publiées après coup) et `enjeu` (aucun match couperet dans l'échantillon).
  Restés à zéro : quatre critères plausibles viennent de dégrader la prévision, les activer
  sans mesure serait parier. `POIDS_CONTEXTE` dans le `.env` permet de les essayer, et les
  fiches enregistrant leur contexte à l'émission, ils deviendront mesurables prospectivement.
- **Garde-fou sur les écarts au marché** : au-delà de 25 % d'espérance de gain, la fiche
  signale que l'écart est démesuré. Un tel rendement n'existe pas sur l'issue d'un match ;
  il mesure ce qui manque au modèle, typiquement un échantillon de trois journées.

### Décisions

- **Un critère absent vaut neutre, jamais zéro.** Trois états sont distingués à l'affichage
  (`ok`, `~`, `--`) parce que « pas encore publié » et « mesuré, sans effet » mènent au même
  multiplicateur et ne disent pas la même chose au lecteur.
- **Aucune correction sans affichage.** Chaque critère porte son multiplicateur exact, et la
  fiche conserve `lambda_avant_contexte`. Une correction qu'on ne pourrait pas retrouver après
  coup serait indéfendable.
- **Pas de liste de derbys écrite à la main.** Elles vieillissent mal et n'existent que pour
  les championnats connus. La rivalité est mesurée là où elle laisse une trace : les cartons
  que ces deux équipes se sont déjà donnés, comparés à la moyenne de leur compétition.
- **Rapprocher par identifiant plutôt que par nom, quand c'est possible.** BetExplorer partage
  les identifiants de Flashscore : la jointure est exacte. Sportsgambler non : la jointure est
  heuristique, donc strictement bornée, et en cas de doute le critère se déclare partiel. Un
  critère indisponible est sans conséquence ; un critère qui attribue à une équipe les blessés
  d'une autre fausserait la prévision sans que rien ne le signale.
- **Les cotes ne corrigent rien.** Mélanger le modèle au marché améliorerait mécaniquement
  toute mesure de calibration — le marché est bien calibré — mais reviendrait à le recopier en
  croyant le prévoir. Seul l'écart est rendu.
- **Le côté du marché mis en avant est une présentation, pas un calcul.** « Plus de 9,5 » et
  « moins de 9,5 » sont les deux faces de la même probabilité ; les deux restent dans la fiche.
- **Import tardif de `context` dans `predict`.** `context` lit le réseau ; l'importer en tête
  ferait de `predict`, aujourd'hui calculable hors ligne, un module qui tire tout le client
  HTTP derrière lui.

## Prochaine étape

1. Créer une clé gratuite sur https://dashboard.api-football.com/register, la mettre dans
   `.env` (`API_FOOTBALL_KEY=…`), puis vérifier les statistiques complètes :
   `python -m ibet --league "serie a" --round 1 --date 2026-08-22 --stats --max-stats 3`
   — attendu : corners, fautes, cartons et arbitre renseignés, plus de mention « indisponible ».
2. Créer aussi une clé sur https://www.football-data.org/client/register, passer
   `PROVIDER=football-data`, puis vérifier :
   `python -m ibet --date 2026-09-12 --league "Ligue 1"`.
2. Une fois validé, envisager : plage de dates (`--from` / `--to`), filtre par équipe,
   classement du championnat, et éventuellement les cotes (disponibles chez API-Football).
   Côté Flashscore, deux pistes repérées mais non implémentées : un filtre par pays
   (`--country`), utile maintenant que la source est mondiale et que les noms de championnats
   se répètent d'un pays à l'autre ; et les statistiques supplémentaires déjà présentes dans
   le flux `df_st_` (xG `SD=432`, arrêts du gardien `SD=19`, hors-jeu `SD=17`), qui
   demanderaient d'élargir `STAT_ORDER` et donc les colonnes d'export.
   L'arbitre, lui, n'est pas exposé par les flux testés (`dfi_`, `df_dt_` : 404 ou vides).
3. Éventuellement étendre `TSDB_LEAGUES` (Liga Portugal, Eredivisie, Ligue Europa) — il suffit
   d'ajouter l'identifiant TheSportsDB dans le dictionnaire.
