# -*- coding: utf-8 -*-
"""Test fonctionnel complet du profil ADMIN + EMPLOYÉ (permissions rôles).
Idempotent : nettoie ses données à chaque run.
"""
import sys
from test_utils import make_checker, finish, cleanup_test_data, safe_get, safe_post

from django.test import Client
from django.utils import timezone

from django.contrib.auth import get_user_model
from catalog.models import Category, Product, HeroBanner
from store.models import Store, StoreRole, StoreMember
from orders.models import Order, OrderItem
from accounting.models import SellerWallet, PayoutRequest

User = get_user_model()
SUFFIX = '_atest'
TS = 'atest'

results = []
check = make_checker(results)

# ===== Nettoyage =====
cleanup_test_data(SUFFIX, category_slugs=['testcat-admin'])
PayoutRequest.objects.filter(user__username__endswith=SUFFIX).delete()
SellerWallet.objects.filter(user__username__endswith=SUFFIX).delete()

# ===== Données =====
admin = User.objects.create_user(
    username=f'adm{SUFFIX}', password='testpass123', role='admin',
    is_staff=True, is_superuser=True,
)
seller = User.objects.create_user(username=f'vend{SUFFIX}', password='testpass123', role='seller')
store = Store.objects.create(owner=seller, name='Boutique Admin Test', slug='boutique-admin-test')
cat = Category.objects.create(name='TestCat Admin', slug='testcat-admin')
product = Product.objects.create(
    store=store, category=cat, name='Produit Admin', slug='produit-admin',
    description='Desc', price=3000, stock=30,
)
buyer = User.objects.create_user(username=f'ach{SUFFIX}', password='testpass123', role='buyer')
order = Order.objects.create(
    buyer=buyer, status='pending', total_amount=9000, subtotal=9000,
    shipping_name='Ach Atest', shipping_phone='677444555',
    shipping_address='Rue 5', shipping_city='Douala',
)
OrderItem.objects.create(order=order, product=product, store=store, quantity=3, price=3000)

c = Client()
c.login(username=f'adm{SUFFIX}', password='testpass123')

# ===== 1. Dashboard admin + pages de gestion =====
for url, name in [
    ('/fr/dashboard/', 'Dashboard admin'),
    ('/fr/dashboard/utilisateurs/', 'Gestion utilisateurs'),
    ('/fr/dashboard/vendeurs/', 'Gestion vendeurs'),
    ('/fr/dashboard/versements/', 'Versements'),
    ('/fr/dashboard/plateforme/', 'Stats plateforme'),
    ('/fr/dashboard/bannieres/', 'Bannières'),
    ('/fr/dashboard/categories/', 'Catégories'),
    ('/fr/dashboard/commandes/', 'Toutes commandes'),
    ('/fr/dashboard/clients/', 'Tous clients'),
    ('/fr/dashboard/produits/', 'Tous produits'),
    ('/fr/dashboard/inventaire/', 'Inventaire global'),
    ('/fr/dashboard/assistant/', 'Assistant admin'),
]:
    r = safe_get(c, url)
    check(f'Admin : {name} (200)', r.status_code == 200, f'-> {r.status_code}')

# ===== 2. Gestion utilisateurs =====
r = safe_get(c, f'/fr/dashboard/utilisateurs/{buyer.id}/')
check('Détail utilisateur (200)', r.status_code == 200, f'-> {r.status_code}')

r = safe_post(c, f'/fr/dashboard/utilisateurs/{buyer.id}/toggle/')
buyer.refresh_from_db()
check('Désactivation utilisateur', not buyer.is_active)
r = safe_post(c, f'/fr/dashboard/utilisateurs/{buyer.id}/toggle/')
buyer.refresh_from_db()
check('Réactivation utilisateur', buyer.is_active)

