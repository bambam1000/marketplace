# -*- coding: utf-8 -*-
"""Test fonctionnel complet du profil VENDEUR (idempotent).
Couvre : dashboard, boutique, produits, stock/inventaire, entrepôts, transferts,
employés/rôles/paie, ventes directes, clients, commandes, RFQ vendeur, assistant.
"""
import sys
from test_utils import (make_checker, finish, cleanup_test_data,
                        safe_get, safe_post)

from django.test import Client
from django.utils import timezone
from datetime import timedelta

from django.contrib.auth import get_user_model
from catalog.models import Category, Product
from store.models import Store, StoreRole, StoreMember, Payslip
from orders.models import Order, RFQ, Quote
from inventory.models import Warehouse, StockTransfer
from accounting.models import SellerWallet, PayoutRequest

User = get_user_model()
SUFFIX = '_stest'
TS = 'stest'

results = []
check = make_checker(results)

# ===== Nettoyage des runs précédents (idempotence) =====
cleanup_test_data(SUFFIX, promo_prefix='ST', category_slugs=['testcat-seller'])
StockTransfer.objects.filter(reference__startswith='TRF-ST').delete()
SellerWallet.objects.filter(user__username__endswith=SUFFIX).delete()

# ===== Données de base =====
seller = User.objects.create_user(username=f'vend{SUFFIX}', password='testpass123', role='seller')
store = Store.objects.create(owner=seller, name='Boutique Seller Test', slug='boutique-seller-test')
cat = Category.objects.create(name='TestCat Seller', slug='testcat-seller')
product = Product.objects.create(
    store=store, category=cat, name='Produit Seller', slug='produit-seller',
    description='Desc', price=4000, stock=50,
)

c = Client()
c.login(username=f'vend{SUFFIX}', password='testpass123')

# ===== 1. Accès au dashboard vendeur =====
for url, name in [
    ('/fr/dashboard/', 'Dashboard vendeur'),
    ('/fr/dashboard/commandes/', 'Page commandes'),
    ('/fr/dashboard/ventes/', 'Page ventes'),
    ('/fr/dashboard/devis/', 'Page devis (RFQ)'),
    ('/fr/dashboard/produits/', 'Page produits'),
    ('/fr/dashboard/inventaire/', 'Page inventaire'),
    ('/fr/dashboard/entrepots/', 'Page entrepôts'),
    ('/fr/dashboard/transferts/', 'Page transferts'),
    ('/fr/dashboard/employes/', 'Hub employés'),
    ('/fr/dashboard/employes/liste/', 'Liste employés'),
    ('/fr/dashboard/roles/', 'Page rôles'),
    ('/fr/dashboard/paie/', 'Page paie'),
    ('/fr/dashboard/clients/', 'Page clients'),
    ('/fr/dashboard/boutique/', 'Page boutiques'),
    ('/fr/dashboard/boutique/parametres/', 'Paramètres boutique'),
    ('/fr/dashboard/parametres/', 'Paramètres dashboard'),
    ('/fr/dashboard/notifications/', 'Notifications dashboard'),
    ('/fr/dashboard/assistant/', 'Assistant IA'),
]:
    r = safe_get(c, url)
    check(f'{name} (200)', r.status_code == 200, f'-> {r.status_code}')

# ===== 2. Assistant IA (question) =====
r = safe_post(c, '/fr/dashboard/assistant/', {'question': 'Comment vont mes ventes ?'})
check('Assistant IA répond (200)', r.status_code == 200, f'-> {r.status_code}')

# ===== 3. CRUD Produit =====
r = safe_get(c, '/fr/dashboard/produits/ajouter/')
check('Formulaire ajout produit (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_post(c, '/fr/dashboard/produits/ajouter/', {
    'name': 'Produit Créé Test', 'description': 'Desc créée', 'price': '2500',
    'stock': '10', 'category': str(cat.id), 'min_order': '1',
})
check('Création produit (302)', r.status_code == 302, f'-> {r.status_code}')
created = Product.objects.filter(name='Produit Créé Test', store=store).first()
check('Produit créé en base', created is not None)
r = safe_post(c, f'/fr/dashboard/produits/{created.id}/', {
    'name': 'Produit Créé Modifié', 'description': 'Desc modifiée', 'price': '3000',
    'category': str(cat.id),
})
check('Édition produit (302)', r.status_code == 302, f'-> {r.status_code}')
created.refresh_from_db()
check('Produit modifié en base (prix 3000)', created.price == 3000, f'-> {created.price}')

