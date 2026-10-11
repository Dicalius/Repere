"""Extraction prudente de faits publiés. Aucun réseau et aucun appel d'IA."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


def fold(value):
    return "".join(c for c in unicodedata.normalize("NFKD", str(value)).casefold()
                   if not unicodedata.combining(c))


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    text = re.sub(r"[\s\u00a0\u202f]", "", str(value)).replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    n = float(text)
    return n if math.isfinite(n) else None


def types(node):
    values = node.get("@type", []) if isinstance(node, dict) else []
    return {str(v).rstrip("/").rsplit("/", 1)[-1] for v in (values if isinstance(values, list) else [values])}


def url_identity(raw, base):
    try:
        p = urlsplit(urljoin(base, raw))
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "msclkid"}
                 and not (k.lower() == "alert_id" and (p.hostname or "").removeprefix("www.") == "jinka.fr"
                          and re.fullmatch(r"/ad/[0-9a-f-]{36}/?", p.path, re.I))]
        return urlunsplit((p.scheme, p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), urlencode(sorted(query)), ""))
    except (TypeError, ValueError):
        return ""


# Niveau de fiabilité d'un candidat : plus le rang est bas, plus la source est fiable.
# Un conflit n'est déclaré qu'entre valeurs du meilleur niveau disponible ; les niveaux
# inférieurs restent visibles dans les diagnostics mais ne bloquent plus le champ.
# Au-delà, une sélection contient probablement plusieurs annonces : elle est traitée comme un corps de page.
SELECTION_SUMMARY_MAX = 1500
RANKS = {"site": 0, "structured": 1, "primary": 2, "labelled": 3, "gemini": 4, "scan": 5}

# Titres de sections qui introduisent d'autres biens que l'annonce.
SIMILAR = re.compile(r"(?:les |nos |des |d'autres |ces |d')?(?:annonces? (?:similaires|recommandees|proches|a proximite|du meme secteur)"
                     r"|biens? (?:similaires|a proximite|proches)|vous aimerez (?:aussi|egalement)|a decouvrir (?:aussi|egalement)"
                     r"|dans le meme (?:secteur|quartier)|autres (?:annonces|biens)|biens (?:pourraient|peuvent) vous interesser"
                     r"|recemment consultes?|internautes ont aussi|vous pourriez aussi aimer)")


def listing_part(text):
    """Coupe le texte au premier titre de section « annonces similaires » (après 200 caractères)."""
    kept, length = [], 0
    for line in str(text or "").splitlines():
        if length > 200 and len(line.strip()) <= 60 and SIMILAR.match(fold(line).strip()):
            break
        kept.append(line)
        length += len(line) + 1
    return "\n".join(kept)


N = r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
AMOUNT = re.compile(N + r"\s*(?:€|euros?\b|eur\b)")
SEPARATORS = re.compile(r"[\n·•|–—;,()!?]|\.\s|\s-\s|€")
NEUTRAL = re.compile(r"honoraires (?:inclus|compris)|frais d.agence (?:inclus|compris)|\bfai\b|\bhai\b|\bttc\b|\bcc\b")
PRICE_LABEL = re.compile(r"(?:le |au )?prix(?: de vente| affiche| d.achat| ttc| demande)?")
BAD_LABEL = re.compile(r"a partir|\bdes\b|mensualit|mensuel|loyer|location|charges?\b|honoraires|frais|taxe|apport|budget|estimation"
                       r"|estime|econom|jusqu|\bdont\b|\bsoit\b|parking|garage|\bbox\b|\bcave\b|travaux|simulation|m2|revenu|salaire"
                       r"|caution|depot|moyen|median|reduction|baisse|ancien prix|plus que|\bmoins\b|\bpar\b|net vendeur|total|\bcout\b|notaire|energie")
BAD_AFTER = re.compile(r"\s*(?:/|par|le|du|au|l')\s*(?:mois|an\b|annee|m2|jour|semaine|nuit)|\s*(?:d.honoraires|de frais|de charges"
                       r"|de travaux|de taxe|hors honoraires|net vendeur|hc\b|mensuel|par mois|de loyer|d.apport|d.econom|de moins|de baisse)")
SURFACE_BAD = re.compile(r"terrasse|balcon|terrain|jardin|garage|parking|dependance|\bcave\b|sejour|salon|chambre|cuisine|piece de vie"
                         r"|salle|bureau|loggia|grenier|combles|annexe|veranda|entree|dressing|mezzanine|\bbox\b")
RENTAL = re.compile(r"\b(?:a louer|location|loyer|locatif)\b")
SALE_TITLE = re.compile(r"\b(?:a vendre|vente|achat|acheter|vendu)\b")
RENT_TOKENS = {"location", "locations", "colocation", "colocations", "louer", "rent", "rental", "loyer"}
SALE_TOKENS = {"vente", "ventes", "achat", "acheter", "vendre", "sale", "buy"}
RENT_SIGNALS = (r"\bloyer\b", r"depot de garantie", r"charges comprises", r"\bbail\b", r"(?:€|euros?)\s*(?:/|par)\s*mois\b(?!.{0,20}(?:mensualite|credit))",
                r"\bhonoraires? (?:de location|locataire)", r"etat des lieux", r"\bapl\b")
SALE_SIGNALS = (r"prix de vente", r"frais de notaire", r"a la charge de l.acquereur", r"\bnet vendeur\b", r"copropriete de \d+ lots",
                r"\bmandat\b", r"\bacquereur", r"\bdpe vierge\b(?=.*vente)")
# Loyer : libellés acceptés, libellés écartés (dépôt, frais, aides…)
RENT_LABEL = re.compile(r"(?:le )?(?:loyer|prix|location)(?: mensuel(?:le)?)?(?: charges comprises| hors charges| cc| hc| ttc| tcc)?(?: mensuel(?:le)?)?")
RENT_BAD_LABEL = re.compile(r"charges?\s*$|charges? (?!comprises)|depot|garantie|caution|honoraires|frais|agence|etat des lieux|a partir|\bdes\b|budget"
                            r"|taxe|\bapl\b|aide|revenus?|salaire|parking|garage|\bbox\b|\bcave\b|m2|economi|reduction|baisse|\bdont\b|\bsoit\b")
PER_MONTH = re.compile(r"\s*(?:/\s*mois|par mois|mensuels?\b|\bcc\b|\bhc\b|c\.c\.|charges comprises|hors charges)")
PROPERTY = re.compile(r"\b(?:appartement|maison|logement|studio|duplex|loft|villa|triplex)\b")

# Villes : « Nantes (44000) », « 44000 Nantes », « à Nantes - », « Ville : Nantes ».
_UP, _LOW = "A-ZÀ-ÖØ-Ý", "a-zà-öø-ÿ"
_WORD = rf"[{_UP}][{_LOW}{_UP}'’\-]*"
_CITY = rf"{_WORD}(?:[ \-](?:{_WORD}|sur|sous|lès|les|la|le|de|du|des|en|aux?|d'{_WORD}|l'{_WORD}))*"
_CP = r"(?:0[1-9]|[1-8]\d|9[0-8])\d{3}"
CITY_WITH_CODE = re.compile(rf"({_CITY})\s*\(\s*({_CP})\s*\)")
CITY_WITH_DEPT = re.compile(rf"({_CITY})\s*\(\s*({_CP}|0[1-9]|[1-8]\d|9[0-5]|97[1-6]|2[AB])\s*\)")
CODE_THEN_CITY = re.compile(rf"(?<!\d)(?<!\d[ \u00a0\u202f])({_CP})(?!\d)\s+({_CITY})")
POSTAL_LABEL = re.compile(rf"(?im)\bcode postal\s*[:=]?\s*\n?\s*({_CP})(?!\d)")

# Valeurs chiffrées du DPE : consommation (énergie primaire) et émissions, telles qu'affichées sur l'étiquette.
KWH = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*kwh\s*(?:ep\b|e\.p\.)?\s*(?:/|par)\s*m2\s*(?:[./]\s*|par\s+)?(?:an|annee)\b")
CO2 = re.compile(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*kg\s*(?:d.)?(?:eq\.?\s*)?co2\s*(?:eq\.?)?\s*(?:/|par)\s*m2\s*(?:[./]\s*|par\s+)?(?:an|annee)\b")
DPE_VALUE_BAD = re.compile(r"[<>≤≥]|(?:^|\s)(?:a|de|entre)$|jusqu|inferieur|superieur|finale|\bef\b|plus de|moins de|\bmax|\bmin|seuil|moyenne|median")
AT_CITY = re.compile(rf"\b[àa] ({_CITY})\s*(?=$|[\-–—|,(:·]|\d)", re.M)
# « Sartrouville · 78500 … », « Nantes, 44000 », « Appartement 3 pièces 65 m² Nantes - 250 000 € »
CITY_BEFORE_CODE = re.compile(rf"({_CITY})\s*[·•,\-–—]\s*({_CP})(?!\d)")
AFTER_AREA = re.compile(rf"m[²2][ \t]+(?:[àa][ \t]+)?({_CITY})[ \t]*(?=$|[\-–—|,(:·]|\d)", re.M)
HEADER_CHARS = 600   # début du texte de l'annonce : titre, lieu, prix (l'adresse de l'agence est plus loin)
CITY_LABEL = re.compile(r"(?im)^[ \t]*(?:ville|commune|localisation|localit[ée])[ \t]*(?:[:=][ \t]*|\n[ \t]*)([^\n;]{2,100})$")
NOT_CITY = {"appartement", "maison", "studio", "duplex", "triplex", "loft", "villa", "vente", "achat", "vendre", "immobilier",
            "annonce", "annonces", "logement", "programme", "neuf", "ancien", "terrain", "immeuble", "local", "parking", "garage",
            "bien", "biens", "exclusivite", "exclusif", "nouveau", "nouveaute", "prix", "a", "au", "en", "de", "du",
            "pieces", "piece", "chambre", "chambres", "terrain", "balcon", "terrasse", "jardin", "cave", "parking",
            "garage", "etage", "rdc", "dpe", "ges", "surface", "loyer", "charges", "honoraires", "description"}


def clean_city(raw):
    words = re.sub(r"\(.*?\)|\b\d{5}\b", " ", str(raw or "")).replace("\u00a0", " ").split()
    while words and (fold(words[0]).strip("-'’") in NOT_CITY or re.fullmatch(r"[tf]\d+", fold(words[0]))):
        words.pop(0)
    city = " ".join(words).strip(" -–—,.;:")
    if not city or not re.match(rf"[{_UP}]", city) or len(city) > 60 or re.search(r"\d", city):
        return None
    return city


def label_before(folded, start, width=60):
    """Libellé d'une valeur : texte depuis le dernier séparateur et la dernière autre donnée chiffrée.
    Si la valeur est seule sur sa ligne, le libellé est la ligne précédente quand elle est courte et sans chiffre."""
    segment = SEPARATORS.split(folded[max(0, start - width):start])[-1]
    segment = re.split(r"\d[\d\s.,]*(?:m2|pieces?|chambres?|%|ans?\b)?", segment)[-1]
    label = re.sub(r"\s+", " ", NEUTRAL.sub(" ", segment)).strip(" :=.\t")
    line_start = folded.rfind("\n", 0, start) + 1
    if not label and line_start and not re.search(r"\w", folded[line_start:start]):
        previous = folded[folded.rfind("\n", 0, line_start - 1) + 1:line_start - 1].strip(" :=.\t")
        if previous and len(previous) <= 40 and not re.search(r"\d", previous):
            label = re.sub(r"\s+", " ", NEUTRAL.sub(" ", previous)).strip(" :=.\t")
    return label


def surface_label(folded, start):
    """Libellé d'une surface : texte depuis le dernier séparateur ou la dernière autre surface."""
    segment = SEPARATORS.split(folded[max(0, start - 50):start])[-1]
    return re.split(r"\d[\d\s.,]*m2", segment)[-1]


