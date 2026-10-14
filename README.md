# Comptoir

Place de marché en ligne pour les commerçants d'Afrique centrale : chaque vendeur a sa boutique en ligne,
sa caisse en magasin, son stock par entrepôt, ses factures, son équipe et ses outils marketing, dans un même
tableau de bord. Les acheteurs commandent et paient en ligne (Mobile Money, carte) ou à la livraison.

Projet Django 6, interface en français, montants en francs CFA.

## Fonctionnalités

**Acheteurs**
- Catalogue, recherche, catégories, ventes flash, recommandations personnalisées
- Panier multi-boutiques, paiement en ligne par boutique (Notch Pay, Stripe) ou à la livraison
- Suivi de commande, demandes de devis (B2B), messagerie avec les vendeurs, espace client

**Vendeurs** (`/dashboard/`)
- Produits, stock multi-entrepôts, transferts, réceptions et mouvements tracés
- Caisse en magasin : sessions, paiement mixte, remboursements, ticket Z, rapports
- Commandes, ventes directes, factures PDF, comptabilité par entrepôt
- Employés, rôles et droits, paie
- Marketing : codes promo, campagnes, e-mailings, WhatsApp / Telegram, fidélité
- WhatsApp (Evolution API) : confirmations et suivi de commande, factures, conversations clients
- Dropshipping : fournisseurs par fichier CSV / Excel (connexion CJ Dropshipping en préparation)
- Assistant IA (Claude) qui répond sur les données de la boutique

**Formules vendeurs** (`billing/`)
- Essai gratuit de 30 jours, puis Starter, Business, Premium ou Entreprise
- Aucune commission sur les ventes : Comptoir se rémunère par l'abonnement
- Limites appliquées par formule (produits, entrepôts, caisses, employés, fonctions) : `billing/limits.py`
- Paiement de la formule par Mobile Money, validé par l'administrateur ; accès offert possible

**Administration** (`/dashboard/admin/…`)
- Commandes, boutiques, produits, avis, utilisateurs, revenus, versements, dépenses
- Abonnements : paiements à valider, numéros MoMo / Orange Money, accès offerts
- Annonces affichées en haut du site, ciblées par public et par dates

## Installation (développement)

Prérequis : Python 3.12 ou plus récent, Git. Docker seulement pour WhatsApp.

```bash
git clone https://gitlab.com/digisoft150/comptoire.git
cd comptoire
python -m venv .venv
.venv\Scripts\activate            # Windows (macOS / Linux : source .venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser  # compte administrateur
python manage.py runserver
```

Le site est sur http://127.0.0.1:8000/ (les pages sont sous `/fr/`).

### Données de démonstration

```bash
python manage.py seed_data            # boutiques, catégories, comptes et commandes de démo
python manage.py seed_demo_catalog    # ~90 vrais produits avec photos, prix en FCFA et avis
```