# Sécurité : un autre vendeur ne peut pas éditer notre produit
other_seller = User.objects.create_user(username=f'vendscan{SUFFIX}', password='testpass123', role='seller')
Store.objects.create(owner=other_seller, name='Boutique Scan', slug='boutique-scan')
c_scan = Client()
c_scan.login(username=f'vendsca{SUFFIX}', password='testpass123')
r = safe_post(c_scan, f'/fr/dashboard/produits/{product.id}/', {'name': 'Hack', 'price': '1', 'description': 'x'})
product.refresh_from_db()
check('Vendeur étranger ne peut pas éditer le produit', product.price == 4000, f'-> {product.price}')

# ===== 4. Stock : ajustement + traçabilité =====
stock_before = product.stock
r = safe_post(c, f'/fr/dashboard/inventaire/{product.id}/ajuster/', {
    'movement_type': 'in', 'quantity': '5', 'reason': 'Réappro test',
})
check('Ajustement stock entrée (302)', r.status_code == 302, f'-> {r.status_code}')
product.refresh_from_db()
check('Stock incrémenté (+5)', product.stock == stock_before + 5, f'-> {product.stock}')
from catalog.models import StockMovement
mv = StockMovement.objects.filter(product=product, movement_type='in').first()
check('Mouvement tracé', mv is not None and mv.quantity == 5)

r = safe_post(c, f'/fr/dashboard/inventaire/{product.id}/ajuster/', {
    'movement_type': 'out', 'quantity': '3', 'reason': 'Casse test',
})
product.refresh_from_db()
check('Stock décrémenté (-3)', product.stock == stock_before + 2, f'-> {product.stock}')

# Exports
r = safe_get(c, '/fr/dashboard/inventaire/export/pdf/')
check('Export PDF mouvements (PDF)', r.status_code == 200 and b'%PDF' in r.content[:5], f'-> {r.status_code}')
r = safe_get(c, '/fr/dashboard/inventaire/export/excel/')
check('Export Excel mouvements (xlsx)', r.status_code == 200 and b'PK' in r.content[:2], f'-> {r.status_code}')

# ===== 5. Entrepôts =====
r = safe_post(c, '/fr/dashboard/entrepots/', {
    'name': 'Entrepôt Test', 'code': 'ST-01', 'address': 'Rue 1', 'city': 'Douala',
})
check('Création entrepôt (302)', r.status_code == 302, f'-> {r.status_code}')
wh = Warehouse.objects.filter(store=store, code='ST-01').first()
check('Entrepôt en base', wh is not None)

# Réception de marchandises
r = safe_post(c, f'/fr/dashboard/entrepots/{wh.id}/reception/', {
    'product[]': [str(product.id)], 'quantity[]': ['8'], 'reason': 'Réception test',
})
check('Réception marchandises (302)', r.status_code == 302, f'-> {r.status_code}')
from inventory.models import ProductStock
ps = ProductStock.objects.filter(product=product, warehouse=wh).first()
check('Stock entrepôt synchronisé (8)', ps is not None and ps.quantity == 8,
      f'-> {ps.quantity if ps else "N/A"}')

# Sous-pages entrepôt
for url, name in [
    (f'/fr/dashboard/entrepots/{wh.id}/', 'Détail entrepôt'),
    (f'/fr/dashboard/entrepots/{wh.id}/employes/', 'Employés entrepôt'),
    (f'/fr/dashboard/entrepots/{wh.id}/commandes/', 'Commandes entrepôt'),
    (f'/fr/dashboard/entrepots/{wh.id}/boutiques/', 'Boutiques entrepôt'),
    (f'/fr/dashboard/entrepots/{wh.id}/stock/', 'Stock entrepôt'),
    (f'/fr/dashboard/entrepots/{wh.id}/comptabilite/', 'Comptabilité entrepôt'),
    (f'/fr/dashboard/entrepots/{wh.id}/depenses/', 'Dépenses entrepôt'),
]:
    r = safe_get(c, url)
    check(f'{name} (200)', r.status_code == 200, f'-> {r.status_code}')