def path_tokens(url):
    return set(re.split(r"[/_\-.?=&+]+", fold(urlsplit(url or "").path + "?" + (urlsplit(url or "").query or ""))))


def detect_transaction(url, primary_texts, body):
    """Achat ou location : URL, puis titre / métadonnées, puis vocabulaire du corps de page.
    Renvoie des candidats (valeur, citation, nature) ; aucun signal ne vaut « Achat » par défaut."""
    found = []
    tokens = path_tokens(url)
    rent, sale = tokens & RENT_TOKENS, tokens & SALE_TOKENS
    if rent and not sale:
        found.append(("Location", "URL : " + "/".join(sorted(rent)), "structured"))
    elif sale and not rent:
        found.append(("Achat", "URL : " + "/".join(sorted(sale)), "structured"))
    for primary in dict.fromkeys(p for p in primary_texts if p):
        summary = fold(primary)
        rental, selling = RENTAL.search(summary), SALE_TITLE.search(summary)
        if rental and not selling:
            found.append(("Location", primary[:200], "primary"))
        elif selling and not rental:
            found.append(("Achat", primary[:200], "primary"))
    clean = fold(listing_part(body))[:30000]
    rent_hits = [s for s in RENT_SIGNALS if re.search(s, clean)]
    sale_hits = [s for s in SALE_SIGNALS if re.search(s, clean)]
    if len(rent_hits) >= 2 and not sale_hits:
        found.append(("Location", "Vocabulaire de location dans la page (loyer, dépôt de garantie…)", "scan"))
    elif sale_hits and not rent_hits:
        found.append(("Achat", "Vocabulaire de vente dans la page (frais de notaire, acquéreur…)", "scan"))
    return found


