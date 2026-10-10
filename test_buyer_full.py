# -*- coding: utf-8 -*-
"""Test fonctionnel complet du parcours acheteur (idempotent)."""
import os, sys, django

import test_utils  # noqa: F401 — base de test isolée (copie de db.sqlite3)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DJANGO_ALLOWED_HOSTS'] = '127.0.0.1,localhost,testserver'
django.setup()

from django.test.utils import setup_test_environment
setup_test_environment()

from django.test import Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from catalog.models import Category, Product
Category.objects.filter(slug='testcatbuyer').delete()
from store.models import Store
from marketing.models import PromoCode

User = get_user_model()
SUFFIX = '_btest'
TS = 'btest'

results = []

def check(name, condition, extra=''):
    results.append((name, bool(condition)))
    print(('PASS' if condition else 'FAIL'), '-', name, extra)

# ===== Nettoyage des runs précédents (idempotence) =====
for m in ['Wishlist', 'Review']:
    try:
        mod = __import__(f'catalog.models', fromlist=[m])
        getattr(mod, m).objects.filter(user__username__contains=TS).delete()
    except AttributeError:
        pass
from orders.models import Order, RFQ, Quote
from messaging.models import Conversation, Message
from cart.models import CartItem
CartItem.objects.filter(user__username__contains=TS).delete()
Order.objects.filter(buyer__username__contains=TS).delete()
Conversation.objects.filter(buyer__username__contains=TS).delete()
User.objects.filter(username__contains=TS).delete()
PromoCode.objects.filter(code__startswith='BT').delete()

# ===== Données de test =====
seller = User.objects.create_user(username=f'vend{TS}', password='testpass123', role='seller')
store = Store.objects.create(owner=seller, name='Boutique Test Buyer', slug='boutique-test-buyer')
# La boutique accepte MoMo : seuls les moyens activés par le vendeur sont proposés
from billing.models import PaymentConfig
PaymentConfig.objects.update_or_create(user=seller, defaults={'momo_enabled': True, 'momo_number': '+237677000111', 'cash_enabled': True})
cat = Category.objects.create(name='TestCatBuyer', slug='testcatbuyer')
product = Product.objects.create(
    store=store, category=cat, name='Produit Test Buyer', slug='produit-test-buyer',
    description='Desc', price=5000, stock=20,
)
# Recréer le stock de départ à chaque run
product.stock = 20
product.save(update_fields=['stock'])
promo = PromoCode.objects.create(
    store=store, code='BTTEST10', discount_type='percentage', discount_value=10,
    valid_to=timezone.now() + timedelta(days=30), min_purchase_amount=1000,
)

c = Client()

# ===== 1. Inscription / Connexion =====
r = c.post('/fr/compte/inscription/', {
    'username': f'acheteur{TS}', 'email': 'acheteur_btest@test.com',
    'password': 'testpass123', 'role': 'buyer',
    'first_name': 'Jean', 'last_name': 'Test',
})
check('Inscription acheteur (redirection 302)', r.status_code == 302, f'-> {r.status_code}')
check('Utilisateur créé avec rôle buyer', User.objects.filter(username=f'acheteur{TS}', role='buyer').exists())

c.get('/fr/compte/deconnexion/')
r = c.post('/fr/compte/connexion/', {'username': f'acheteur{TS}', 'password': 'testpass123'})
check('Connexion (redirection 302)', r.status_code == 302, f'-> {r.status_code}')