# Dépense entrepôt
from accounting.models import WarehouseExpense
r = safe_post(c, f'/fr/dashboard/entrepots/{wh.id}/comptabilite/', {
    'category': 'rent', 'description': 'Loyer test', 'amount': '25000',
})
check('Ajout dépense entrepôt (302)', r.status_code == 302, f'-> {r.status_code}')
check('Dépense enregistrée', WarehouseExpense.objects.filter(warehouse=wh, description='Loyer test').exists())

# ===== 6. Transferts inter-entrepôts =====
r = safe_post(c, '/fr/dashboard/entrepots/', {
    'name': 'Entrepôt B', 'code': 'ST-02', 'city': 'Yaoundé',
})
wh2 = Warehouse.objects.filter(store=store, code='ST-02').first()
check('Second entrepôt créé', wh2 is not None)
r = safe_post(c, '/fr/dashboard/transferts/', {
    'from_warehouse': str(wh.id), 'to_warehouse': str(wh2.id),
    'transfer_type': 'transfer', 'product[]': [str(product.id)], 'quantity[]': ['3'],
    'notes': 'Transfert test',
})
check('Création transfert (302)', r.status_code == 302, f'-> {r.status_code}')
transfer = StockTransfer.objects.filter(store=store, reference__startswith='TRF').first()
check('Transfert en brouillon', transfer is not None and transfer.status == 'draft',
      f'-> {transfer.status if transfer else "N/A"}')

r = safe_post(c, f'/fr/dashboard/transferts/{transfer.id}/confirm/')
transfer.refresh_from_db()
check('Transfert confirmé (en transit)', transfer.status == 'in_transit', f'-> {transfer.status}')
ps.refresh_from_db()
check('Stock source déduit (8→5)', ps.quantity == 5, f'-> {ps.quantity}')

r = safe_post(c, f'/fr/dashboard/transferts/{transfer.id}/receive/')
transfer.refresh_from_db()
check('Transfert reçu', transfer.status == 'received', f'-> {transfer.status}')
ps2 = ProductStock.objects.filter(product=product, warehouse=wh2).first()
check('Stock destination crédité (3)', ps2 is not None and ps2.quantity == 3,
      f'-> {ps2.quantity if ps2 else "N/A"}')

r = safe_get(c, '/fr/dashboard/transferts/export/pdf/')
check('Export PDF transferts', r.status_code == 200 and b'%PDF' in r.content[:5], f'-> {r.status_code}')
r = safe_get(c, '/fr/dashboard/transferts/export/excel/')
check('Export Excel transferts', r.status_code == 200 and b'PK' in r.content[:2], f'-> {r.status_code}')

# BUG connu (documenté, hors périmètre) : StockTransfer.cancel() sur un transfert
# "in_transit" restaure le stock dans l'entrepôt source sans re-déduire la destination.

# ===== 7. Employés / rôles / paie =====
r = safe_post(c, '/fr/dashboard/roles/', {
    'name': 'Gestionnaire Stock', 'permissions': ['products.view', 'stock.view', 'stock.adjust'],
})
check('Création rôle (302)', r.status_code == 302, f'-> {r.status_code}')
role = StoreRole.objects.filter(store=store, name='Gestionnaire Stock').first()
check('Rôle en base avec permissions', role is not None and role.has_permission('stock.adjust'))

r = safe_post(c, '/fr/dashboard/employes/liste/', {
    'username': f'emp{SUFFIX}', 'password': 'employe123', 'password_confirm': 'employe123',
    'first_name': 'Alice', 'last_name': 'Employee', 'role': str(role.id),
    'warehouse': str(wh.id), 'salary': '80000',
})
check('Ajout employé (302)', r.status_code == 302, f'-> {r.status_code}')
member = StoreMember.objects.filter(store=store, user__username=f'emp{SUFFIX}').first()
check('Employé créé avec rôle + entrepôt + salaire',
      member is not None and member.role == role and member.warehouse == wh and member.salary == 80000)