def extract_rent_rules(add, sources, body):
    """Location : loyer, charges locatives, charges comprises ou non, meublé. Aucun prix d'achat."""
    for primary, selection in sources:
        summary = fold(primary)
        quote = (lambda m: m[0].strip()) if selection else (lambda m: primary)
        for match in AMOUNT.finditer(summary):
            value = number(match[1])
            segment = SEPARATORS.split(summary[max(0, match.start() - 60):match.start()])[-1]
            if value and 100 <= value <= 20000 and not RENT_BAD_LABEL.search(segment) and \
                    not re.match(r"\s*(?:/|par|le)\s*m2|\s*de (?:charges|depot|garantie|frais|honoraires)", summary[match.end():match.end() + 25]):
                add("loyer_mois", value, quote(match), "primary")
    clean = fold(body)
    flags = []
    for match in AMOUNT.finditer(clean):
        value = number(match[1])
        if value is None or not 100 <= value <= 20000:
            continue
        raw_label = SEPARATORS.split(clean[max(0, match.start() - 60):match.start()])[-1].strip(" :=.\t")
        previous = ""
        if not raw_label and clean.rfind("\n", 0, match.start()) > 0:
            line_start = clean.rfind("\n", 0, match.start()) + 1
            if not re.search(r"\w", clean[line_start:match.start()]):
                previous = clean[clean.rfind("\n", 0, line_start - 1) + 1:line_start - 1].strip(" :=.\t")[:40]
                if RENT_LABEL.fullmatch(previous):
                    raw_label = previous
        after = clean[match.end():match.end() + 30]
        if RENT_LABEL.fullmatch(raw_label) and not re.match(r"\s*(?:/|par|le)\s*m2", after):
            add("loyer_mois", value, (raw_label + " " + match[0]).strip(), "labelled")
            context = raw_label + " " + after
            if re.search(r"hors charges|\bhc\b", context):
                flags.append(("Non", raw_label + " " + match[0]))
            elif re.search(r"charges comprises|\bcc\b|c\.c\.|\btcc\b", context):
                flags.append(("Oui", raw_label + " " + match[0]))
        elif not raw_label and PER_MONTH.match(after) and not RENT_BAD_LABEL.search(after[:20] + " " + previous):
            add("loyer_mois", value, (match[0] + after[:15]).strip(), "scan")
    for match in re.finditer(r"(?<!hors )(?<!comprises )(?:provisions? (?:sur|pour) charges|charges(?: locatives| mensuelles| forfaitaires)?)\s*(?:[:=]\s*|\n\s*)"
                             + N + r"\s*(?:€|euros?\b|eur\b)", clean):
        if number(match[1]) is not None and number(match[1]) <= 2000:
            add("charges_loyer_mois", number(match[1]), match[0].strip(), "labelled")
    for match in re.finditer(r"\bdont\s+" + N + r"\s*(?:€|euros?\b|eur\b)\s*(?:de\s+)?(?:provisions? (?:sur|pour) )?charges", clean):
        add("charges_loyer_mois", number(match[1]), match[0], "labelled")
        flags.append(("Oui", match[0]))
    for match in re.finditer(r"\+\s*" + N + r"\s*(?:€|euros?\b|eur\b)\s*(?:de\s+)?(?:provisions? (?:sur|pour) )?charges", clean):
        add("charges_loyer_mois", number(match[1]), match[0], "labelled")
        flags.append(("Non", match[0]))
    for value, quote in flags:
        add("charges_incluses", value, quote, "labelled")
    texts = [fold(p) for p, _ in sources] + [clean]
    for text, kind in zip(texts, ["primary"] * len(sources) + ["labelled"]):
        for match in re.finditer(r"\b(?:non[ -]meublee?s?|location vide|loue vide|logement vide|meublee?\s*[:=]\s*non)\b", text):
            add("meuble", "Non meublé", match[0], kind)
        for match in re.finditer(r"(?<!non )(?<!non-)\b(?:(?:appartement|studio|logement|maison|location|loue|duplex|loft|chambre|"
                                 r"[tf]\d|pieces?)\s+meublee?s?|meublee?\s*[:=]\s*oui|lmnp)\b|(?m:^\s*meublee?\s*$)", text):
            add("meuble", "Meublé", match[0].strip(), kind)


