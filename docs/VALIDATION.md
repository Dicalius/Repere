# Vérification de Repère 1.0.0

Vérifications effectuées le 27 septembre 2026 sur le code fourni, avec des données synthétiques.

## Exécuté

- Suite Python : tests du moteur financier, des parseurs et de l'API, avec une base SQLite temporaire. Le compte final est indiqué dans le fichier `RESULTATS.txt`.
- Extension : **6 tests réussis** sous Node.js/jsdom. Collecte HTML, JSON-LD et texte ; exclusion des formulaires ; sélection manuelle ; édition du texte ; envoi unique ; erreur serveur ; permissions.
- Parcours dans **Chromium 153**, piloté par Playwright : connexion, budget, import de deux annonces HTML, contrôle du prix/surface, correction du prix, validation, comparaison, simulation inverse, enregistrement et rechargement d'un scénario, invalidation des résultats après modification, export de sauvegarde.
- Affichage à 1440 px et à 390 px ; absence de débordement horizontal sur le tableau de bord mobile testé. Captures d'écran dans `tests/screenshots`.
- Collecteur de l'extension exécuté dans un vrai DOM Chromium ; exclusion du texte masqué et des champs de formulaire vérifiée. Les API Chrome de la fenêtre d'extension sont simulées dans les tests séparés.
- Configuration Compose analysée : deux services, port applicatif lié à l'interface locale, PostgreSQL sans port hôte publié, volumes persistants et dépendance à son contrôle de santé.

Les tests financiers couvrent notamment le taux nul, la formule d'annuité, le remboursement du capital, l'assurance, les différés, les prêts continuant après le prêt principal, la cible mensuelle vérifiée sur toutes les phases, les projets surfinancés et la distinction entre charge nulle et charge inconnue.

Les tests d'extraction couvrent notamment JSON-LD et microdonnées, HTML, Trafilatura, montants français, conflits, prix locatifs, prix au m², prix « à partir de », montants d'un simulateur de crédit, adresse d'agence, citations Gemini incohérentes, règles de site et pages de vérification.

L'API est vérifiée sur les captures, corrections conservées, révisions concurrentes, contrôles de type, authentification, origine, adresses privées, persistance après reconnexion à la base, sauvegarde, restauration et annulation transactionnelle d'une restauration invalide.

## Non exécuté dans ce contexte

- **Construction et démarrage réels des conteneurs Docker, intégration PostgreSQL** : aucun moteur Docker utilisable ici. Le pilote PostgreSQL et les déclarations SQLAlchemy sont présents, mais les tests de base exécutés utilisent SQLite.
- Scripts `.bat`/PowerShell sur Windows et installation de l'extension non empaquetée dans votre profil Chrome/Edge.
- Appel réel à Gemini avec votre clé et le modèle configuré. Les contrôles de citations et le maintien des données locales en cas d'échec sont testés sans requête payante.
- Extraction de votre annonce Jinka réelle : aucune capture complète de cette annonce n'était fournie. Les captures des tests n'attestent pas une compatibilité exhaustive avec tous les portails.

Pour la première utilisation, démarrez avec `docker compose up -d --build`, vérifiez le statut des services avec `docker compose ps`, puis importez une annonce connue via l'extension. Contrôlez son prix, sa surface et sa ville avant de valider la fiche.

La première version n'inclut pas de migration du classeur Excel, d'OCR, de robot connecté aux portails, d'accès multi-utilisateur ou d'évaluation bancaire automatique.