# L'employé a un compte utilisable
c_emp = Client()
check('Login employé OK', c_emp.login(username=f'emp{SUFFIX}', password='employe123'))

# Bulletins de paie
r = safe_post(c, '/fr/dashboard/paie/nouveau/', {
    'member': str(member.id), 'month': str(timezone.now().month), 'year': str(timezone.now().year),
    'base_salary': '80000',
    'line_type[]': ['bonus', 'deduction'], 'line_label[]': ['Transport', 'CNPS'],
    'line_amount[]': ['10000', '4000'],
})
check('Création bulletin (302)', r.status_code == 302, f'-> {r.status_code}')
payslip = Payslip.objects.filter(member=member).first()
check('Bulletin avec net correct (80000+10000-4000=86000)',
      payslip is not None and payslip.net_salary == 86000,
      f'-> {payslip.net_salary if payslip else "N/A"}')

r = safe_post(c, f'/fr/dashboard/paie/{payslip.id}/validate/')
payslip.refresh_from_db()
check('Bulletin validé', payslip.status == 'validated', f'-> {payslip.status}')
r = safe_post(c, f'/fr/dashboard/paie/{payslip.id}/pay/')
payslip.refresh_from_db()
check('Bulletin payé', payslip.status == 'paid', f'-> {payslip.status}')

# Doublon de bulletin refusé
r = safe_post(c, '/fr/dashboard/paie/nouveau/', {
    'member': str(member.id), 'month': str(timezone.now().month), 'year': str(timezone.now().year),
    'base_salary': '80000',
})
check('Doublon bulletin refusé (pas de 2e)', Payslip.objects.filter(member=member).count() == 1)

