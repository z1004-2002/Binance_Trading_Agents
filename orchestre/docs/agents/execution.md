# Carte d'agent — ExecutionAgent

> Carte pédagogique (C-06, EX-AG-01) : relie l'agent aux notions du cours (PEAS, type d'agent de Russell & Norvig, propriétés de Wooldridge & Jennings).
> Chiffres de backtest : historique public Binance (BTCUSDT et quelques autres paires), environ 2024-05 → 2026-10, mesurés le 2026-10-20,
> **indicatifs** (période courte, biais du survivant, plusieurs évaluations sur le même historique). Ils se régénèrent avec les commandes de
> `docs/LANCEMENT.md` §8. Ceci n'est pas un conseil financier.

| Champ | Valeur |
|---|---|
| Identifiant | `execution` |
| Rôle | Seul écrivain sur Binance : traduit les ordres approuvés en ordres arrondis aux filtres, suit leur état, annule, réconcilie. |
| Type d'agent (Russell & Norvig) | Actionneur |
| Écrit sur Binance ? | OUI, seul |
| Code | `execution/service.py`, `exchange/` (Binance via ccxt, factice, paper) |
| Tests | `tests/unit/test_execution.py`, `tests/unit/test_limit_orders.py`, `tests/unit/test_stop_orders.py`, `tests/integration_testnet/` |
| Exigences de la spécification | EX-SEC-12, EX-EX-01 à EX-EX-10, EX-ST-02, EX-ST-06 |

## PEAS

- **Performance** : zéro ordre en double, zéro ordre orphelin, état local = état de la plateforme.
- **Environnement** : API Binance Spot (ou exchange factice / paper).
- **Actionneurs** : ordres au marché, limite maker, stop-limit ; annulations.
- **Capteurs** : statut des ordres, soldes, filtres.

## Propriétés de Wooldridge & Jennings

| Propriété | Illustration |
|---|---|
| Autonomie | exécute seul ce que le RiskAgent a approuvé. |
| Réactivité | après un délai dépassé, interroge la plateforme avant tout renvoi (jamais d'envoi à l'aveugle). |
| Pro-activité | aucune : il ne décide pas quoi acheter. |
| Capacité sociale | n'accepte que des `ApprovedOrder` ; alimente le registre. |

## Paramètres

Quantités arrondies vers le bas au stepSize ; prix au tickSize dans le sens favorable ; STP `EXPIRE_TAKER` ; limites de débit : ralentit à 80 %, relance sur 429, jamais de relance sur 418 (pause de l'équipe) ; `recvWindow` 5 000 ms ; les stops de protection survivent à l'arrêt.

## Régimes favorables et défavorables

Sans objet.

## Conditions d'échec

Ordres au statut inconnu ; adresse IP bannie ; stop-limit non exécuté sur un trou de prix (garde-fou : sortie d'urgence au marché tant que le programme tourne) ; l'envoi des quantités à ccxt passe par des flottants formatés par la bibliothèque.

## Résultats mesurés

Test d'intégration testnet réel passé pour les ordres limite ; le stop-limit réel n'a pas encore été vérifié par le test opt-in.

## Statut dans le cycle de vie

Composant d'infrastructure critique.
