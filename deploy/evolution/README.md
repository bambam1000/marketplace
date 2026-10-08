# WhatsApp pour AfriMarket (Evolution API, mode Baileys)

AfriMarket envoie et reçoit des messages WhatsApp via [Evolution API](https://github.com/evolution-foundation/evolution-api)
(Apache 2.0), lancé à côté de Django. Le mode **Baileys** connecte un vrai numéro WhatsApp en scannant un QR code,
comme WhatsApp Web.

> ⚠️ Baileys n'est pas une API officielle de Meta : un numéro qui envoie beaucoup de messages non sollicités
> peut être **banni**. AfriMarket n'envoie que des messages transactionnels (commandes, factures) à des clients
> qui ont commandé, avec un délai entre les envois, et respecte « STOP ». N'utilisez pas votre numéro personnel principal.

## Fonctionnement

- **Envoi** : chaque message est mis en file (table `WhatsAppMessage`) ; la page web ne l'attend jamais.
  Le **worker** (`python manage.py whatsapp_worker`) l'envoie, et relance les échecs après 1 min, 5 min, 15 min, 1 h, 6 h
  (6 essais au maximum). Il vérifie aussi chaque minute que les numéros sont toujours connectés.
- **Réception** : les messages des clients arrivent dans **Dashboard → WhatsApp** (conversations), avec le client
  reconnu par son numéro et ses dernières commandes ; on répond depuis le dashboard.
- **STOP / START** : un client qui écrit STOP (ou ARRÊT) ne reçoit plus de message automatique ; START le réinscrit.

## Test en local (Windows)

1. **Installer WSL puis Docker Desktop** (PowerShell **administrateur**, un redémarrage est demandé) :
   ```powershell
   wsl --install
   # redémarrer, puis :
   winget install -e --id Docker.DockerDesktop
   ```
   Lancer Docker Desktop une première fois et attendre « Engine running ».
2. **Démarrer Evolution API** puis Django :
   ```powershell
   cd deploy\evolution ; docker compose up -d ; cd ..\..
   python manage.py runserver 0.0.0.0:8000
   ```
   En développement, Django lit tout seul `deploy/evolution/.env` (clé API, secret du webhook, URL
   `host.docker.internal`) : aucune variable à définir. `0.0.0.0` permet au conteneur de joindre Django.
   Les messages partent en arrière-plan sans worker ; `start_local.ps1` fait tout ceci d'un coup et ouvre aussi le worker.
3. **Connecter** : http://127.0.0.1:8000/fr/dashboard/whatsapp/ → Connexion → « Connecter WhatsApp »,
   puis sur le téléphone **de test** : WhatsApp → Appareils connectés → Connecter un appareil → scanner.
4. **Tester** : « Envoyer un message de test » vers votre numéro, puis répondez depuis le téléphone :
   la réponse apparaît dans Conversations. Écrivez « STOP » pour tester la désinscription.

## Production (serveur Linux)

```bash
cd deploy/evolution
cp .env.example .env          # remplacer chaque CHANGER_MOI
docker compose up -d
```

Variables de Django :

| Variable | Exemple | Rôle |
|---|---|---|
| `WHATSAPP_ENABLED` | `1` | Active WhatsApp (désactivé par défaut : aucun appel réseau) |
| `EVOLUTION_API_URL` | `http://127.0.0.1:8081` | Adresse d'Evolution API |
| `EVOLUTION_API_KEY` | même valeur que `AUTHENTICATION_API_KEY` | Clé de l'API |
| `WHATSAPP_WEBHOOK_SECRET` | un secret aléatoire | Envoyé par Evolution dans l'en-tête `X-AfriMarket-Secret` |
| `WHATSAPP_WEBHOOK_URL` | `https://afrimarket.cm/whatsapp/webhook/` | URL publique appelée par Evolution (défaut : `SITE_URL` + `/whatsapp/webhook/`) |
| `WHATSAPP_DELIVERY_MODE` | `worker` | `worker` en production ; `thread` (défaut) envoie depuis un fil d'arrière-plan |
| `WHATSAPP_PLATFORM_INSTANCE` | `afrimarket` | Nom de l'instance du numéro AfriMarket |

Worker en service permanent (redémarre tout seul) :

```bash
sudo cp deploy/evolution/afrimarket-whatsapp-worker.service /etc/systemd/system/   # adapter les chemins
sudo systemctl daemon-reload && sudo systemctl enable --now afrimarket-whatsapp-worker
```

`python manage.py whatsapp_retry` relance tout de suite les échecs (utile après une panne) ; pas besoin de cron.

## Connecter les numéros

- **Numéro AfriMarket** (admin) : Dashboard → WhatsApp → Connexion → « Numéro AfriMarket ».
  Sert aux alertes vendeurs (nouvelle commande) et de secours pour les clients des boutiques non connectées.
- **Numéro du vendeur** : Dashboard → WhatsApp → Connexion, avec le téléphone de la boutique.

## Marque Evolution API

Le code est sous Apache 2.0. Le nom et le logo « Evolution API » sont des marques d'Evolution Foundation
(voir leur `TRADEMARKS.md`) : AfriMarket n'expose pas leur interface aux vendeurs et ne réutilise pas leur logo.
