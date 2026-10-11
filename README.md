# Repère

Une application locale pour importer des annonces immobilières, comparer les biens et simuler leur financement. Elle fonctionne sans Excel et sans clé Gemini obligatoire.

Le paquet contient l'application, PostgreSQL via Docker Compose, une extension Chrome/Edge et le code source. La base est vide au premier démarrage. Les captures d'écran de `tests/screenshots` utilisent uniquement des annonces fictives.

## Démarrer sous Windows

1. Installez et ouvrez [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) avec les conteneurs Linux.
2. Arrêtez l'ancien serveur Excel avec `Ctrl+C` dans sa fenêtre : il utilisait lui aussi le port **8765**.
3. Extrayez **tout le dossier `repere`** dans un emplacement permanent, par exemple `Documents\Repere`. Ne lancez pas les fichiers depuis l'archive ZIP.
4. Double-cliquez sur **`DEMARRER.bat`**. Le premier lancement télécharge les images et installe les dépendances ; une connexion Internet est nécessaire pour cette préparation.
5. Copiez le **code de connexion** affiché dans la fenêtre. Le navigateur ouvre [http://127.0.0.1:8765](http://127.0.0.1:8765). Collez le code pour entrer.

Le lanceur crée automatiquement `.env` et un mot de passe de base de données aléatoire. Il conserve une configuration existante. Le code de connexion est généré séparément et conservé dans un volume Docker. Python et Node.js ne sont pas nécessaires sur le PC pour utiliser l'application.

Pour retrouver le code, ouvrez un terminal dans le dossier `repere` :

```powershell
docker compose exec -T app python -m app.manage token
```

**`ARRETER.bat`** arrête les deux services sans supprimer les données. Relancez `DEMARRER.bat` pour reprendre.

### Linux ou macOS

Avec Docker et Docker Compose disponibles, ouvrez un terminal dans `repere` :

```sh
sh demarrer.sh
```

Le script crée `.env` si nécessaire, démarre les services puis affiche le code et l'adresse. Pour arrêter : `docker compose stop`.

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

## Comparer et simuler

Dans **Mon budget**, renseignez vos revenus mensuels avant impôt, votre épargne, la réserve à conserver, les autres crédits et vos hypothèses de financement. Ces données commencent vides lorsque nécessaires : aucune situation personnelle n'est présumée.

Dans **Mes annonces**, ajoutez notes, favoris et statut de suivi, puis cochez jusqu'à quatre biens pour les comparer. La comparaison utilise les mêmes hypothèses de profil pour tous les biens. Les scénarios détaillés restent accessibles depuis **Simuler**.

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

Conservez également `.env` et `config/parsers.json` pour retrouver votre configuration. Le code de connexion peut être récupéré séparément avec la commande de démarrage. Aucune migration automatique d'une future version de schéma n'est fournie dans cette première version.

## En cas de problème

| Symptôme | Action |
| --- | --- |
| Port 8765 occupé | Arrêtez l'ancien serveur Excel ou un autre Repère déjà lancé. |
| Docker indisponible | Ouvrez Docker Desktop, attendez son démarrage, relancez `DEMARRER.bat`. |
| Base refusant la connexion après une modification de `.env` | Le mot de passe du volume existant ne change pas automatiquement. Rétablissez celui créé au premier lancement. |
| Code refusé | Récupérez le code avec `python -m app.manage token` dans le conteneur, puis remplacez-le dans l'application et l'extension. |
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
