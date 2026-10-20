# Déploiement sur un serveur (VPS Linux) — modèles

Ces fichiers sont des **modèles non testés sur un vrai serveur** : relis-les et adapte les chemins et l'utilisateur avant usage.

## Préparer le serveur
1. Créer un utilisateur dédié sans droits d'administration : `sudo adduser --system --group orchestre`.
2. Copier le projet dans `/opt/orchestre` (dossiers `orchestre/` et `bin-env/`, ou recréer l'environnement Python).
3. Créer `/opt/orchestre/orchestre/.env` (droits `600`, propriétaire `orchestre`) à partir de `.env.example`. **Ne jamais mettre de clé dans un fichier de service.**
4. Vérifier l'horloge : `timedatectl status` doit indiquer `System clock synchronized: yes` (NTP, ENF-04). Une horloge décalée fait rejeter les ordres.
5. Le tableau de bord n'écoute que sur `127.0.0.1`. À distance : tunnel SSH (`ssh -L 8765:127.0.0.1:8765 serveur`), jamais d'ouverture du port au public.
6. Pour le mode réel (plus tard) : adresse IP fixe du serveur, clé Binance sans droit de retrait ni de transfert, restriction IP activée (EX-SEC-04, H-2).

## Installer les services
```bash
sudo cp deploy/orchestre.service deploy/orchestre-backup.service deploy/orchestre-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now orchestre orchestre-backup.timer
systemctl status orchestre
journalctl -u orchestre -f
```

## Arrêt d'urgence à distance
`ssh serveur 'touch /opt/orchestre/orchestre/var/STOP'` active le kill switch (fichier sentinelle) ; les stops de protection posés chez Binance restent en place.

## Sauvegarde
`orchestre-backup.timer` crée chaque nuit une copie cohérente de la base et en garde 14. **Copie-la hors du serveur** (ligne `rsync` à adapter dans `orchestre-backup.service`).
