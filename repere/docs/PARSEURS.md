# Adapter les extractions

L'objectif est de conserver les informations déjà présentes dans la page, de pouvoir comparer plusieurs méthodes et de réanalyser une capture sans retourner sur le site.

## Les méthodes disponibles

| Méthode | Contenu utilisé | Bibliothèque / module |
| --- | --- | --- |
| Métadonnées structurées | JSON-LD, microdonnées, titre et description OpenGraph, prix explicite en EUR | `extruct`, règles dans `rules.py` |
| Texte visible | Texte capturé dans le navigateur, libellés et unités explicites | `rules.py` |
| HTML | Éléments du contenu principal, sélecteurs génériques et propres au site | BeautifulSoup + lxml |
| Texte principal | Contenu central extrait du HTML, réanalysé avec les règles textuelles | Trafilatura |
| Gemini facultatif | Titre, métadonnées, JSON-LD, texte de la capture | Sortie structurée, contrôle des citations |

Le choix des méthodes se trouve dans **Ajouter une annonce → Méthodes d'extraction**. Elles produisent des candidats par champ. Un candidat comporte une valeur, un nom de parseur et un extrait source. Des valeurs différentes pour un même champ créent un conflit : aucune méthode ne remporte automatiquement le conflit.

Les corrections manuelles sont stockées séparément et réappliquées après l'extraction. L'absence d'information reste explicite. Les prix de location, mensualités, prix au m², fourchettes et surfaces de terrasse ne doivent pas devenir le prix ou la surface habitable du logement.

Une page de vérification humaine reconnue est signalée. Le serveur ne lance pas de navigateur automatisé pour la contourner : utilisez la capture depuis votre navigateur.

## Ajouter des sélecteurs CSS sans modifier Python

Éditez `config/parsers.json`. Par exemple, pour une page dont le HTML contient :

```html
<main>
  <h1>Appartement à Cergy</h1>
  <span data-testid="sale-price">234 000 €</span>
  <span class="living-area">43,3 m²</span>
  <span class="property-city">Cergy</span>
</main>
```

Configurez :

```json
{
  "sites": [
    {
      "hosts": ["exemple.fr"],
      "fields": {
        "price_eur": {"selector": "[data-testid='sale-price']", "unit": "EUR"},
        "area_m2": {"selector": ".living-area", "unit": "m2"},
        "city": {"selector": ".property-city"}
      }
    }
  ]
}
```

Remplacez `exemple.fr` par le domaine réel. `www.` est retiré du domaine analysé ; les autres sous-domaines doivent être listés explicitement. Le domaine vient de l'URL de la capture : renseignez-la même lors d'un import HTML.

Les sélecteurs sont évalués dans `<main>`, `[role=main]`, ou à défaut le corps de la page. Ils doivent viser **le bien principal**, pas une liste de recommandations. Une règle s'ajoute aux méthodes générales ; elle ne masque pas leurs valeurs contradictoires.

Le texte visible de l'élément est lu, ou son attribut `content` s'il existe. `unit` permet de déclarer une unité absente de l'élément : ne l'ajoutez que si le contexte du site la garantit. Une valeur numérique doit être unique dans l'élément. Pour `charges_annual` et `tax_annual`, le montant sélectionné doit déjà être **annuel** : une règle CSS n'invente pas la périodicité. Les conversions mensuelles des règles textuelles exigent un libellé explicite.

Le fichier est monté dans le conteneur et relu à chaque analyse. En général, une modification suffit ; si votre éditeur remplace le fichier au lieu de l'écrire et que Docker garde l'ancienne version du montage, recréez le service avec `docker compose up -d --force-recreate app`.

Ouvrez ensuite une fiche et cliquez sur **Réanalyser la capture**. Une capture contenant uniquement du texte ne permet pas d'essayer des sélecteurs HTML : recapturez la page complète avec la nouvelle extension. Les sources afficheront **Règle du site** pour les candidats issus du fichier.

## Champs reconnus

