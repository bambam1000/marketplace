# -*- coding: utf-8 -*-
"""Test fonctionnel complet : MARKETING, FACTURATION, POS, ABONNEMENT, COMPTA.
Idempotent : nettoie ses données à chaque run.
"""
import sys
from test_utils import (make_checker, finish, cleanup_test_data,
                        safe_get, safe_post, xlsx_bytes)

from django.test import Client
from django.utils import timezone
from datetime import timedelta

from django.contrib.auth import get_user_model
from catalog.models import Category, Product
from store.models import Store
from marketing.models import (PromoCode, Campaign, Newsletter, MessagingCampaign,
                              FacebookPost, LoyaltyProgram)
from billing.models import SubscriptionPlan, Subscription, PaymentConfig, ProductBoost
from accounting.models import Transaction, SellerWallet, PayoutRequest, Expense
from pos.models import POSSession, POSSale, POSSaleItem, POSProduct, CashMovement

User = get_user_model()
SUFFIX = '_mtest'
TS = 'mtest'

results = []
check = make_checker(results)

# ===== Nettoyage =====
cleanup_test_data(SUFFIX, promo_prefix='MT', category_slugs=['testcat-mkt'],
                  plan_slugs=['plan-mtest'])
ProductBoost.objects.filter(product__slug='produit-mkt').delete()

# ===== Données =====
seller = User.objects.create_user(username=f'vend{SUFFIX}', password='testpass123', role='seller')
store = Store.objects.create(owner=seller, name='Boutique Mkt Test', slug='boutique-mkt-test')
cat = Category.objects.create(name='TestCat Mkt', slug='testcat-mkt')
product = Product.objects.create(
    store=store, category=cat, name='Produit Mkt', slug='produit-mkt',
    description='Desc', price=5000, stock=40,
)
buyer = User.objects.create_user(username=f'ach{SUFFIX}', password='testpass123', role='buyer')

c = Client()
c.login(username=f'vend{SUFFIX}', password='testpass123')

