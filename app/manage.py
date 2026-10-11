import json
import sys
from .security import token

USAGE = ("Usage : python -m app.manage token\n"
         "        python -m app.manage dpe <code postal> <kWh/m²/an> [kg CO2/m²/an] [surface m²]\n"
         "        python -m app.manage dpe-lettres <code postal> <surface m²> <lettre DPE> [lettre GES]")

if __name__ == "__main__":
    if sys.argv[1:] == ["token"]:
        print(token())
    elif sys.argv[1:2] == ["dpe"] and 3 <= len(sys.argv) - 1 <= 5:
        # Vérification en direct de la recherche d'adresse, sans passer par l'interface.
        from .dpe_lookup import search_addresses
        from .schemas import AddressSearch
        args = sys.argv[2:] + [None] * 3
        criteria = AddressSearch(postal_code=args[0], dpe_kwh_m2=float(args[1].replace(",", ".")),
                                 ges_kg_m2=float(args[2].replace(",", ".")) if args[2] else None,
                                 area_m2=float(args[3].replace(",", ".")) if args[3] else None)
        print(json.dumps(search_addresses(criteria), ensure_ascii=False, indent=2))
    elif sys.argv[1:2] == ["dpe-lettres"] and 4 <= len(sys.argv) - 1 <= 5:
        # Recherche sans la consommation : code postal, surface, lettre DPE et éventuellement lettre GES.
        from .dpe_lookup import search_addresses
        from .schemas import AddressSearch
        args = sys.argv[2:] + [None]
        criteria = AddressSearch(postal_code=args[0], area_m2=float(args[1].replace(",", ".")), dpe=args[2].upper(),
                                 ges=(args[3] or "NC").upper())
        result = search_addresses(criteria)
        print(json.dumps({k: result[k] for k in ("criteria", "confidence", "total", "warnings")}, ensure_ascii=False, indent=2))
        for g in result["groups"]:
            print(f"- {g['address']} {g['postal_code'] or ''} · {g['count']} logement(s) · écart {g['score']}")
    else:
        raise SystemExit(USAGE)