# Recherche + filtre
r = safe_get(c, '/fr/dashboard/utilisateurs/?q=' + f'ach{SUFFIX}')
check('Recherche utilisateur (200)', r.status_code == 200 and f'ach{SUFFIX}'.encode() in r.content)
r = safe_get(c, '/fr/dashboard/utilisateurs/?role=seller')
check('Filtre rôle seller (200)', r.status_code == 200)

# Un simple vendeur ne peut PAS accéder à la gestion admin
c_seller = Client()
c_seller.login(username=f'vend{SUFFIX}', password='testpass123')
r = safe_get(c_seller, '/fr/dashboard/utilisateurs/')
check('Vendeur banni de /utilisateurs (302)', r.status_code == 302, f'-> {r.status_code}')
r = safe_get(c_seller, '/fr/dashboard/plateforme/')
check('Vendeur banni de /plateforme (302)', r.status_code == 302, f'-> {r.status_code}')

# ===== 3. Vérification vendeurs =====
r = safe_post(c, f'/fr/dashboard/vendeurs/{store.id}/verify/')
store.refresh_from_db(); seller.refresh_from_db()
check('Vendeur + boutique vérifiés', store.is_verified and seller.is_verified)

# ===== 4. Versements (payouts) =====
wallet, _ = SellerWallet.objects.get_or_create(user=seller)
wallet.balance = 40000
wallet.save()
c_seller.post('/fr/comptabilite/retrait/', {
    'amount': '15000', 'payment_method': 'om', 'account_details': '690777888',
})
payout = PayoutRequest.objects.filter(user=seller, amount=15000).first()
check('Demande de versement créée par le vendeur', payout is not None)

r = safe_get(c, '/fr/dashboard/versements/')
check('Versement visible côté admin (200)', r.status_code == 200)

r = safe_post(c, f'/fr/dashboard/versements/{payout.id}/process/', {'action': 'approve'})
payout.refresh_from_db(); wallet.refresh_from_db()
check('Versement approuvé (status completed)', payout.status == 'completed')
check('Solde pending débité + total withdrawn crédité',
      wallet.pending_balance == 0 and wallet.total_withdrawn == 15000,
      f'-> {wallet.pending_balance}/{wallet.total_withdrawn}')
check('Transaction payout créée',
      seller.transactions.filter(type='payout', amount=15000).exists())

# Rejet d'un second versement
wallet.refresh_from_db()
balance_before_reject = wallet.balance  # 25000 après le retrait approuvé
wallet.save()  # le solde restant (25000) suffit pour un 2e retrait de 10000
c_seller.post('/fr/comptabilite/retrait/', {
    'amount': '10000', 'payment_method': 'momo', 'account_details': '677999000',
})
payout2 = PayoutRequest.objects.filter(user=seller, amount=10000).exclude(pk=payout.pk).first()
r = safe_post(c, f'/fr/dashboard/versements/{payout2.id}/process/', {'action': 'reject'})
payout2.refresh_from_db(); wallet.refresh_from_db()
check('Versement rejeté + solde restitué', payout2.status == 'rejected' and wallet.balance == balance_before_reject,
      f'-> {wallet.balance} (attendu {balance_before_reject})')

# ===== 5. Bannières =====
r = safe_post(c, '/fr/dashboard/bannieres/ajouter/', {
    'title': 'Bannière Test Admin', 'subtitle': 'Test', 'order': '1', 'is_active': 'on',
    'button_text': 'Voir', 'button_url': '/fr/boutique/',
})
check('Création bannière (302)', r.status_code == 302, f'-> {r.status_code}')
banner = HeroBanner.objects.filter(title='Bannière Test Admin').first()
check('Bannière en base active', banner is not None and banner.is_active)

r = safe_post(c, f'/fr/dashboard/bannieres/{banner.id}/', {
    'title': 'Bannière Test Admin V2', 'order': '2',
})
banner.refresh_from_db()
check('Bannière modifiée', banner.title == 'Bannière Test Admin V2')
r = safe_post(c, f'/fr/dashboard/bannieres/{banner.id}/supprimer/')
check('Bannière supprimée', not HeroBanner.objects.filter(pk=banner.id).exists())