| Champ | Format et unité |
| --- | --- |
| `price_eur` | Prix d'achat du logement, EUR |
| `area_m2` | Surface habitable / Carrez, m² |
| `city`, `district` | Ville, quartier |
| `property_type` | `Neuf`, `Ancien`, `Ancien récent`, `Inconnu` |
| `rooms`, `floor` | Pièces, étage, texte court |
| `dpe` | Lettre A à G, ou `NC` |
| `charges_annual`, `tax_annual` | Charges de copropriété et taxe foncière annuelles, EUR |
| `parking_extra`, `works_eur` | Supplément parking, travaux, EUR |
| `station_minutes` | Temps à pied vers la gare, minutes |
| `delivery` | Livraison annoncée, texte court |

Le schéma complet et ses bornes se trouvent dans `app/schemas.py`. Les champs dont le sens demande une conversion plus complexe sont mieux traités par un parseur Python ciblé que par un sélecteur CSS.

## Ajouter un autre parseur Python

`app/parsing/pipeline.py` orchestre l'analyse. Un nouvel extracteur doit appeler le collecteur interne `add(field, value, parser, quote)` après avoir identifié des faits et leurs extraits. Le collecteur valide les types avec Pydantic, conserve les candidats et calcule les conflits. N'écrivez pas directement dans la base à partir d'un parseur.

Pour un parseur sélectionnable dans l'interface, ajoutez son nom à `Capture.parsers` dans `app/schemas.py`, puis aux options de `openImport()` dans `app/static/app.js`. Traitez son échec comme un avertissement pour conserver les résultats des autres méthodes. Les appels réseau et les calculs financiers ne font pas partie d'un parseur local.

Pour tester une méthode sans créer de fiche, envoyez une capture à `POST /api/preview` avec le header `X-Repere-Token`. `POST /api/import` attend `{ "capture": { ... }, "fetch_url": false }` et enregistre une fiche. `/capture` reçoit directement la capture de l'extension.

Exemple de capture minimale :

```json
{
  "url": "https://exemple.fr/annonce/123",
  "title": "Appartement à Cergy",
  "text": "Prix : 234 000 €. Surface habitable : 43,3 m². Ville : Cergy",
  "html": "",
  "metadata": {},
  "json_ld": [],
  "selection_only": false,
  "use_gemini": false,
  "parsers": ["structured", "html", "text", "trafilatura"]
}
```

`json_ld` contient des chaînes JSON, pas des objets déjà décodés. Les imports acceptent jusqu'à 120 000 caractères de texte et 1,5 million de caractères HTML, avec une limite globale de requête de 2,5 Mo. Le collecteur de l'extension filtre notamment formulaires, champs de saisie, scripts exécutables et contenu masqué ; vérifiez toujours son aperçu avant un envoi externe facultatif.

## Un cas difficile à diagnostiquer

1. Exportez la capture depuis l'extension ou la fiche. Elle permet de reproduire le problème sans solliciter de nouveau le site.
2. Regardez quels éléments sont effectivement présents : prix dans le texte, JSON-LD, HTML, ou aucune de ces sources.
3. Essayez les méthodes séparément dans le formulaire d'import et consultez candidats et conflits.
4. Ajoutez une règle de site si le DOM est stable ; sinon, ajoutez une règle contextuelle ou essayez un autre extracteur.
5. Ajoutez un exemple anonymisé à `tests/test_parsers.py`, avec les valeurs attendues et les confusions à exclure.

Une source peut publier elle-même un prix incohérent. Plusieurs logements peuvent aussi être présents sur une page. Le mécanisme conserve les preuves pour que vous puissiez trancher ; il ne promet pas l'extraction parfaite de tous les portails.

Les données JavaScript internes non structurées, les iframes d'une autre origine, les images/PDF et l'OCR ne sont pas analysés dans cette version. L'extension récupère le DOM accessible de la page principale après son chargement.

Références techniques : [extruct](https://github.com/scrapinghub/extruct), [Trafilatura](https://trafilatura.readthedocs.io/en/latest/corefunctions.html), [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/).
