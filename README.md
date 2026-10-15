# Repère

Une application locale pour importer des annonces immobilières, comparer les biens et simuler leur financement. Elle fonctionne sans Excel et sans clé Gemini obligatoire.

Le paquet contient l'application, PostgreSQL via Docker Compose, une extension Chrome/Edge et le code source. La base est vide au premier démarrage. Les captures d'écran de `tests/screenshots` utilisent uniquement des annonces fictives.

## Démarrer

1. Installez et ouvrez [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) (conteneurs Linux), ou Docker et Docker Compose sous Linux/macOS.
2. Récupérez le dossier depuis GitHub (`git clone https://github.com/Dicalius/Repere.git`) dans un emplacement permanent.
3. Dans ce dossier :

```sh
docker compose up -d
docker compose exec app repere token
```

4. Ouvrez [http://127.0.0.1:8765](http://127.0.0.1:8765) et collez le **code de connexion** affiché par la seconde commande.

Aucun script ni fichier `.env` n'est nécessaire. `.env` reste facultatif pour la clé Gemini et quelques réglages (voir `.env.example`).

Pour arrêter : `docker compose stop` (les données sont conservées). Pour relancer : `docker compose up -d`.

### Base de données sans mot de passe à gérer

L'application se connecte à PostgreSQL par un socket Unix partagé entre les deux conteneurs (volume `repere_pg_socket`), avec l'authentification locale de l'image officielle. Il n'y a donc aucun mot de passe à conserver dans `.env` ni à resynchroniser. Les connexions réseau à PostgreSQL restent protégées par mot de passe ; le port n'est pas publié sur le PC. Seul le conteneur `app` monte ce socket.

## Mettre à jour

À chaque push sur `main` qui modifie l'application (`app/`, `config/`, `docker/`, `Dockerfile`, `requirements.txt`), le workflow **Image Repère** construit l'image et la publie sur `ghcr.io/dicalius/repere` (étiquettes `latest` et SHA du commit). Le workflow **Tests Repère** tourne en parallèle, sans bloquer l'image.

Quand le workflow est vert, depuis le dossier à jour (`git pull`) :

```sh
docker compose pull app
docker compose up -d --no-deps app
```

Seul le conteneur `app` est remplacé : PostgreSQL, ses volumes et `.env` ne sont pas touchés. `docker compose exec app repere version` et **Paramètres** affichent le commit de l'image lancée.

- **Extension modifiée** : cliquez sur **Recharger** dans `chrome://extensions` après le `git pull`.
- **Revenir à une image précédente** : `REPERE_TAG=<SHA complet du commit>` dans `.env`, puis les deux commandes ci-dessus. Videz la ligne pour revenir à `latest`.
- **Construire l'image sur le PC** (sans GitHub) : `docker build -t ghcr.io/dicalius/repere:latest .`, puis `docker compose up -d --no-deps app`.

## Importer une annonce facilement

### Depuis l'extension

Cette méthode utilise la page déjà ouverte dans votre navigateur. Elle convient notamment aux annonces Jinka ou aux pages accessibles grâce à votre session.

1. Ouvrez `chrome://extensions` dans Chrome ou `edge://extensions` dans Edge.
2. Activez **Mode développeur**, puis **Charger l'extension non empaquetée**.
3. Sélectionnez le dossier **`extension` de ce paquet Repère**. Il s'agit de la nouvelle extension ; désactivez l'ancienne pour éviter de confondre les boutons.
4. Épinglez l'extension. Dans **Connexion au comparateur**, enregistrez le code de l'application. Vous pouvez le copier depuis **Repère → Paramètres**.
5. Ouvrez une annonce complète, attendez son chargement et dépliez si besoin sa description. Cliquez sur **Lire l'annonce ouverte**.
6. Consultez le texte, le HTML utile et les métadonnées. **Vérifier les champs localement** affiche un aperçu sans enregistrer de fiche et sans appel Gemini.
7. Cliquez sur **Ajouter à Repère**, puis ouvrez la fiche pour vérifier les champs et la valider.

La capture est envoyée une seule fois au serveur local. Le serveur analyse directement son contenu : **il ne recharge pas l'URL de l'annonce**. Si vous activez Gemini, il reçoit le contenu capturé, sans aller chercher une autre annonce sur le web.

Vous pouvez sélectionner un passage avant la capture ou cocher **Utiliser seulement ce texte**. Modifier le texte dans l'extension active ce mode : HTML et métadonnées sont alors exclus de l'envoi pour respecter votre sélection. Désactivez cette option seulement si vous voulez de nouveau inclure les métadonnées d'origine.

Gardez la fenêtre de l'extension ouverte pendant l'envoi. **Exporter la capture** conserve un JSON réimportable pour diagnostiquer un cas difficile.

### Depuis l'application

**Ajouter une annonce** propose :

- **Lien public** : téléchargement HTML côté serveur, puis analyse locale. Les pages nécessitant JavaScript ou une session peuvent être incomplètes ; utilisez alors l'extension.
- **Texte / HTML / fichier** : texte collé, HTML copié ou fichier `.txt`, `.html`, `.htm`, capture JSON Repère, bloc JSON-LD.
- **Saisie manuelle**, depuis Mes annonces, pour conserver aussi les biens difficiles à extraire.

Il n'y a pas d'import automatique du classeur Excel dans cette version. Les captures JSON au format reconnu se réimportent individuellement ; la sauvegarde complète Repère se restaure depuis Paramètres.

Une URL déjà connue met à jour sa fiche et ajoute une capture. Les paramètres de suivi usuels, dont `alert_id` sur une annonce Jinka, sont retirés. Deux URLs différentes ne sont pas automatiquement fusionnées, même si elles concernent le même logement.

## Vérifier les informations

Les extractions combinent JSON-LD, microdonnées, métadonnées, HTML, texte visible et texte principal. **Gemini est un complément facultatif.**

Dans une fiche, **Voir les sources et les diagnostics** affiche les valeurs proposées, le parseur et l'extrait source. Un prix contradictoire reste à choisir : utilisez le bouton **Utiliser** ou saisissez la bonne valeur. Un champ absent reste vide ; il ne devient pas artificiellement zéro.

Les champs corrigés manuellement sont protégés lors des prochaines captures. La section **Champs corrigés manuellement** permet de rendre un champ à l'extraction. Le prix, la surface et la ville doivent être vérifiés avant une comparaison. **Valider la fiche** marque votre vérification, sans certifier les informations du vendeur.

Les captures restent en base et peuvent être réanalysées après une modification des règles. Elles n'expirent pas au bout de sept jours. La suppression d'une annonce supprime ses captures, mais conserve les scénarios enregistrés.

Pour ajouter une règle CSS à un site ou brancher un autre parseur : [docs/PARSEURS.md](docs/PARSEURS.md).

## Repères sur l'annonce

Chaque carte et chaque fiche affichent quelques repères, aussi repris dans **Comparer** et dans la fenêtre de l'extension après une capture :

- **Ancienneté** : date de première publication lue dans la capture. Elle vient des données de l'annonce sur Leboncoin (avec la date de modification ou de remontée), de `datePosted` dans les données structurées, ou du texte (« publiée le 12/09 », « mise en ligne il y a 3 jours »). Corrigez-la dans la fiche si besoin. Sans date, la fiche indique depuis quand Repère la suit.
- **Prix relevé** : chaque capture enregistre le prix, ou le loyer, affiché ce jour-là. Recapturer la même annonce plus tard montre la baisse ou la hausse depuis le premier relevé. Repère ne surveille pas les sites : il ne connaît que les prix capturés. Un relevé erroné (mauvaise extraction) se retire depuis la fiche ; une réanalyse n'ajoute pas de relevé. Lors de la mise à jour vers la version 1.6, les captures existantes servent de premiers relevés.
- **Marché local** : pour un achat, le prix au m² est comparé au prix médian des ventes réelles de la commune sur cinq ans ([Statistiques DVF](https://www.data.gouv.fr/datasets/statistiques-dvf/), appartements ou maisons). Pour une location, le loyer au m² charges comprises est comparé au loyer de référence de la commune ([Carte des loyers](https://www.data.gouv.fr/datasets/693aa2feed1bf4da603faa49/), logements non meublés, avec sa fourchette). Paris, Lyon et Marseille sont comparés par arrondissement. C'est un repère, pas une estimation du bien.
- **DPE et location** : calendrier d'interdiction à la location (G depuis 2025, F en 2028, E en 2034 en métropole) et gel des loyers des logements F et G.
- **Même bien probable** : une autre fiche de Repère avec le même lieu, la même surface et la même consommation DPE (ou les mêmes pièces, lettres DPE/GES et étage), par exemple une republication ou une autre agence.

Pour le marché local, Repère envoie le code postal et la ville à `geo.api.gouv.fr` pour obtenir le code de la commune, puis ce code à l'API de `data.gouv.fr`. Ni le prix, ni l'adresse, ni l'URL de l'annonce ne sont envoyés. `MARKET_LOOKUP=0` dans `.env` désactive ces appels.

## Comparer et simuler

Dans **Mon budget**, renseignez vos revenus mensuels avant impôt, votre épargne, la réserve à conserver, les autres crédits et vos hypothèses de financement. Ces données commencent vides lorsque nécessaires : aucune situation personnelle n'est présumée.

Dans **Mes annonces**, ajoutez notes, favoris et statut de suivi ; triez par ancienneté en ligne ou par baisse de prix ; puis cochez jusqu'à quatre biens pour les comparer. La comparaison utilise les mêmes hypothèses de profil pour tous les biens. Les scénarios détaillés restent accessibles depuis **Simuler**.

Deux modes sont proposés :

- **Apport → mensualité** : calculer les échéances et le coût du financement à partir de votre apport.
- **Mensualité → apport** : calculer l'apport nécessaire pour ne dépasser la mensualité cible à aucune phase du remboursement, assurance comprise.

La simulation tient compte du prix, des frais d'acquisition, des travaux, du parking supplémentaire et des autres frais saisis. Des prêts complémentaires peuvent être ajoutés avec leur montant, leur taux, leur durée et leur différé de capital. Les phases de remboursement, intérêts, assurance et capital restant dû sont affichés. Vous pouvez enregistrer plusieurs scénarios et exporter un échéancier CSV.

### Hypothèses précises

Le prêt principal est amortissable à taux nominal annuel constant, divisé par douze. L'assurance est calculée sur le **capital initial**, avec une cotisation constante. Un taux nul est accepté.

Pour un prêt complémentaire, la durée de remboursement **s'ajoute au différé**. Pendant le différé de capital, les intérêts et l'assurance restent payés. Il n'y a ni capitalisation d'intérêts ni lissage automatique entre prêts. Un éventuel PTZ peut être représenté par un prêt à taux nul, après vérification externe de ses conditions et de l'éligibilité.

La mensualité cible concerne **les crédits du projet et leurs assurances**. Les charges, la taxe foncière et l'entretien s'ajoutent au budget mensuel lorsqu'ils sont renseignés. Une charge inconnue rend le budget partiel ; énergie, alimentation et autres dépenses de vie ne sont pas incluses. Les crédits existants interviennent dans le taux d'effort et le reste mensuel calculé, pas dans le financement du bien.

Les valeurs initiales — 25 ans, taux nominal de 3,5 %, assurance de 0,3 %, frais d'acquisition de 8 % dans l'ancien ou inconnu et 3 % dans le neuf — sont **des exemples modifiables, pas des taux de marché ni des devis**. Saisir un montant exact de frais remplace le pourcentage. Le seuil d'effort de 35 % est un repère modifiable, pas une décision bancaire. L'application ne calcule pas automatiquement les aides, la fiscalité ou le TAEG réglementaire.

Références pour vérifier votre financement : [HCSF — crédits immobiliers](https://www.economie.gouv.fr/hcsf/mesures/mesure-relative-loctroi-de-credits-immobiliers), [Service Public — PTZ](https://www.service-public.gouv.fr/particuliers/vosdroits/F10871).

## Activer Gemini, facultativement

Renseignez votre clé dans `.env`, jamais dans l'extension :

```dotenv
GEMINI_API_KEY=votre_cle
GEMINI_MODEL=gemini-3.5-flash-lite
```

Puis appliquez la configuration :

```sh
docker compose up -d --force-recreate app
```

Le modèle est configurable : utilisez un modèle disponible pour votre compte. La compatibilité du modèle et de la clé n'a pas été vérifiée avec votre compte dans cet environnement.

Chaque appel demande une sortie structurée et des citations issues de la capture. Les citations et les valeurs numériques sont contrôlées ; les divergences restent à valider. Une citation est une trace vérifiable, pas une garantie que le modèle a compris son contexte.

Une erreur de clé, de quota, de modèle ou de réseau devient un avertissement dans la fiche. **Les données trouvées localement sont conservées**. Il n'y a ni boucle de tentatives automatiques ni recherche web. Le profil financier n'est pas envoyé à Gemini ; le contenu de l'annonce est envoyé seulement lorsque vous activez cette option.

## Données et sauvegardes

Docker Compose crée deux services : `app` et `db`. Les annonces, captures, corrections, profil et scénarios sont conservés dans PostgreSQL, volume `repere_postgres_data`. Le code de connexion est conservé dans `repere_app_data`.

L'interface est publiée uniquement sur `127.0.0.1:8765`. PostgreSQL n'est pas exposé sur un port du PC. Cette configuration est prévue pour un usage local personnel ; le mode multi-utilisateur, l'accès réseau et l'hébergement public ne sont pas configurés.

**Paramètres → Télécharger une sauvegarde** exporte un JSON contenant vos données et captures, sans le code de connexion ni la clé Gemini. Conservez ce fichier avec vos documents privés. L'arrêt ou le remplacement du conteneur conserve les volumes ; supprimer les volumes Docker efface leurs données. Ne lancez pas `docker compose down -v` pour un simple arrêt.

**Restaurer un fichier** ajoute les annonces et scénarios absents. Les fiches déjà présentes et le profil déjà enregistré sont conservés. Une sauvegarde mal formée est rejetée sans restauration partielle. La restauration par l'interface accepte actuellement jusqu'à 20 Mo ; pour une base plus volumineuse, conservez une sauvegarde PostgreSQL et les volumes.

Pour une sauvegarde PostgreSQL sans problème d'encodage lié à la redirection PowerShell :

```sh
docker compose exec -T db pg_dump -U repere -d repere -Fc -f /tmp/repere.dump
docker compose cp db:/tmp/repere.dump ./repere.dump
```

Conservez également `config/parsers.json` (et `.env` si vous y avez mis une clé Gemini). Le code de connexion peut être récupéré séparément avec la commande de démarrage. Aucune migration automatique d'une future version de schéma n'est fournie dans cette première version.

## En cas de problème

| Symptôme | Action |
| --- | --- |
| Port 8765 occupé | Arrêtez l'ancien serveur Excel ou un autre Repère déjà lancé. |
| Docker indisponible | Ouvrez Docker Desktop, attendez son démarrage, puis `docker compose up -d`. |
| Nouvelle version non installée | Vérifiez que le workflow **Image Repère** est vert, puis `docker compose pull app` et `docker compose up -d --no-deps app`. `repere version` doit afficher le nouveau commit. |
| `pull` refusé (`denied`, `unauthorized`) | Le paquet `repere` est privé sur GitHub : passez-le en public (*Packages → repere → Package settings → Change visibility*) ou connectez Docker avec `docker login ghcr.io`. |
| Code refusé | Récupérez le code avec `docker compose exec app repere token`, puis remplacez-le dans l'application et l'extension. |
| URL bloquée, page vide ou vérification humaine | Ouvrez l'annonce dans votre navigateur habituel, puis utilisez l'extension. |
| Prix absent | Vérifiez les conflits dans les diagnostics ; ouvrez la description complète ; essayez HTML, métadonnées ou une règle CSS ciblée. La correction manuelle est conservée. |
| Complément Gemini indisponible | Consultez l'avertissement de la fiche, contrôlez clé et modèle. Les extractions locales restent utilisables. |
| Extension modifiée mais ancien comportement | Cliquez sur Recharger dans la page des extensions, puis recapturez l'annonce. |

Pour consulter l'état et les erreurs :

```sh
docker compose ps
docker compose logs --tail 80 app db
```

## Développement et vérifications

Les dépendances de production sont figées dans `requirements.txt`. L'interface n'utilise ni étape npm de compilation ni service externe de polices ou de scripts.

Pour lancer les tests Python avec Python 3.12, depuis le dossier `repere`, dans un environnement virtuel activé :

```sh
python -m pip install -r tests/requirements.txt
python -m pytest tests -q
```

Les tests API forcent une base SQLite temporaire, même si `DATABASE_URL` existe dans l'environnement. Le parcours navigateur lance son propre serveur temporaire sur le port 8877 :

```sh
python -m playwright install chromium
python tests/browser_checks.py
```

Pour les tests de l'extension, avec Node.js compatible avec jsdom 30 :

```sh
npm install --prefix tests
npm test --prefix tests
```

Le bilan réellement exécuté, ainsi que les validations restant à faire sur Docker et Windows, figure dans [docs/VALIDATION.md](docs/VALIDATION.md).

Pour un lancement de développement hors Docker, `uvicorn app.main:app --host 127.0.0.1 --port 8765` utilise SQLite si `DATABASE_URL` est absent. La configuration de production fournie utilise PostgreSQL.