def extract_text_rules(add, primary_texts, body, selection_summary="", rental=False):
    """Règles textuelles hiérarchisées : titre/résumé > libellé explicite > balayage du corps de page.
    Une courte sélection manuelle est un résumé de l'annonce : on n'y exige pas le mot « appartement ».
    En location, les montants sont lus comme des loyers et des charges mensuelles, jamais comme un prix d'achat."""
    sources = [(p, False) for p in dict.fromkeys(p for p in primary_texts if p)]
    if selection_summary:
        sources.append((selection_summary, True))
    if rental:
        extract_rent_rules(add, [(p, sel) for p, sel in sources if sel or PROPERTY.search(fold(p)) or RENTAL.search(fold(p))], body)
    for primary, selection in sources:
        summary = fold(primary)
        if not selection and (not PROPERTY.search(summary) or (RENTAL.search(summary) and not rental)):
            continue
        quote = (lambda m: m[0].strip()) if selection else (lambda m: primary)
        for match in AMOUNT.finditer(summary):
            label = label_before(summary, match.start())
            value = number(match[1])
            if not rental and value and value >= 10000 and not BAD_AFTER.match(summary[match.end():match.end() + 30]) \
                    and not BAD_LABEL.search(label):
                add("prix_eur", value, quote(match), "primary")
        for match in re.finditer(N + r"\s*m(?:2|²)\b", summary):
            if not SURFACE_BAD.search(surface_label(summary, match.start())) and not re.match(
                    r"\s*d(?:e\s+|')(?:terrasse|balcon|terrain|jardin|garage|parking|cave)\b", summary[match.end():match.end() + 30]):
                add("surface_m2", number(match[1]), quote(match), "primary")
        for match in re.finditer(r"\b(\d{1,2})\s*pieces?\b|\b[tf]\s?(\d{1,2})\b", summary):
            rooms = int(match[1] or match[2])
            if 1 <= rooms <= 20:
                add("pieces", f"T{rooms}", match[0], "primary")
        if re.search(r"\bstudio\b", summary) and not re.search(r"\b\d{1,2}\s*pieces?\b|\b[tf]\s?\d", summary):
            add("pieces", "T1", "studio", "primary")
        for match in CITY_WITH_DEPT.finditer(primary):
            add("ville", clean_city(match[1]), match[0], "primary")
            if len(match[2]) == 5:
                add("code_postal", match[2], match[0], "primary")
        for match in CODE_THEN_CITY.finditer(primary):
            add("ville", clean_city(match[2]), match[0], "primary")
            add("code_postal", match[1], match[0], "primary")
        for pattern in (AT_CITY, AFTER_AREA):
            for match in pattern.finditer(primary):
                add("ville", clean_city(match[1]), match[0], "primary")
        for match in CITY_BEFORE_CODE.finditer(primary):
            add("ville", clean_city(match[1]), match[0], "primary")
            add("code_postal", match[2], match[0], "primary")

    clean = fold(body)
    for match in AMOUNT.finditer(clean if not rental else ""):
        label, value = label_before(clean, match.start()), number(match[1])
        if BAD_AFTER.match(clean[match.end():match.end() + 30]) or value is None:
            continue
        if PRICE_LABEL.fullmatch(label):
            add("prix_eur", value, (label + " " + match[0]).strip(), "labelled")
        elif not label and value >= 10000:
            add("prix_eur", value, match[0].strip(), "scan")
    for match in re.finditer(r"surface\s*(?:habitable|carrez|loi carrez)?\s*[:=]?\s*" + N + r"\s*m(?:2|²)\b", clean):
        add("surface_m2", number(match[1]), match[0], "labelled")
    for match in re.finditer(r"(?m)^[ \t]*(?:nombre de\s+)?pieces?[ \t]*(?:[:=][ \t]*|\n[ \t]*)(\d{1,2})[ \t]*$", clean):
        if 1 <= int(match[1]) <= 20:
            add("pieces", "T" + match[1], match[0].strip(), "labelled")
    for match in re.finditer(r"\b(\d{1,2})\s*pieces\b", clean):
        if 1 <= int(match[1]) <= 20:
            add("pieces", "T" + match[1], match[0], "scan")
    for match in CITY_LABEL.finditer(body):
        add("ville", clean_city(match[1]), match[0].strip(), "labelled")
    for match in CITY_WITH_CODE.finditer(body):
        add("ville", clean_city(match[1]), match[0], "labelled")
        add("code_postal", match[2], match[0], "labelled")
    header = body[:HEADER_CHARS]
    for match in CITY_BEFORE_CODE.finditer(header):
        add("ville", clean_city(match[1]), match[0], "labelled")
        add("code_postal", match[2], match[0], "labelled")
    for match in CODE_THEN_CITY.finditer(header):
        # Près du titre, « 78500 La Vaudoire » donne un code postal fiable ; le nom qui suit peut être un quartier.
        add("code_postal", match[1], match[0], "labelled")
    for match in POSTAL_LABEL.finditer(body):
        add("code_postal", match[1], match[0].strip(), "labelled")
    for field, pattern in (("conso_kwh_m2", KWH), ("ges_kg_m2", CO2)):
        for match in pattern.finditer(clean):
            if not DPE_VALUE_BAD.search(label_before(clean, match.start(), 40)):
                add(field, number(match[1]), match[0].strip(), "labelled")
    for match in re.finditer(r"\b(?:ges|emissions? de gaz a effet de serre|classe (?:ges|climat)|indice ges)\s*[:=]?\s*(?:classe\s*)?([a-g])\b", clean):
        if not re.match(r"\s+[a-g](?:\s+[a-g]){2}", clean[match.end():]):
            add("ges", match[1].upper(), match[0], "labelled")
    for field, label in (() if rental else (("charges_copro_an_eur", r"charges(?: de copropriete| copro)?"),
                                            ("taxe_fonciere_an_eur", r"taxe fonciere"))):
        for match in re.finditer(label + r"\s*[:=]?\s*" + N + r"\s*(?:€|euros?\b|eur\b)\s*(?:/|par)\s*(mois|an|annee)\b", clean):
            add(field, number(match[1]) * (12 if match[2] == "mois" else 1), match[0], "labelled")
        for match in re.finditer(label + r"\s+(annuelles?|mensuelles?)\s*[:=]?\s*" + N + r"\s*(?:€|euros?\b|eur\b)", clean):
            add(field, number(match[2]) * (12 if match[1].startswith("mensuel") else 1), match[0], "labelled")
    for match in re.finditer(r"\b(?:dpe|classe\s+(?:energie|energetique)|performance energetique)\s*[:=]?\s*(?:classe\s*)?([a-g])\b", clean):
        # Une échelle A B C D E F G visible ne désigne pas une classe particulière.
        if not re.match(r"\s+[a-g](?:\s+[a-g]){2}", clean[match.end():]):
            add("dpe", match[1].upper(), match[0], "labelled")
    for kind, pattern in (("Neuf", r"\b(?:appartement neuf|logement neuf|programme neuf|vefa)\b"),
                          ("Ancien", r"\b(?:appartement ancien|logement ancien|maison ancienne)\b")):
        for match in re.finditer(pattern, clean):
            if not re.search(r"(?:pas|non|hors)\s+(?:un\s+)?$", clean[max(0, match.start() - 15):match.start()]):
                add("type_bien", kind, match[0], "scan")