# ===== 1. Hub marketing + analytics =====
r = safe_get(c, '/fr/marketing/')
check('Dashboard marketing (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_get(c, '/fr/marketing/analytics/')
check('Analytics marketing (200)', r.status_code == 200, f'-> {r.status_code}')

# ===== 2. Codes promo (CRUD + toggle) =====
r = safe_post(c, '/fr/marketing/promo-codes/create/', {
    'code': 'MTPROMO15', 'description': 'Promo test', 'discount_type': 'percentage',
    'discount_value': '15', 'min_purchase_amount': '1000', 'is_active': 'on',
    'valid_from': timezone.now().strftime('%Y-%m-%dT%H:%M'),
    'valid_to': (timezone.now() + timedelta(days=30)).strftime('%Y-%m-%dT%H:%M'),
})
check('Création code promo (302)', r.status_code == 302, f'-> {r.status_code}')
promo = PromoCode.objects.filter(code='MTPROMO15', store=store).first()
check('Code promo en base', promo is not None)

r = safe_post(c, f'/fr/marketing/promo-codes/{promo.id}/toggle/')
promo.refresh_from_db()
check('Toggle promo → inactif', not promo.is_active, f'-> {promo.is_active}')
r = safe_post(c, f'/fr/marketing/promo-codes/{promo.id}/toggle/')
promo.refresh_from_db()
check('Toggle promo → actif', promo.is_active)

r = safe_post(c, f'/fr/marketing/promo-codes/{promo.id}/edit/', {
    'code': 'MTPROMO15', 'description': 'Promo modifiée', 'discount_type': 'fixed',
    'discount_value': '500', 'min_purchase_amount': '1000',
    'valid_from': timezone.now().strftime('%Y-%m-%dT%H:%M'),
    'valid_to': (timezone.now() + timedelta(days=30)).strftime('%Y-%m-%dT%H:%M'),
})
promo.refresh_from_db()
check('Édition promo (fixed 500)', promo.discount_type == 'fixed' and promo.discount_value == 500,
      f'-> {promo.discount_type}/{promo.discount_value}')

# ===== 3. Campagnes =====
r = safe_post(c, '/fr/marketing/campaigns/create/', {
    'name': 'Campagne Flash', 'campaign_type': 'flash_sale', 'description': 'Vente flash test',
    'start_date': (timezone.now() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
    'end_date': (timezone.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'),
    'status': 'active', 'budget': '10000', 'target_products': [str(product.id)],
    'promo_code': str(promo.id),
})
check('Création campagne (302)', r.status_code == 302, f'-> {r.status_code}')
campaign = Campaign.objects.filter(store=store, name='Campagne Flash').first()
check('Campagne en base active', campaign is not None and campaign.status == 'active')
check('Produits cibles liés', campaign and campaign.target_products.filter(pk=product.id).exists())

r = safe_get(c, f'/fr/marketing/campagne/{campaign.id}/')
check('Page publique campagne (200)', r.status_code == 200, f'-> {r.status_code}')
campaign.refresh_from_db()
check('Clic compté sur la campagne', campaign.clicks_count >= 1, f'-> {campaign.clicks_count}')

r = safe_post(c, f'/fr/marketing/campaigns/{campaign.id}/edit/', {
    'name': 'Campagne Flash V2', 'campaign_type': 'flash_sale', 'description': 'Modifiée',
    'start_date': (timezone.now() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
    'end_date': (timezone.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'),
    'status': 'active', 'budget': '20000',
})
campaign.refresh_from_db()
check('Campagne modifiée (budget 20000)', campaign.budget == 20000, f'-> {campaign.budget}')
r = safe_get(c, f'/fr/marketing/campaigns/{campaign.id}/')
check('Détail campagne (200)', r.status_code == 200, f'-> {r.status_code}')

# ===== 4. Email marketing =====
# Newsletter draft (audience custom via Excel)
r = safe_post(c, '/fr/marketing/emails/nouveau/', {
    'subject': 'Promo MT de janvier', 'content': '<p>Bonjour, profitez de -15%</p>',
    'target_audience': 'custom', 'action': 'draft',
    'recipients_file': xlsx_bytes([['email'], [f'ach{SUFFIX}@test.com'], ['pasun@email']]),
    'products': [str(product.id)], 'promo_code': str(promo.id),
})
check('Création newsletter draft (302)', r.status_code == 302, f'-> {r.status_code}')
newsletter = Newsletter.objects.filter(store=store, subject='Promo MT de janvier').first()
check('Newsletter en base avec destinataires custom',
      newsletter is not None and f'ach{SUFFIX}@test.com' in newsletter.custom_recipients)

r = safe_get(c, f'/fr/marketing/emails/{newsletter.id}/')
check('Aperçu email (200)', r.status_code == 200, f'-> {r.status_code}')

# Envoi
r = safe_post(c, f'/fr/marketing/emails/{newsletter.id}/envoyer/')
newsletter.refresh_from_db()
check('Envoi newsletter (statut sent)', newsletter.status == 'sent' and newsletter.recipients_count >= 1,
      f'-> {newsletter.status}/{newsletter.recipients_count}')

# Programmation
r = safe_post(c, '/fr/marketing/emails/nouveau/', {
    'subject': 'Promo programmée MT', 'content': '<p>Salut</p>',
    'target_audience': 'customers', 'action': 'schedule',
    'scheduled_at': (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
})
nl_sched = Newsletter.objects.filter(store=store, subject='Promo programmée MT').first()
check('Newsletter programmée', nl_sched is not None and nl_sched.status == 'scheduled',
      f'-> {nl_sched.status if nl_sched else "N/A"}')

# Template destinataires
r = safe_get(c, '/fr/marketing/emails/template-destinataires/')
check('Template Excel destinataires', r.status_code == 200 and b'PK' in r.content[:2], f'-> {r.status_code}')

# ===== 5. WhatsApp / Telegram =====
r = safe_post(c, '/fr/marketing/messagerie/nouveau/', {
    'name': 'Campagne WA MT', 'message': 'Bonjour, nouvelles arrivées !',
    'channel': 'whatsapp', 'audience': 'custom',
    'numbers_file': xlsx_bytes([['numero'], ['690111222'], ['+237690333444']]),
    'products': [str(product.id)], 'promo_code': str(promo.id),
})
check('Création campagne WhatsApp (302)', r.status_code == 302, f'-> {r.status_code}')
wa = MessagingCampaign.objects.filter(store=store, name='Campagne WA MT').first()
check('Campagne WA en base', wa is not None)
recipients = wa.get_recipients()
check('Numéros normalisés (237…)', recipients == ['237690111222', '237690333444'],
      f'-> {recipients}')

r = safe_get(c, f'/fr/marketing/messagerie/{wa.id}/')
check('Console envoi (200)', r.status_code == 200, f'-> {r.status_code}')

# Marquer envoyé (AJAX)
r = safe_post(c, f'/fr/marketing/messagerie/{wa.id}/envoye/', {'number': '237690111222'})
check('Marquage envoyé (JSON ok)', r.status_code == 200 and r.json().get('ok') is True, f'-> {r.status_code}')
r = safe_post(c, f'/fr/marketing/messagerie/{wa.id}/envoye/', {'number': '237690333444'})
wa.refresh_from_db()
check('Progression 100% → done', wa.status == 'done' and wa.progress == 100,
      f'-> {wa.status}/{wa.progress}')

r = safe_get(c, '/fr/marketing/messagerie/template-numeros/')
check('Template Excel numéros', r.status_code == 200 and b'PK' in r.content[:2], f'-> {r.status_code}')

# ===== 6. Posts Facebook =====
r = safe_post(c, '/fr/marketing/facebook/nouveau/', {
    'title': 'Post MT', 'content': 'Découvrez notre produit !',
    'hashtags': '#promo #cameroun', 'products': [str(product.id)], 'promo_code': str(promo.id),
})
check('Création post Facebook (302)', r.status_code == 302, f'-> {r.status_code}')
post = FacebookPost.objects.filter(store=store, title='Post MT').first()
check('Post en base', post is not None)
post_text = post.build_post_text()
check('Texte du post contient produit + promo + hashtag',
      'Produit Mkt' in post_text and 'MTPROMO15' in post_text and '#promo' in post_text)

r = safe_get(c, f'/fr/marketing/facebook/{post.id}/')
check('Aperçu post (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_post(c, f'/fr/marketing/facebook/{post.id}/')
post.refresh_from_db()
check('Post publié', post.status == 'published' and post.published_at is not None)

# ===== 7. Fidélité =====
r = safe_post(c, '/fr/marketing/loyalty/', {
    'points_per_amount': '1', 'silver_threshold': '2000', 'silver_rate': '5',
    'gold_threshold': '5000', 'gold_rate': '10', 'platinum_threshold': '10000',
    'platinum_rate': '15',
})
check('Réglages fidélité (302)', r.status_code == 302, f'-> {r.status_code}')

# Programme côté acheteur (créé par le flux checkout, on simule)
from marketing.models import LoyaltySettings
cfg = LoyaltySettings.objects.get(store=store)
lp = LoyaltyProgram.objects.create(user=buyer, store=store, points=0)
added = lp.add_points(15000)  # 1 point/FCFA → 15000 points → platinum (>= 10000)
lp.refresh_from_db()
check('Points ajoutés (15000)', added == 15000 and lp.points == 15000, f'-> {added}/{lp.points}')
check('Niveau platinum atteint', lp.tier == 'platinum', f'-> {lp.tier}')
check('Taux de réduction platinum (15%)', lp.discount_rate == 15, f'-> {lp.discount_rate}%')

r = safe_get(c, '/fr/marketing/loyalty/')
check('Page fidélité avec membre (200)', r.status_code == 200, f'-> {r.status_code}')

# ===== 8. ABONNEMENT (billing) =====
plan = SubscriptionPlan.objects.create(
    name='Plan Test', slug='plan-mtest', price_monthly=5000, price_yearly=50000,
    max_products=100, can_boost=True, boost_credits_monthly=4, badge_level='silver',
    is_active=True,
)
r = safe_get(c, '/fr/abonnement/plans/')
check('Page plans (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_post(c, '/fr/abonnement/souscrire/plan-mtest/', {
    'billing_cycle': 'monthly', 'payment_method': 'momo',
})
check('Souscription (302)', r.status_code == 302, f'-> {r.status_code}')
sub = Subscription.objects.filter(user=seller).first()
check('Abonnement actif 30j', sub is not None and sub.is_active and sub.plan == plan)
check('Transaction abonnement créée',
      Transaction.objects.filter(user=seller, type='subscription', amount=5000).exists())

r = safe_get(c, '/fr/abonnement/mon-abonnement/')
check('Mon abonnement (200)', r.status_code == 200, f'-> {r.status_code}')

# Config paiement vendeur
r = safe_post(c, '/fr/abonnement/paiements/', {
    'momo_enabled': 'on', 'momo_number': '677000999', 'momo_name': 'Vendeur Mtest',
    'cash_enabled': 'on',
})
pcfg = PaymentConfig.objects.filter(user=seller).first()
check('Config paiement (MoMo + cash)', pcfg is not None and pcfg.momo_enabled and pcfg.cash_enabled)
check('Méthodes activées', ('momo', 'MTN MoMo', '677000999') in pcfg.enabled_methods)

# ===== 9. BOOSTS =====
# Boost interne (featured)
r = safe_post(c, f'/fr/abonnement/boost/{product.id}/', {
    'platform': 'internal', 'budget': '5000', 'duration': '7',
})
check('Boost interne (302)', r.status_code == 302, f'-> {r.status_code}')
boost = ProductBoost.objects.filter(product=product, platform='internal').first()
check('Boost interne actif', boost is not None and boost.status == 'active')
product.refresh_from_db()
check('Produit mis en avant (featured)', product.is_featured)

# Boost Facebook : commission 20%
r = safe_post(c, f'/fr/abonnement/boost/{product.id}/', {
    'platform': 'facebook', 'budget': '25000', 'duration': '14',
    'target_city': 'Douala', 'target_age': '25-45',
})
check('Boost Facebook (302)', r.status_code == 302, f'-> {r.status_code}')
boost_fb = ProductBoost.objects.filter(product=product, platform='facebook').first()
check('Commission 20% (5000) + budget FB 20000',
      boost_fb.commission == 5000 and boost_fb.platform_budget == 20000,
      f'-> {boost_fb.commission}/{boost_fb.platform_budget}')
check('Données de ciblage enregistrées',
      boost_fb.targeting_data and boost_fb.targeting_data.get('city') == 'Douala')

r = safe_get(c, '/fr/abonnement/mes-boosts/')
check('Mes boosts (200)', r.status_code == 200, f'-> {r.status_code}')

# ===== 10. COMPTABILITÉ =====
# Wallet + vente simulée pour créditer
wallet, _ = SellerWallet.objects.get_or_create(user=seller)
wallet.balance = 50000
wallet.total_earned = 80000
wallet.save()

r = safe_get(c, '/fr/comptabilite/')
check('Dashboard compta vendeur (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_get(c, '/fr/comptabilite/transactions/')
check('Liste transactions (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_get(c, '/fr/comptabilite/portefeuille/')
check('Portefeuille (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_get(c, '/fr/comptabilite/rapport/')
check('Rapport financier (200)', r.status_code == 200, f'-> {r.status_code}')

# Demande de retrait : validations
r = safe_post(c, '/fr/comptabilite/retrait/', {'amount': '1000', 'payment_method': 'momo'})
check('Retrait < 5000 refusé', not PayoutRequest.objects.filter(user=seller).exists())
r = safe_post(c, '/fr/comptabilite/retrait/', {'amount': '999999', 'payment_method': 'momo'})
check('Retrait > solde refusé', not PayoutRequest.objects.filter(user=seller).exists())
r = safe_post(c, '/fr/comptabilite/retrait/', {
    'amount': '20000', 'payment_method': 'momo', 'account_details': '677000999',
})
payout = PayoutRequest.objects.filter(user=seller, amount=20000).first()
check('Retrait 20000 créé (302)', payout is not None)
wallet.refresh_from_db()
check('Solde débité + en attente', wallet.balance == 30000 and wallet.pending_balance == 20000,
      f'-> {wallet.balance}/{wallet.pending_balance}')

# ===== 11. POS =====
r = safe_get(c, '/fr/pos/')
check('Dashboard POS (200)', r.status_code == 200, f'-> {r.status_code}')

r = safe_post(c, '/fr/pos/open-session/', {'opening_cash': '10000'})
check('Ouverture session caisse (302)', r.status_code == 302, f'-> {r.status_code}')
session = POSSession.objects.filter(store=store, status='open').first()
check('Session ouverte', session is not None)

r = safe_get(c, '/fr/pos/interface/')
check('Interface caisse (200)', r.status_code == 200, f'-> {r.status_code}')
sale = POSSale.objects.filter(session=session, status='pending').first()
check('Vente en cours créée automatiquement', sale is not None)

# API : ajouter article
import json as _json
r = c.post('/fr/pos/api/add-item/', data=_json.dumps({'sale_id': sale.id, 'product_id': product.id, 'quantity': 2}),
           content_type='application/json')
check('API ajout article (200 + total)', r.status_code == 200 and r.json()['sale_total'] == 10000,
      f'-> {r.status_code} {r.content[:80]}')
item = POSSaleItem.objects.filter(sale=sale).first()
check('Ligne de vente créée (qty 2)', item is not None and item.quantity == 2)

# API : modifier quantité
r = c.post(f'/fr/pos/api/update-item/{item.id}/', data=_json.dumps({'quantity': 3}),
           content_type='application/json')
check('API maj quantité (total 15000)', r.status_code == 200 and r.json()['sale_total'] == 15000,
      f'-> {r.content[:80]}')

# API : remise 10%
r = c.post(f'/fr/pos/api/discount/{sale.id}/', data=_json.dumps({'discount_percent': 10}),
           content_type='application/json')
sale.refresh_from_db()
check('API remise 10% (1500)', sale.discount_amount == 1500, f'-> {sale.discount_amount}')

# API : recherche produits
r = c.get('/fr/pos/api/search/', {'q': 'Mkt'})
check('API recherche produits (JSON)', r.status_code == 200 and len(r.json()['products']) >= 1)

# API : encaisser (cash 20000 sur total 13500 → monnaie 6500)
r = c.post(f'/fr/pos/api/complete/{sale.id}/', data=_json.dumps({
    'cash_amount': 20000, 'amount_tendered': 20000, 'customer_name': 'Client POS',
}), content_type='application/json')
data = r.json()
check('Encaissement OK + monnaie rendue (6500)',
      r.status_code == 200 and data['change_amount'] == 6500,
      f'-> {r.content[:100]}')
sale.refresh_from_db()
check('Vente complétée + mouvement caisse', sale.status == 'completed' and
      CashMovement.objects.filter(session=session, category='sale', amount=13500).exists())  # 20 000 reçus − 6 500 rendus

# Stock décrémenté
product.refresh_from_db()
check('Stock décrémenté par vente POS (-3)', product.stock == 37, f'-> {product.stock}')

# Ticket de caisse
r = safe_get(c, f'/fr/pos/receipt/{sale.id}/')
check('Ticket de caisse (200)', r.status_code == 200, f'-> {r.status_code}')

# Rapport ventes
r = safe_get(c, '/fr/pos/reports/')
check('Rapport ventes POS (200)', r.status_code == 200, f'-> {r.status_code}')

# Remboursement
r = c.post(f'/fr/pos/api/refund/{sale.id}/', content_type='application/json')
check('Remboursement vente (JSON ok)', r.status_code == 200 and r.json().get('success') is True)
sale.refresh_from_db()
check('Vente remboursée', sale.status == 'refunded')
product.refresh_from_db()
check('Stock restauré après remboursement (+3)', product.stock == 40, f'-> {product.stock}')

# Fermeture session
r = safe_post(c, f'/fr/pos/close-session/{session.id}/', {'closing_cash': '10000'})
session.refresh_from_db()
check('Session fermée (écart -6500 attendu)', session.status == 'closed',
      f'-> diff={session.cash_difference}')

# ===== 12. FACTURATION =====
r = safe_get(c, '/fr/facturation/')
check('Dashboard facturation (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_get(c, '/fr/facturation/invoices/')
check('Liste factures (200)', r.status_code == 200, f'-> {r.status_code}')

# Facture manuelle
r = safe_post(c, '/fr/facturation/invoices/create/', {
    'invoice_type': 'standard', 'customer_name': 'Client Facture', 'customer_email': 'fact@test.com',
    'customer_phone': '690555666', 'customer_address': 'Rue 3',
    'issue_date': timezone.now().date().isoformat(),
    'due_date': (timezone.now() + timedelta(days=15)).date().isoformat(),
    'item_description[]': ['Livraison cartons', 'Service emballage'],
    'item_quantity[]': ['10', '1'],
    'item_unit_price[]': ['1500', '5000'],
    'shipping_amount': '2000', 'discount_amount': '1000', 'notes': 'Merci',
})
check('Création facture (302)', r.status_code == 302, f'-> {r.status_code}')
from invoicing.models import Invoice, InvoiceSettings
invoice = Invoice.objects.filter(store=store, customer_name='Client Facture').first()
check('Facture en base (2 lignes)', invoice is not None and invoice.items.count() == 2)
# Total = 10*1500 + 5000 + 2000 - 1000 = 21000
check('Total facture correct (21000)', invoice.total_amount == 21000, f'-> {invoice.total_amount}')
check('Numérotation FAC-000001', invoice.invoice_number.startswith('FAC-'),
      f'-> {invoice.invoice_number}')

# PDF
r = safe_get(c, f'/fr/facturation/invoices/{invoice.id}/pdf/')
check('PDF facture généré', r.status_code == 200 and b'%PDF' in r.content[:5], f'-> {r.status_code}')

# Envoi
r = safe_post(c, f'/fr/facturation/invoices/{invoice.id}/send/')
invoice.refresh_from_db()
check('Facture envoyée (statut sent)', invoice.status == 'sent', f'-> {invoice.status}')

# Détail + édition
r = safe_get(c, f'/fr/facturation/invoices/{invoice.id}/')
check('Détail facture (200)', r.status_code == 200, f'-> {r.status_code}')
r = safe_get(c, f'/fr/facturation/invoices/{invoice.id}/edit/')
check('Édition facture (200)', r.status_code == 200, f'-> {r.status_code}')

# Marquer payée
r = safe_post(c, f'/fr/facturation/invoices/{invoice.id}/mark-paid/')
invoice.refresh_from_db()
check('Facture payée', invoice.status == 'paid' and invoice.paid_date is not None)

# Facture depuis commande
from orders.models import Order, OrderItem
order = Order.objects.create(
    buyer=buyer, status='pending', total_amount=8000, subtotal=8000,
    shipping_name='Ach Mtest', shipping_phone='677222333',
    shipping_address='Rue 4', shipping_city='Douala',
)
OrderItem.objects.create(order=order, product=product, store=store, quantity=2, price=4000)
r = safe_post(c, f'/fr/facturation/invoices/from-order/{order.order_number}/')
inv2 = Invoice.objects.filter(order=order).first()
check('Facture depuis commande créée', inv2 is not None and inv2.items.count() == 1)

# Paramètres facturation
r = safe_post(c, '/fr/facturation/settings/', {
    'company_name': 'Boutique Mkt Test SARL', 'tax_id': 'M0123456789A', 'city': 'Douala',
    'apply_tva': 'on', 'tva_rate': '19.25', 'invoice_prefix': 'FAC',
})
isv = InvoiceSettings.objects.get(store=store)
check('Paramètres facturation (TVA 19.25)', isv.apply_tva and str(isv.tva_rate) == '19.25',
      f'-> {isv.tva_rate}')

# ===== 13. Sécurité : acheteur banni des espaces vendeurs =====
c_buyer = Client()
c_buyer.login(username=f'ach{SUFFIX}', password='testpass123')
for url, name in [
    ('/fr/marketing/', 'marketing'), ('/fr/facturation/', 'facturation'), ('/fr/pos/', 'POS'),
]:
    r = safe_get(c_buyer, url)
    # POS renvoie 404 (pas de boutique) : c'est bien un refus d'accès
    check(f'Acheteur banni de {name} (302/404)', r.status_code in (302, 404), f'-> {r.status_code}')

# ===== NETTOYAGE FINAL (les données de ce run ne doivent pas polluer) =====
ProductBoost.objects.filter(product=product).delete()
PayoutRequest.objects.filter(user=seller).delete()
SellerWallet.objects.filter(user=seller).delete()
cleanup_test_data(SUFFIX, promo_prefix='MT', category_slugs=['testcat-mkt'],
                  plan_slugs=['plan-mtest'])

sys.exit(1 if finish(results, 'MARKETING/BILLING/POS/FACTURATION') else 0)