`seed_data` crée des comptes de test (administrateur `admin`, vendeur `techcm`, acheteur `jean_k` ;
mots de passe dans `catalog/management/commands/seed_data.py`). À ne jamais lancer en production.
Les photos de `seed_demo_catalog` viennent du catalogue libre [DummyJSON](https://dummyjson.com), prévu pour
les démonstrations : remplacez-les par vos propres photos pour un vrai site.

## Configuration

Tout se règle par variables d'environnement (voir `config/settings.py`). En développement, les valeurs par
défaut suffisent pour démarrer.

| Variable | Rôle |
|---|---|
| `DJANGO_SECRET_KEY` | Clé secrète. **Obligatoire en production** (la valeur par défaut est publique) |
| `DJANGO_DEBUG` | `False` en production |
| `DJANGO_ALLOWED_HOSTS` | Domaines autorisés, séparés par des virgules |
| `SITE_URL` | Adresse publique du site (liens des e-mails et de WhatsApp) |
| `DJANGO_DB_PATH`, `DJANGO_MEDIA_ROOT` | Emplacement de la base SQLite et des fichiers envoyés |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`, `DJANGO_EMAIL_BACKEND` | Envoi des e-mails (voir `EMAIL_SETUP.md`) |
| `ANTHROPIC_API_KEY`, `ASSISTANT_MODEL`, `ASSISTANT_AI_ENABLED` | Assistant IA du tableau de bord |
| `WHATSAPP_ENABLED`, `EVOLUTION_API_URL`, `EVOLUTION_API_KEY`, `WHATSAPP_WEBHOOK_SECRET`, `WHATSAPP_WEBHOOK_URL`, `WHATSAPP_DELIVERY_MODE` | WhatsApp (voir ci-dessous) |

Les clés des comptes de paiement (Notch Pay, Stripe) et de CJ Dropshipping se saisissent dans le tableau de
bord ; elles sont chiffrées en base avec `DJANGO_SECRET_KEY` (`config/secretbox.py`) : ne changez pas cette
clé une fois le site en service.

## WhatsApp

WhatsApp passe par [Evolution API](https://github.com/EvolutionAPI/evolution-api), qui tourne dans Docker :

```bash
cd deploy/evolution
cp .env.example .env      # puis remplir les secrets
docker compose up -d
```

En développement, Django lit `deploy/evolution/.env` tout seul. Lancez alors le serveur avec
`python manage.py runserver 0.0.0.0:8000` pour qu'Evolution API puisse joindre le webhook, puis connectez le
numéro depuis Tableau de bord > WhatsApp. Guide complet : `deploy/evolution/README.md`.

## Tâches planifiées

| Commande | Fréquence | Rôle |
|---|---|---|
| `billing_subscriptions` | 1 fois par jour | Rappels de fin d'abonnement (J-7, J-2) et expiration ; `--dry-run` pour simuler |
| `billing_check_payments` | toutes les 10 min | Revérifie les paiements en ligne restés en attente |
| `send_scheduled_emails` | régulièrement (ex. toutes les 5 min) | Envoie les e-mailings programmés dont l'heure est arrivée |
| `dropship_sync --minutes 10` | toutes les heures | Commandes, suivis, prix et stocks des fournisseurs connectés |
| `dropship_images` | toutes les heures | Télécharge les photos des produits fournisseur importés |
| `whatsapp_worker` | en continu | File d'envoi WhatsApp en production (`WHATSAPP_DELIVERY_MODE=worker`) |
| `purge_browsing` | 1 fois par jour | Efface l'historique de navigation de plus de 90 jours (recommandations) |

Exemples de services systemd pour Linux : `deploy/billing/` (abonnements) et `deploy/evolution/` (worker WhatsApp).

## Tests

```bash
python manage.py test accounting accounts billing cart catalog dashboard invoicing messaging orders store
```

Les fichiers `test_*.py` à la racine sont des parcours fonctionnels complets (vendeur, acheteur, admin, marketing) ;
ils écrivent dans la base de développement et se nettoient eux-mêmes :

```bash
python manage.py test test_seller_full
```

## Organisation du code

| Dossier | Contenu |
|---|---|
| `config/` | Réglages, adresses, PWA, captcha, chiffrement des secrets |
| `accounts/` | Comptes, inscription acheteur / vendeur, espace client |
| `catalog/` | Produits, catégories, avis, recommandations, pages publiques |
| `store/` | Boutiques, employés, rôles, droits d'accès (`store/access.py`) |
| `cart/`, `orders/` | Panier, commandes, devis B2B |
| `billing/` | Formules et limites, abonnements, paiement en ligne, boosts |
| `accounting/` | Portefeuilles vendeurs, transactions, versements |
| `inventory/` | Entrepôts, stock, transferts |
| `pos/` | Caisse en magasin |
| `invoicing/` | Factures PDF |
| `marketing/` | Promotions, campagnes, e-mailings, fidélité |
| `messaging/` | Messagerie, notifications, alertes et annonces du site |
| `dashboard/` | Tableau de bord vendeur et administrateur, assistant IA |
| `dropship/` | Dropshipping |
| `whatsapp/` | Intégration WhatsApp (Evolution API) |
| `templates/`, `static/` | Gabarits et fichiers statiques |
| `deploy/` | Fichiers de déploiement (Docker, systemd) |

## Mise en production

- Définir `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS` et `SITE_URL`
- `python manage.py collectstatic`, puis servir le site avec un serveur WSGI (Gunicorn…) derrière un proxy HTTPS
- Programmer les tâches ci-dessus et lancer `whatsapp_worker` comme service
- Renseigner les numéros de paiement des formules dans Admin > Abonnements
- Ne versionnez jamais `db.sqlite3`, `media/` ni les fichiers `.env` (déjà dans `.gitignore`)