# ===== 2. Pages publiques du catalogue =====
r = c.get('/fr/')
check("Page d'accueil (200)", r.status_code == 200)
r = c.get('/fr/boutique/')
check('Liste des produits (200)', r.status_code == 200)
r = c.get('/fr/boutique/categorie/testcatbuyer/')
check('Page catégorie (200)', r.status_code == 200)
r = c.get('/fr/boutique/produit/produit-test-buyer/')
check('Page produit (200)', r.status_code == 200)
r = c.get('/fr/boutique/recherche/?q=Produit')
check('Recherche (200)', r.status_code == 200)
r = c.get('/fr/boutique/api/search-autocomplete/?q=Produ')
check('Autocomplete API (200 + JSON)', r.status_code == 200 and r.json().get('suggestions') is not None)
r = c.get('/fr/a-propos/')
check('Page à propos (200)', r.status_code == 200)
r = c.get('/fr/faq/')
check('Page FAQ (200)', r.status_code == 200)
r = c.get('/fr/contact/')
check('Page contact (200)', r.status_code == 200)
r = c.get('/fr/devenir-vendeur/')
check('Page devenir vendeur (200)', r.status_code == 200)
r = c.get('/fr/compte/mot-de-passe-oublie/')
check('Page mot de passe oublié (200)', r.status_code == 200)

# ===== 3. Wishlist =====
r = c.post(f'/fr/boutique/wishlist/toggle/{product.id}/')
check('Toggle wishlist ON (302)', r.status_code == 302)
from catalog.models import Wishlist
check('Wishlist en base', Wishlist.objects.filter(user__username=f'acheteur{TS}', product=product).exists())
r = c.post(f'/fr/boutique/wishlist/toggle/{product.id}/')
check('Toggle wishlist OFF (302)', r.status_code == 302)
check('Wishlist supprimée', not Wishlist.objects.filter(user__username=f'acheteur{TS}', product=product).exists())
r = c.get('/fr/boutique/wishlist/')
check('Page wishlist (200)', r.status_code == 200)

# ===== 4. Avis produit =====
r = c.post(f'/fr/boutique/avis/{product.id}/', {'rating': 5, 'comment': 'Excellent produit', 'name': 'Jean'})
check('Ajout avis (302)', r.status_code == 302)
from catalog.models import Review
check('Avis en base', Review.objects.filter(product=product, user__username=f'acheteur{TS}').exists())

# ===== 5. Panier =====
r = c.post(f'/fr/panier/ajouter/{product.id}/', {'quantity': 2})
check('Ajout au panier (302)', r.status_code == 302)
r = c.get('/fr/panier/')
check('Page panier (200)', r.status_code == 200)
check('Panier contient 2 articles', CartItem.objects.filter(user__username=f'acheteur{TS}', product=product, quantity=2).exists())

# Mise à jour quantité
r = c.post('/fr/panier/modifier/', {'product_id': str(product.id), 'action': 'increase'})
check('Augmenter quantité (302)', r.status_code == 302)
check('Quantité passée à 3', CartItem.objects.filter(user__username=f'acheteur{TS}', product=product, quantity=3).exists())
r = c.post('/fr/panier/modifier/', {'product_id': str(product.id), 'action': 'decrease'})
check('Diminuer quantité (302)', r.status_code == 302)
check('Quantité redescendue à 2', CartItem.objects.filter(user__username=f'acheteur{TS}', product=product, quantity=2).exists())

# ===== 6. Code promo =====
r = c.post('/fr/panier/promo/appliquer/', {'promo_code': 'BTTEST10'})
check('Appliquer code promo (302)', r.status_code == 302)
check('Code promo en session', c.session.get('promo_code') == 'BTTEST10')
r = c.get('/fr/panier/')
# 5000*2 = 10000, -10% = 1000 de réduc
check('Réduction promo visible dans le contexte (1000)',
      r.context is not None and r.context.get('discount') == 1000,
      f"-> discount={r.context.get('discount') if r.context else 'contexte indisponible'}")

# ===== 7. Checkout / Commande =====
r = c.post('/fr/panier/commander/', {
    'payment_method': 'momo', 'shipping_name': 'Jean Test',
    'shipping_phone': '677123456', 'shipping_address': 'Rue 1',
    'shipping_city': 'Douala',
})
check('Commande créée (redirection 302)', r.status_code == 302, f'-> {r.status_code}')
from orders.models import Order, OrderItem
order = Order.objects.filter(buyer__username=f'acheteur{TS}').first()
check('Commande en base avec numéro ALB-', order is not None and order.order_number.startswith('ALB-'))
# Total = 10000 + 2000 livraison - 1000 promo = 11000
check('Montant total correct (11000 = 2x5000 + 2000 - 1000 promo)', order and order.total_amount == 11000,
      f'-> {order.total_amount if order else "N/A"}')
