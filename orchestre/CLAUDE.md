# CLAUDE.md — ORCHESTRE

- Source de vérité : `../SPEC.md` (v2.1). Le lire avant toute tâche.
- Travailler phase par phase (§21) ; proposer un plan avant de coder.
- Testnet par défaut ; jamais de code qui contacte le réel sans la double confirmation EX-SEC-01.
- Ne jamais lire, afficher, journaliser ni versionner de clés.
- Seul l'`ExecutionAgent` écrit sur Binance ; aucun ordre sans accord du `RiskAgent` ; aucun retrait.
- Ce qui augmente le risque exige une validation humaine ; ce qui le réduit peut être automatique.
- Décimal pour l'argent, UTC pour le temps, logique de décision pure, raison lisible pour chaque décision.
- Tests d'abord pour le risque, les filtres et le registre ; citer les identifiants d'exigences.
- Demander avant d'ajouter une dépendance.

Lancer les tests : `cd orchestre && ../bin-env/bin/python -m pytest`