def extract_local(context: str, url: str, capture=None):
    """Retourne données reconnues, preuves et conflits. Les absences restent absentes."""
    candidates = defaultdict(list)
    notes, warnings = [], []
    limits = {"prix_eur": (1000, 20_000_000), "surface_m2": (5, 2000),
              "charges_copro_an_eur": (0, 200000), "taxe_fonciere_an_eur": (0, 200000)}

    def add(field, value, source, kind="labelled"):
        if value is None or value == "":
            return
        if field in limits:
            lo, hi = limits[field]
            if not isinstance(value, (int, float)) or not lo <= value <= hi:
                return
        candidates[field].append((value, source[:500], kind))

    roots = []
    def flatten(value):
        if isinstance(value, list):
            for item in value:
                flatten(item)
        elif isinstance(value, dict):
            roots.append(value)
            flatten(value.get("@graph"))

    rental_mode = bool(getattr(capture, "rental", False))
    if capture is not None:
        raw_blocks = [] if capture.selection_only else capture.json_ld
        text_parts = [capture.title, capture.text]
        # Une sélection manuelle désigne l'annonce elle-même : on la traite comme un titre/résumé.
        # Le titre de la page reste une source principale, y compris en mode sélection.
        primary_texts = [capture.title]
        selected = listing_part(capture.text) if capture.selection_only else ""
        summary = selected if 0 < len(selected) <= SELECTION_SUMMARY_MAX else ""
        if not capture.selection_only:
            primary_texts += [v for k, v in capture.metadata.items() if k in ("og:title", "description", "og:description")]
            text_parts += primary_texts[1:]
        text = "\n".join(text_parts)
        body = capture.text
    else:
        summary = ""
        # Les pages directes utilisent le même contexte avec chaque JSON sur une ligne.
        raw_blocks = re.findall(r"(?m)^JSON-LD: (.*)$", context)
        text = re.sub(r"(?m)^JSON-LD: .*$", "", context)
        primary_texts = re.findall(r"(?m)^(?:TITLE|META (?:og:title|description|og:description)): (.*)$", context)
        body = text
    for raw in raw_blocks:
        if len(raw) > 120_000:
            continue
        try:
            flatten(json.loads(raw))
        except (ValueError, TypeError, RecursionError):
            continue
    by_id = {urljoin(url, n["@id"]): n for n in roots if isinstance(n.get("@id"), str)}

    def resolve(node):
        if isinstance(node, dict) and isinstance(node.get("@id"), str):
            return {**by_id.get(urljoin(url, node["@id"]), {}), **node}
        return node

    def is_property(node):
        kinds = types(node)
        return bool(kinds & {"Apartment", "House", "SingleFamilyResidence", "Residence", "Accommodation", "RealEstateListing"}) or (
            "Product" in kinds and ("floorSize" in node or re.search(r"appartement|maison|logement", str(node.get("name", "")), re.I)))

    properties = [n for n in roots if is_property(n)]
    # Favoriser la page qui désigne explicitement son entité principale.
    wrappers = [n for n in roots if (types(n) & {"RealEstateListing", "WebPage"} and
                (is_property(resolve(n.get("mainEntity"))) or is_property(resolve(n.get("about"))))) or
                ("Offer" in types(n) and is_property(resolve(n.get("itemOffered"))))]
    linked_ids = {urljoin(url, child["@id"]) for n in wrappers for key in ("mainEntity", "about", "itemOffered")
                  if isinstance((child := n.get(key)), dict) and isinstance(child.get("@id"), str)}
    properties = wrappers + [n for n in properties if n not in wrappers and
                            urljoin(url, n.get("@id", "")) not in linked_ids]
    # Le même bloc peut être publié deux fois.
    properties = list({json.dumps(n, sort_keys=True): n for n in properties}.values())

    def refs(node):
        return [v for key in ("url", "@id") if isinstance((v := node.get(key)), str)]

    same = [n for n in properties if any(url_identity(v, url) == url_identity(url, url) for v in refs(n))]
    eligible = same or [n for n in properties if not refs(n)]
    ambiguous = len(eligible) > 1 or (not eligible and any("ItemList" in types(n) for n in roots))
    if ambiguous:
        warnings.append("Plusieurs biens possibles : sélectionne uniquement le texte de l'annonce dans l'extension.")
        return {"data": {}, "evidence": {}, "conflicts": [], "warnings": warnings, "notes": [], "candidates": {}}
    if properties and not eligible:
        warnings.append("Les métadonnées structurées ne désignent pas l'URL de cette annonce ; leurs valeurs ne sont pas attribuées au bien.")

    def one(value):
        if isinstance(value, list):
            return resolve(value[0]) if len(value) == 1 else None
        return resolve(value)

    if len(eligible) == 1:
        root = eligible[0]
        node = one(root.get("mainEntity")) or one(root.get("about")) or root
        offer = root if "Offer" in types(root) else (one(root.get("offers")) or (one(node.get("offers")) if isinstance(node, dict) else None))
        if not isinstance(node, dict):
            node = {}
        if isinstance(offer, dict):
            offered = one(offer.get("itemOffered"))
            if is_property(offered):
                node = offered
            price_node = one(offer.get("priceSpecification")) or offer
            if isinstance(price_node, dict):
                currency = str(price_node.get("priceCurrency", offer.get("priceCurrency", ""))).upper()
                rental = "lease" in str(offer.get("businessFunction", "")).lower() or bool(price_node.get("billingDuration"))
                rental |= fold(price_node.get("unitText", price_node.get("unitCode", ""))) in {"mois", "month", "mon", "jour", "day"}
                rental |= bool(re.search(r"\b(?:location\s+(?:d.un\s+)?appartement|appartement\s+a\s+louer)\b",
                                         fold(str(root.get("name", "")) + " " + str(node.get("name", "")))))
                if currency in {"EUR", "€"} and not rental and not rental_mode and "AggregateOffer" not in types(offer):
                    add("prix_eur", number(price_node.get("price")), "JSON-LD offers.price = " + str(price_node.get("price")), "structured")
                elif currency in {"EUR", "€"} and (rental or rental_mode) and "AggregateOffer" not in types(offer) and \
                        fold(price_node.get("unitText", price_node.get("unitCode", ""))) not in {"jour", "day", "nuit", "night", "semaine", "week"}:
                    rent = number(price_node.get("price"))
                    if rent is not None and 100 <= rent <= 20000:
                        add("loyer_mois", rent, "JSON-LD offers.price = " + str(price_node.get("price")), "structured")
        address = one(node.get("address"))
        if isinstance(address, dict) and isinstance(address.get("addressLocality"), str):
            add("ville", address["addressLocality"].strip(), "JSON-LD address.addressLocality = " + address["addressLocality"], "structured")
        size = one(node.get("floorSize"))
        if isinstance(size, dict) and str(size.get("unitCode", size.get("unitText", ""))).upper() in {"MTK", "M2", "M²", "SQM"}:
            add("surface_m2", number(size.get("value")), "JSON-LD floorSize = " + json.dumps(size, ensure_ascii=False), "structured")
        rooms = node.get("numberOfRooms")
        rooms = number(rooms.get("value")) if isinstance(rooms, dict) else number(rooms)
        if rooms is not None and rooms.is_integer() and 1 <= rooms <= 20:
            add("pieces", "T" + str(int(rooms)), "JSON-LD numberOfRooms = " + str(int(rooms)), "structured")
        year = number(node.get("yearBuilt"))
        if year is not None and year.is_integer() and 1700 <= year <= 2100:
            add("annee_livraison", str(int(year)), "JSON-LD yearBuilt = " + str(int(year)), "structured")
        for key in ("name", "description"):
            for item in (root, node):
                if isinstance(item.get(key), str):
                    text += "\n" + item[key]
                    primary_texts.append(item[key])
        props = node.get("additionalProperty", [])
        for prop in props if isinstance(props, list) else [props]:
            if not isinstance(prop, dict):
                continue
            label, value = fold(prop.get("name", "")), prop.get("value")
            if label in {"dpe", "classe energie", "classe energetique", "performance energetique"} and value in list("ABCDEFG"):
                add("dpe", value, "JSON-LD " + str(prop.get("name")) + " = " + value, "structured")
            if label in {"surface habitable", "surface carrez"} and prop.get("unitText", prop.get("unitCode")) in {"m²", "m2", "MTK"}:
                add("surface_m2", number(value), "JSON-LD " + str(prop.get("name")) + " = " + str(value), "structured")

    body = listing_part(body)
    extract_text_rules(add, primary_texts, body, summary, rental_mode)

    for line in text.splitlines():
        if re.search(r"balcon|terrasse|parking|ptz|psla|brs|tva|livraison", line, re.I):
            notes.append(line.strip()[:250])

    data, evidence, conflicts = {}, {}, []
    for field, values in candidates.items():
        best = min(RANKS[kind] for _, _, kind in values)
        top = [(v, q) for v, q, kind in values if RANKS[kind] == best]
        if len({json.dumps(v, ensure_ascii=False) for v, _ in top}) == 1:
            data[field] = top[0][0]
            evidence[field] = list(dict.fromkeys(q for _, q in top))
        else:
            conflicts.append(field)
    # L'avertissement final sur les conflits est émis par pipeline(), après fusion de toutes les sources.
    return {"data": data, "evidence": evidence, "conflicts": conflicts,
            "warnings": warnings, "notes": list(dict.fromkeys(notes))[:5], "candidates": dict(candidates)}