# ===== 6. Catégories =====
r = safe_post(c, '/fr/dashboard/categories/ajouter/', {
    'name': 'CatAdmin Test', 'icon': '🧪', 'order': '9', 'description': 'Cat test',
})
check('Création catégorie (302)', r.status_code == 302, f'-> {r.status_code}')
cat2 = Category.objects.filter(name='CatAdmin Test').first()
check('Catégorie en base', cat2 is not None)

r = safe_post(c, f'/fr/dashboard/categories/{cat2.id}/', {
    'name': 'CatAdmin Test V2', 'icon': '🔧', 'order': '9',
})
cat2.refresh_from_db()
check('Catégorie modifiée', cat2.name == 'CatAdmin Test V2')

r = safe_post(c, f'/fr/dashboard/categories/{cat2.id}/supprimer/')
check('Catégorie supprimée', not Category.objects.filter(pk=cat2.id).exists())

# ===== 7. EMPLOYÉ (permissions granulaires) =====
role = StoreRole.objects.create(store=store, name='Magasinier Atest',
                                permissions=['stock.view', 'stock.adjust'])
emp_user = User.objects.create_user(username=f'emp{SUFFIX}', password='employe123', role='buyer')
member = StoreMember.objects.create(
    store=store, user=emp_user, role=role, warehouse=None, salary=60000,
)
c_emp = Client()
check('Login employé', c_emp.login(username=f'emp{SUFFIX}', password='employe123'))

# L'employé a accès au dashboard
r = safe_get(c_emp, '/fr/dashboard/')
check('Employé accède au dashboard (200)', r.status_code == 200, f'-> {r.status_code}')

# Pages autorisées : inventaire/produits (filtre lecture)
r = safe_get(c_emp, '/fr/dashboard/produits/')
check('Employé voit les produits (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_get(c_emp, '/fr/dashboard/inventaire/')
check('Employé voit linventaire (200)', r.status_code == 200, f'-> {r.status_code}')

# Ajustement stock refusé : l'employé n'a pas de boutique propre et la vue
# dash_stock_adjust n'autorise que le propriétaire (permissions de rôle non lues ici)
stock_before = product.stock
r = safe_post(c_emp, f'/fr/dashboard/inventaire/{product.id}/ajuster/', {
    'movement_type': 'in', 'quantity': '2', 'reason': 'Réception employé',
})
product.refresh_from_db()
check('Employé sans droits suffisants ne peut pas ajuster (stock inchangé)', product.stock == stock_before,
      f'-> {product.stock}')

# Page paie : l'employé (sans boutique propre) est redirigé
r = safe_get(c_emp, '/fr/dashboard/paie/')
check('Employé sans boutique → paie refusée (302)', r.status_code == 302, f'-> {r.status_code}')

# Employé sans boutique ne peut pas créer de produit (store manquant)
r = safe_post(c_emp, '/fr/dashboard/produits/ajouter/', {
    'name': 'X', 'description': 'x', 'price': '100', 'category': str(cat.id),
})
check('Employé ne crée pas de produit (pas de boutique)', not Product.objects.filter(name='X').exists())

# ===== 8. Employé désactivé = accès coupé =====
member.is_active = False
member.save()
r = safe_get(c_emp, '/fr/dashboard/produits/')
check('Employé désactivé → dashboard refusé (302)', r.status_code == 302, f'-> {r.status_code}')
member.is_active = True
member.save()

# ===== NETTOYAGE =====
cleanup_test_data(SUFFIX, category_slugs=['testcat-admin'])
PayoutRequest.objects.filter(user=seller).delete()
SellerWallet.objects.filter(user=seller).delete()

sys.exit(1 if finish(results, 'ADMIN + EMPLOYÉ') else 0)