check('Réduction enregistrée sur la commande', order and order.discount_amount == 1000)
product.refresh_from_db()
check('Stock décrémenté (20 → 18)', product.stock == 18, f'-> {product.stock}')
check('OrderItems créés', OrderItem.objects.filter(order=order).count() == 1)
check('Panier vidé après commande', not CartItem.objects.filter(user__username=f'acheteur{TS}').exists())

promo.refresh_from_db()
check('Compteur du code promo incrémenté', promo.usage_count == 1, f'-> {promo.usage_count}')

# Pages de suivi
r = c.get('/fr/commandes/')
check('Liste des commandes (200)', r.status_code == 200)
r = c.get(f'/fr/commandes/{order.order_number}/')
check('Détail commande (200)', r.status_code == 200)
r = c.get(f'/fr/commandes/succes/{order.order_number}/')
check('Page succès commande (200)', r.status_code == 200)

# Sécurité : un autre acheteur ne peut PAS voir la commande
other = User.objects.create_user(username=f'autre{TS}', password='testpass123', role='buyer')
c2 = Client()
c2.login(username=f'autre{TS}', password='testpass123')
r = c2.get(f'/fr/commandes/{order.order_number}/')
check('Autre acheteur → commande inaccessible (404)', r.status_code == 404, f'-> {r.status_code}')

# ===== 8. RFQ (demande de devis) =====
r = c.post('/fr/commandes/rfq/nouveau/', {
    'product_name': 'Machine à coudre', 'description': 'Je cherche une machine industrielle',
    'quantity': 5, 'category': cat.id,
})
check('Création demande de devis (302)', r.status_code == 302, f'-> {r.status_code}')
from orders.models import RFQ
rfq = RFQ.objects.filter(buyer__username=f'acheteur{TS}').first()
check('RFQ en base', rfq is not None)
r = c.get('/fr/commandes/rfq/mes-demandes/')
check('Mes demandes de devis (200)', r.status_code == 200)

# ===== 9. Messagerie =====
r = c.get(f'/fr/messages/nouveau/{seller.id}/')
check('Création conversation avec vendeur (302)', r.status_code == 302)
from messaging.models import Conversation
convo = Conversation.objects.filter(buyer__username=f'acheteur{TS}', seller=seller).first()
check('Conversation créée', convo is not None)
r = c.post(f'/fr/messages/conversation/{convo.id}/', {'content': 'Bonjour, question sur le produit'})
check('Envoi message (redirection vers la conversation)', r.status_code == 302 and r.url.endswith(f'/conversation/{convo.id}/'), f'-> {r.status_code}')
check('Message enregistré', convo.messages.filter(content__contains='Bonjour').exists())
r = c.get('/fr/messages/')
check('Boîte de réception (200)', r.status_code == 200)

# ===== 10. Dashboard refusé à l'acheteur (réservé aux vendeurs) =====
r = c.get('/fr/dashboard/')
check('Dashboard refusé à l\'acheteur (302 → accueil)', r.status_code == 302)

# ===== 11. Déconnexion / accès protégé =====
c.get('/fr/compte/deconnexion/')
r = c.get('/fr/panier/commander/')
check('Checkout sans connexion → redirection login', r.status_code == 302 and '/connexion' in r.url, f'-> {r.status_code}')

# ===== RÉSULTATS =====
print()
failed = [n for n, ok in results if not ok]
print(f'RESULTAT : {len(results) - len(failed)}/{len(results)} reussis')
if failed:
    print('ECHECS :')
    for f_ in failed:
        print('  -', f_)
sys.exit(1 if failed else 0)