# Détail employé
r = safe_get(c, f'/fr/dashboard/employes/{member.id}/')
check('Détail employé (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_post(c, f'/fr/dashboard/employes/{member.id}/toggle/')
member.refresh_from_db()
check('Employé désactivé par toggle', not member.is_active)

# ===== 8. Ventes directes (comptoir) =====
product.refresh_from_db()
sale_stock_before = product.stock
r = safe_post(c, '/fr/dashboard/ventes/nouvelle/', {
    'product[]': [str(product.id)], 'quantity[]': ['2'],
    'customer_name': 'Client Comptoir', 'customer_phone': '690000000',
    'payment_method': 'cash',
})
check('Vente directe créée (302)', r.status_code == 302, f'-> {r.status_code}')
direct_order = Order.objects.filter(notes__contains='Vente directe', buyer=seller).first()
check('Commande vente directe en base (payée, livrée)',
      direct_order is not None and direct_order.is_paid and direct_order.status == 'delivered')
product.refresh_from_db()
check('Stock décrémenté par la vente (-2)', product.stock == sale_stock_before - 2,
      f'-> {product.stock} (attendu {sale_stock_before - 2})')

# ===== 9. Commandes dashboard + changement de statut =====
buyer = User.objects.create_user(username=f'ach{SUFFIX}', password='testpass123', role='buyer')
order = Order.objects.create(
    buyer=buyer, status='pending', total_amount=5000, subtotal=5000,
    shipping_name='Ach Stest', shipping_phone='677000000',
    shipping_address='Rue 2', shipping_city='Douala',
)
from orders.models import OrderItem
OrderItem.objects.create(order=order, product=product, store=store, quantity=1, price=4000)
# Le vendeur voit la commande de son stock
r = safe_get(c, '/fr/dashboard/commandes/')
check('Commande visible dans dashboard vendeur (200)', r.status_code == 200 and b'ALB-' in r.content)
r = safe_get(c, f'/fr/dashboard/commandes/{order.order_number}/')
check('Détail commande vendeur (200)', r.status_code == 200, f'-> {r.status_code}')

r = safe_post(c, f'/fr/dashboard/commandes/{order.order_number}/', {'status': 'confirmed'})
order.refresh_from_db()
check('Statut confirmé + is_paid', order.status == 'confirmed' and order.is_paid)
from messaging.models import Notification
check('Notification envoyée à l\'acheteur',
      Notification.objects.filter(user=buyer, notif_type='order').exists())

# Vendeur étranger ne peut pas voir cette commande
r = safe_get(c_scan, f'/fr/dashboard/commandes/{order.order_number}/')
check('Vendeur étranger → commande refusée (302)', r.status_code == 302, f'-> {r.status_code}')

# Vente encaissée : transaction + wallet (commission 10%)
check('Transaction vente créée (commission 10%)',
      seller.transactions.filter(type='sale', order=order).exists())
wallet = SellerWallet.objects.filter(user=seller).first()
check('Wallet vendeur crédité (net 90% = 3600)',
      wallet is not None and wallet.balance == 3600 and wallet.total_earned == 4000,
      f'-> balance={wallet.balance if wallet else "N/A"} earned={wallet.total_earned if wallet else "N/A"}')

# ===== 10. RFQ côté vendeur (créer + répondre) =====
r = safe_post(c, '/fr/dashboard/devis/', {
    'product_name': 'Palettes bois', 'description': '100 palettes',
    'quantity': '100', 'unit': 'pièce', 'target_price': '3000',
})
check('Vendeur crée une RFQ (302)', r.status_code == 302, f'-> {r.status_code}')
rfq = RFQ.objects.filter(buyer=seller, product_name='Palettes bois').first()
check('RFQ vendeur en base', rfq is not None)

rfq2 = RFQ.objects.create(
    buyer=buyer, product_name='Cartons', description='Cartons xyz', quantity=50,
)
r = safe_get(c, f'/fr/dashboard/devis/{rfq2.id}/')
check('Détail RFQ vendeur (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_post(c, f'/fr/dashboard/devis/{rfq2.id}/', {
    'price_per_unit': '500', 'delivery_time': '3 jours', 'payment_terms': '50/50',
    'description': 'Devis cartons',
})
check('Vendeur soumet devis (302)', r.status_code == 302, f'-> {r.status_code}')
quote = Quote.objects.filter(rfq=rfq2, seller=seller).first()
check('Devis en base (total 25000)', quote is not None and quote.total_price == 25000,
      f'-> {quote.total_price if quote else "N/A"}')

# L'acheteur accepte le devis
c_buyer = Client()
c_buyer.login(username=f'ach{SUFFIX}', password='testpass123')
r = safe_post(c_buyer, f'/fr/commandes/devis/{quote.id}/accepter/')
quote.refresh_from_db(); rfq2.refresh_from_db()
check('Devis accepté + RFQ acceptée', quote.status == 'accepted' and rfq2.status == 'accepted')

# ===== 11. Clients + export =====
r = safe_get(c, '/fr/dashboard/clients/export/')
check('Export Excel clients', r.status_code == 200 and b'PK' in r.content[:2], f'-> {r.status_code}')

# ===== 12. Paramètres dashboard (profil + notifications) =====
r = safe_post(c, '/fr/dashboard/parametres/', {
    'section': 'account', 'first_name': 'Vendeur', 'last_name': 'Test',
    'email': f'vend{SUFFIX}@test.com', 'phone': '677111222',
})
check('Sauvegarde compte (302)', r.status_code == 302, f'-> {r.status_code}')
seller.refresh_from_db()
check('Profil vendeur mis à jour', seller.first_name == 'Vendeur' and seller.phone == '677111222')

r = safe_post(c, '/fr/dashboard/parametres/', {
    'section': 'notifications', 'email_order': 'on', 'email_stock': 'on',
})
check('Préférences notifications (302)', r.status_code == 302, f'-> {r.status_code}')

# ===== 13. Acheteur bloqué du dashboard vendeur =====
r = safe_get(c_buyer, '/fr/dashboard/produits/')
check('Acheteur redirigé hors dashboard vendeur (302)', r.status_code == 302, f'-> {r.status_code}')

# ===== RÉSULTATS =====
sys.exit(1 if finish(results, 'VENDEUR') else 0)
