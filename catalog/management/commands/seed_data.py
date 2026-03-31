import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import User
from store.models import Store
from catalog.models import Category, Product, Review, FlashDeal
from orders.models import Order, OrderItem
from messaging.models import Conversation, Message

class Command(BaseCommand):
    help = 'Seed marketplace with demo data'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding AfriMarket...')

        # Admin
        admin, _ = User.objects.get_or_create(username='admin', defaults={
            'email': 'admin@afrimarketplace.cm', 'role': 'admin',
            'is_staff': True, 'is_superuser': True,
            'first_name': 'Admin', 'last_name': 'AfriMarket',
        })
        admin.set_password('admin123')
        admin.save()

        # Sellers
        sellers_data = [
            ('techcm', 'Tech', 'Cameroun', 'tech@cm.com', 'Douala', True, 2020, 'Leader de la tech au Cameroun. Produits Apple, Samsung, HP certifiés.'),
            ('modafrica', 'Mod', 'Africa', 'moda@cm.com', 'Yaoundé', True, 2019, 'Mode africaine contemporaine. Ankara, bazin, prêt-à-porter.'),
            ('maisonplus', 'Maison', 'Plus', 'maison@cm.com', 'Douala', True, 2021, 'Meubles, électroménager et décoration pour la maison africaine moderne.'),
            ('beautyqueen', 'Beauty', 'Queen', 'beauty@cm.com', 'Douala', True, 2022, 'Cosmétiques bio et produits de beauté naturels made in Africa.'),
            ('agroking', 'Agro', 'King', 'agro@cm.com', 'Bafoussam', True, 2018, 'Produits agricoles du terroir camerounais. Café, cacao, épices, miel.'),
            ('sportzone', 'Sport', 'Zone', 'sport@cm.com', 'Yaoundé', False, 2023, 'Équipements sportifs et vêtements de sport pour tous les niveaux.'),
        ]
        stores = []
        for uname, fname, lname, email, city, verified, year, desc in sellers_data:
            user, _ = User.objects.get_or_create(username=uname, defaults={
                'email': email, 'role': 'seller', 'first_name': fname,
                'last_name': lname, 'city': city, 'country': 'Cameroun',
            })
            user.set_password('seller123')
            user.save()
            store, _ = Store.objects.get_or_create(owner=user, defaults={
                'name': f'{fname}{lname}', 'description': desc,
                'city': city, 'is_verified': verified, 'year_established': year,
                'response_rate': random.randint(85, 99),
                'response_time': random.choice(['< 1h', '< 6h', '< 24h']),
            })
            stores.append(store)

        # Buyers
        buyers = []
        buyers_data = [
            ('jean_k', 'Jean-Pierre', 'Kamga', 'jp@cm.com', '+237677123456', 'Douala'),
            ('marie_c', 'Marie-Claire', 'Ngo Bassa', 'mc@cm.com', '+237699234567', 'Yaoundé'),
            ('paul_t', 'Paul', 'Tchigang', 'pt@cm.com', '+237655345678', 'Douala'),
            ('fatima_a', 'Fatima', 'Amadou', 'fa@cm.com', '+237670456789', 'Garoua'),
            ('serge_n', 'Serge', 'Nkoulou', 'sn@cm.com', '+237691567890', 'Yaoundé'),
            ('christelle_f', 'Christelle', 'Fouda', 'cf@cm.com', '+237676678901', 'Douala'),
            ('alain_t', 'Alain', 'Tchinda', 'at@cm.com', '+237650789012', 'Bafoussam'),
            ('brigitte_e', 'Brigitte', 'Eyenga', 'be@cm.com', '+237698890123', 'Yaoundé'),
        ]
        for uname, fn, ln, email, phone, city in buyers_data:
            u, _ = User.objects.get_or_create(username=uname, defaults={
                'email': email, 'role': 'buyer', 'first_name': fn, 'last_name': ln,
                'phone': phone, 'city': city,
            })
            u.set_password('buyer123')
            u.save()
            buyers.append(u)

        # Categories (hierarchical)
        cats_data = [
            ('Électronique', 'electronique', '📱', 0, [
                ('Smartphones', 'smartphones', '📱'),
                ('Ordinateurs', 'ordinateurs', '💻'),
                ('TV & Audio', 'tv-audio', '📺'),
                ('Accessoires', 'accessoires-tech', '🎧'),
            ]),
            ('Mode & Vêtements', 'mode', '👗', 1, [
                ('Femme', 'mode-femme', '👗'),
                ('Homme', 'mode-homme', '👔'),
                ('Chaussures', 'chaussures', '👟'),
                ('Sacs & Accessoires', 'sacs-accessoires', '👜'),
            ]),
            ('Maison & Déco', 'maison', '🏠', 2, [
                ('Meubles', 'meubles', '🛋️'),
                ('Électroménager', 'electromenager', '🔌'),
                ('Cuisine', 'cuisine', '🍽️'),
            ]),
            ('Beauté & Santé', 'beaute', '💄', 3, [
                ('Soins Visage', 'soins-visage', '🧴'),
                ('Maquillage', 'maquillage', '💄'),
                ('Parfums', 'parfums', '🌸'),
            ]),
            ('Alimentation', 'alimentation', '🍽️', 4, [
                ('Café & Thé', 'cafe-the', '☕'),
                ('Épices', 'epices', '🌶️'),
                ('Miel & Confitures', 'miel', '🍯'),
            ]),
            ('Sport & Loisirs', 'sport', '⚽', 5, [
                ('Fitness', 'fitness', '💪'),
                ('Football', 'football', '⚽'),
                ('Vêtements Sport', 'vetements-sport', '🏃'),
            ]),
            ('Auto & Moto', 'auto-moto', '🚗', 6, []),
            ('Bébé & Enfant', 'bebe-enfant', '🧸', 7, []),
            ('Informatique', 'informatique', '🖥️', 8, []),
            ('Bricolage', 'bricolage', '🔧', 9, []),
        ]
        all_cats = {}
        for name, slug, icon, order, children in cats_data:
            parent, _ = Category.objects.get_or_create(slug=slug, defaults={
                'name': name, 'icon': icon, 'order': order,
            })
            all_cats[slug] = parent
            for cname, cslug, cicon in children:
                child, _ = Category.objects.get_or_create(slug=cslug, defaults={
                    'name': cname, 'icon': cicon, 'parent': parent,
                })
                all_cats[cslug] = child

        # Products (50+)
        products_data = [
            # Tech
            ('iPhone 15 Pro Max 256Go', 'smartphones', 0, 850000, 950000, 12, True, True, 1, 'Puce A17 Pro, caméra 48MP, écran Super Retina XDR 6.7".\nMatériau: Titane\nPoids: 221g', 'Noir, Blanc, Bleu, Naturel', '', 'Chine'),
            ('Samsung Galaxy S24 Ultra', 'smartphones', 0, 780000, 850000, 8, True, True, 1, 'S Pen intégré, caméra 200MP, écran AMOLED 6.8".\nProcesseur: Snapdragon 8 Gen 3', 'Noir, Violet, Jaune', '', 'Corée du Sud'),
            ('MacBook Air M3 13"', 'ordinateurs', 0, 1200000, None, 5, True, False, 1, 'Puce Apple M3, 8Go RAM, 256Go SSD, 18h autonomie.\nÉcran: Liquid Retina 13.6"', 'Argent, Gris Sidéral, Lumière', '', 'Chine'),
            ('AirPods Pro 2', 'accessoires-tech', 0, 185000, 220000, 25, False, True, 1, 'Réduction bruit active, Audio spatial, USB-C.\nAutonomie: 6h (30h avec boîtier)', '', '', 'Chine'),
            ('HP Pavilion 15 i5', 'ordinateurs', 0, 450000, None, 15, False, False, 1, 'Intel Core i5-1335U, 8Go RAM, 512Go SSD, Windows 11.\nÉcran: 15.6" Full HD IPS', 'Argent', '', 'Chine'),
            ('Samsung Smart TV 55" 4K', 'tv-audio', 0, 380000, 420000, 7, True, False, 1, '4K UHD Crystal, Tizen OS, HDR10+.\nConnectivité: WiFi, Bluetooth, 3x HDMI', '', '', 'Corée du Sud'),
            ('Tecno Spark 20', 'smartphones', 0, 85000, 95000, 50, False, True, 1, 'Écran 6.56", 128Go, caméra 50MP, batterie 5000mAh.\nProcesseur: Helio G85', 'Noir, Bleu, Vert', '', 'Chine'),
            ('JBL Charge 5', 'accessoires-tech', 0, 75000, 90000, 20, False, True, 1, 'Enceinte Bluetooth portable, étanche IP67, 20h autonomie.\nPoids: 960g', 'Noir, Rouge, Bleu, Vert', '', 'Chine'),

            # Mode
            ('Robe Ankara Premium Wax', 'mode-femme', 1, 35000, 45000, 30, True, False, 5, 'Tissu Ankara 100% coton, coupe moderne.\nLongueur: Mi-mollet\nFermeture: Zip dos', 'Multicolore, Bleu/Or, Rouge/Noir', 'S, M, L, XL, XXL', 'Cameroun'),
            ('Costume Homme 3 Pièces Slim', 'mode-homme', 1, 95000, 120000, 12, True, False, 1, 'Coupe slim moderne, tissu italien.\nComposition: 70% Polyester, 30% Viscose', 'Noir, Bleu Marine, Gris', 'S, M, L, XL, XXL', 'Cameroun'),
            ('Nike Air Max 90', 'chaussures', 1, 65000, None, 20, False, False, 1, 'Les iconiques Air Max avec coussin Air visible.\nSemelle: Caoutchouc', 'Blanc, Noir, Rouge', '39, 40, 41, 42, 43, 44, 45', 'Vietnam'),
            ('Sac à Main Cuir Artisanal', 'sacs-accessoires', 1, 55000, 70000, 18, False, False, 1, 'Cuir véritable fait main au Cameroun.\nDimensions: 30x25x12cm', 'Marron, Noir, Cognac', '', 'Cameroun'),
            ('Montre Casio Vintage A168', 'sacs-accessoires', 1, 28000, None, 40, False, False, 1, 'Classique intemporelle, dorée, résistante à l\'eau.\nMécanisme: Quartz', 'Doré, Argent', '', 'Japon'),
            ('Bazin Riche Brodé 10 Yards', 'mode-femme', 1, 75000, 95000, 15, False, False, 3, 'Bazin riche allemand de haute qualité, broderie main.\nLongueur: 10 yards', 'Blanc, Bleu, Rose, Vert', '', 'Mali'),
            ('Polo Ralph Lauren Homme', 'mode-homme', 1, 42000, None, 35, False, False, 1, 'Polo classique en coton piqué.\nCoupe: Regular Fit', 'Blanc, Noir, Bleu, Rouge, Vert', 'S, M, L, XL', 'Bangladesh'),

            # Maison
            ('Canapé 3+2 Places Moderne', 'meubles', 2, 450000, 520000, 3, True, False, 1, 'Simili cuir premium, design moderne.\nDimensions: 220x90x85cm', 'Noir, Marron, Gris', '', 'Chine'),
            ('Ventilateur Plafonnier LED', 'electromenager', 2, 85000, None, 22, False, False, 1, 'Éclairage LED intégré, télécommande, 3 vitesses.\nDiamètre: 120cm', 'Blanc, Noir', '', 'Chine'),
            ('Lit King Size + Matelas', 'meubles', 2, 280000, 320000, 5, False, False, 1, 'Bois massif 180x200cm, matelas orthopédique.\nÉpaisseur matelas: 25cm', '', '', 'Cameroun'),
            ('Set Cuisine 12 Pièces', 'cuisine', 2, 45000, 55000, 35, False, False, 1, 'Casseroles et poêles antiadhésives professionnel.\nMatériau: Aluminium + Teflon', '', '', 'Chine'),
            ('Réfrigérateur Samsung 350L', 'electromenager', 2, 320000, 360000, 4, True, False, 1, 'Double porte, No Frost, classe A+.\nDimensions: 171x60x66cm', 'Inox', '', 'Corée du Sud'),
            ('Machine à Laver 7kg', 'electromenager', 2, 195000, 230000, 8, False, False, 1, 'Automatique, 15 programmes, 1200 tours.\nClasse énergétique: A++', 'Blanc', '', 'Chine'),

            # Beauté
            ('Coffret Soins Visage Bio', 'soins-visage', 3, 42000, 55000, 28, True, False, 1, 'Crème, sérum, nettoyant, masque au beurre de karité.\nBio certifié', '', '', 'Cameroun'),
            ('Parfum Tom Ford Oud Wood', 'parfums', 3, 125000, None, 10, False, False, 1, 'Eau de parfum 100ml, notes bois de oud, santal.\nConcentration: EDP', '', '', 'France'),
            ('Kit Maquillage Pro 24 Pcs', 'maquillage', 3, 38000, 48000, 20, False, True, 1, 'Fond de teint, poudre, rouges à lèvres, pinceaux.\nTeintes adaptées peaux africaines', '', '', 'Chine'),
            ('Huile de Coco Vierge 500ml', 'soins-visage', 3, 5500, 7500, 60, False, False, 10, 'Pressée à froid, bio, multi-usage (cheveux, peau, cuisine).\nOrigine: Cameroun', '', '', 'Cameroun'),
            ('Shea Butter Karité Pur 1kg', 'soins-visage', 3, 8000, 12000, 45, False, False, 5, 'Beurre de karité pur non raffiné du Nord Cameroun.\nUsage: Corps, cheveux, bébé', '', '', 'Cameroun'),

            # Alimentation
            ('Café Arabica Premium 1kg', 'cafe-the', 4, 8500, None, 60, False, False, 5, 'Café arabica des hauts plateaux de l\'Ouest.\nTorréfaction: Moyenne\nNotes: Chocolat, noisette', '', '', 'Cameroun'),
            ('Miel Naturel Adamaoua 500ml', 'miel', 4, 5500, 7000, 45, False, False, 3, 'Miel pur récolté traditionnellement.\nOrigine: Adamaoua, Cameroun', '', '', 'Cameroun'),
            ('Pack Épices Cameroun 8 Variétés', 'epices', 4, 12000, 15000, 35, True, False, 1, 'Pèbè, njansang, 4 côtés, poivre blanc, country onion...\nPoids total: 400g', '', '', 'Cameroun'),
            ('Cacao en Poudre Bio 500g', 'cafe-the', 4, 6500, None, 40, False, False, 5, 'Cacao pur du Sud Cameroun, non sucré.\nCertifié bio et commerce équitable', '', '', 'Cameroun'),
            ('Thé Vert Bio Foumban 250g', 'cafe-the', 4, 4500, 5500, 50, False, True, 3, 'Thé vert cultivé sur les collines de Foumban.\nRécolte artisanale', '', '', 'Cameroun'),

            # Sport
            ('Tapis de Yoga Premium', 'fitness', 5, 18000, None, 50, False, False, 1, 'TPE écologique 6mm, antidérapant.\nDimensions: 183x61cm', 'Noir, Violet, Bleu', '', 'Chine'),
            ('Haltères Réglables 20kg', 'fitness', 5, 65000, 75000, 15, False, False, 1, 'Set complet 2-20kg, revêtement caoutchouc.\nMatériau: Fonte + Néoprène', '', '', 'Chine'),
            ('Maillot Cameroun 2026', 'vetements-sport', 5, 25000, None, 100, True, False, 1, 'Maillot officiel Lions Indomptables, Dri-FIT.\nSaison: 2025-2026', 'Vert, Blanc', 'S, M, L, XL, XXL', 'Thaïlande'),
            ('Ballon Adidas Pro Match', 'football', 5, 35000, 42000, 25, False, False, 3, 'Ballon de match officiel, FIFA Quality Pro.\nCirconférence: 68-70cm', 'Blanc/Or', '', 'Pakistan'),
            ('Chaussures de Foot Nike Phantom', 'football', 5, 85000, 95000, 12, False, False, 1, 'Crampons moulés, Flyknit, semelle AG.\nUsage: Terrain synthétique', 'Noir/Rouge, Blanc/Bleu', '39, 40, 41, 42, 43, 44', 'Vietnam'),
        ]

        products = []
        for name, cat_slug, store_idx, price, old_price, stock, featured, flash, min_order, desc, colors, sizes, origin in products_data:
            p, _ = Product.objects.get_or_create(
                name=name,
                defaults={
                    'store': stores[store_idx],
                    'category': all_cats[cat_slug],
                    'description': desc.split('\n')[0],
                    'specifications': '\n'.join(desc.split('\n')[1:]) if '\n' in desc else '',
                    'price': price,
                    'old_price': old_price,
                    'stock': stock,
                    'is_featured': featured,
                    'is_flash_deal': flash,
                    'min_order': min_order,
                    'colors': colors,
                    'sizes': sizes,
                    'origin': origin or 'Cameroun',
                    'is_active': True,
                    'views_count': random.randint(50, 5000),
                    'orders_count': random.randint(5, 500),
                }
            )
            products.append(p)

        # Reviews
        review_names = ['Sophie M.', 'Emmanuel T.', 'Aïcha B.', 'Patrick N.', 'Carine F.', 'David K.', 'Sandrine O.', 'Hervé M.']
        comments = [
            'Excellent produit, je recommande ! Livraison rapide.',
            'Très bon rapport qualité-prix. Conforme à la description.',
            'Qualité au rendez-vous. Le vendeur est très réactif.',
            'Bon produit, emballage soigné. Paiement MoMo facile.',
            'Super content ! Exactement ce que je cherchais.',
            'Livraison en 24h à Douala, impressionnant !',
            'Produit correct, rien à redire.',
        ]
        for p in random.sample(products, min(25, len(products))):
            for _ in range(random.randint(1, 4)):
                Review.objects.create(
                    product=p,
                    name=random.choice(review_names),
                    rating=random.choices([3,4,4,5,5,5], k=1)[0],
                    comment=random.choice(comments),
                )

        # Orders
        now = timezone.now()
        statuses = ['pending', 'confirmed', 'processing', 'shipped', 'delivered', 'cancelled']
        payments = ['momo', 'om', 'cash', 'card', 'transfer']
        cities = ['Douala', 'Yaoundé', 'Bafoussam', 'Bamenda', 'Garoua']
        for i in range(30):
            buyer = random.choice(buyers)
            status = random.choice(statuses)
            created = now - timedelta(days=random.randint(0, 120), hours=random.randint(0, 23))
            order = Order(
                buyer=buyer, status=status,
                payment_method=random.choice(payments),
                shipping_name=buyer.get_full_name(),
                shipping_phone=buyer.phone or '+237 677 000 000',
                shipping_address=f'Quartier {random.choice(["Akwa","Bonapriso","Bastos","Bonamoussadi","Melen"])}',
                shipping_city=buyer.city or random.choice(cities),
                is_paid=status in ['confirmed', 'delivered', 'shipped'],
            )
            order.save()
            Order.objects.filter(pk=order.pk).update(created_at=created)
            total = 0
            for prod in random.sample(products, random.randint(1, 4)):
                qty = random.randint(1, 3)
                OrderItem.objects.create(
                    order=order, product=prod, store=prod.store,
                    quantity=qty, price=prod.price,
                )
                total += int(prod.price) * qty
            shipping = 2000 if total < 50000 else 0
            order.subtotal = total
            order.shipping_cost = shipping
            order.total_amount = total + shipping
            order.save()

        # Messages
        for _ in range(5):
            buyer = random.choice(buyers)
            seller = random.choice(stores).owner
            convo, _ = Conversation.objects.get_or_create(buyer=buyer, seller=seller)
            Message.objects.get_or_create(conversation=convo, sender=buyer, defaults={
                'content': 'Bonjour, ce produit est-il disponible ? Pouvez-vous faire un prix pour 10 pièces ?'
            })
            Message.objects.get_or_create(conversation=convo, sender=seller, defaults={
                'content': 'Bonjour ! Oui, le produit est disponible. Pour 10 pièces, je peux vous faire -15%. Intéressé ?'
            })

        self.stdout.write(self.style.SUCCESS(
            f'Done! {Product.objects.count()} products, {Store.objects.count()} stores, '
            f'{Order.objects.count()} orders, {User.objects.count()} users, '
            f'{Review.objects.count()} reviews, {Category.objects.count()} categories'
        ))
        self.stdout.write(self.style.SUCCESS('Logins: admin/admin123 | sellers: techcm/seller123 | buyers: jean_k/buyer123'))


        # --- NEW: Subscription Plans ---
        from billing.models import SubscriptionPlan, Subscription, PaymentConfig, ProductBoost
        from accounting.models import Transaction, SellerWallet, Expense
        from datetime import date

        plans_data = [
            ('Starter', 'starter', 3000, 30000, 10, 3, 15.0, False, False, False, False, False, 0, 0, 'basic', False, 0,
             'Idéal pour démarrer\nSupport email'),
            ('Business', 'business', 8000, 80000, 50, 5, 10.0, True, True, True, False, False, 3, 2, 'silver', True, 1,
             'Idéal pour les PME\nAnalytics de base\nSupport email prioritaire'),
            ('Premium', 'premium', 15000, 150000, 200, 8, 7.0, True, True, True, True, True, 10, 5, 'gold', False, 2,
             'Pour les vendeurs sérieux\nAnalytics avancés\nSupport WhatsApp dédié\nBadge Gold'),
            ('Enterprise', 'enterprise', 35000, 350000, 9999, 10, 5.0, True, True, True, True, True, 30, 15, 'platinum', False, 3,
             'Illimité\nAccount manager dédié\nAPI access\nBadge Platinum\nFormation gratuite'),
        ]
        for name, slug, pm, py, maxp, maxi, comm, flash, boost, analytics, priority, custom, bcm, fp, badge, pop, order, features in plans_data:
            SubscriptionPlan.objects.get_or_create(slug=slug, defaults={
                'name': name, 'price_monthly': pm, 'price_yearly': py,
                'max_products': maxp, 'max_images_per_product': maxi,
                'commission_rate': comm, 'can_flash_deal': flash, 'can_boost': boost,
                'can_analytics': analytics, 'can_priority_support': priority,
                'can_custom_store': custom, 'boost_credits_monthly': bcm,
                'featured_products': fp, 'badge_level': badge, 'is_popular': pop,
                'order': order, 'features': features,
            })

        # Assign subscriptions to sellers
        business_plan = SubscriptionPlan.objects.get(slug='business')
        premium_plan = SubscriptionPlan.objects.get(slug='premium')
        for i, store in enumerate(stores):
            plan = premium_plan if i < 2 else business_plan
            Subscription.objects.get_or_create(user=store.owner, defaults={
                'plan': plan, 'status': 'active',
                'end_date': timezone.now() + timedelta(days=random.randint(15, 90)),
                'boost_credits_remaining': plan.boost_credits_monthly,
            })

        # Payment configs for sellers
        for store in stores:
            PaymentConfig.objects.get_or_create(user=store.owner, defaults={
                'momo_enabled': True, 'momo_number': f'+237 6{random.randint(50,99)} {random.randint(100,999)} {random.randint(100,999)}',
                'momo_name': store.owner.get_full_name(),
                'om_enabled': random.choice([True, False]),
                'om_number': f'+237 6{random.randint(90,99)} {random.randint(100,999)} {random.randint(100,999)}',
                'cash_enabled': True,
            })

        # Seller wallets + Transactions
        for store in stores:
            wallet, _ = SellerWallet.objects.get_or_create(user=store.owner, defaults={
                'balance': random.randint(50000, 500000),
                'pending_balance': random.randint(10000, 100000),
                'total_earned': random.randint(500000, 5000000),
                'total_withdrawn': random.randint(200000, 2000000),
                'total_commission_paid': random.randint(50000, 500000),
            })
            # Sample transactions
            for _ in range(random.randint(5, 15)):
                t_type = random.choice(['sale', 'sale', 'sale', 'commission', 'subscription', 'boost', 'payout'])
                amt = random.randint(5000, 500000)
                comm_amt = int(amt * 0.1) if t_type == 'sale' else 0
                Transaction.objects.create(
                    user=store.owner, type=t_type,
                    amount=amt, commission_amount=comm_amt,
                    net_amount=amt - comm_amt,
                    status='completed',
                    payment_method=random.choice(['momo', 'om', 'card']),
                    reference=f'Transaction {t_type} #{random.randint(1000,9999)}',
                )

        # Sample boosts
        for store in stores[:3]:
            for prod in store.products.all()[:2]:
                ProductBoost.objects.get_or_create(product=prod, user=store.owner, defaults={
                    'platform': random.choice(['internal', 'facebook', 'instagram']),
                    'status': random.choice(['active', 'completed']),
                    'budget': random.choice([5000, 10000, 25000]),
                    'duration_days': random.choice([3, 7, 14]),
                    'impressions': random.randint(500, 15000),
                    'clicks': random.randint(20, 500),
                    'start_date': timezone.now() - timedelta(days=random.randint(1, 30)),
                    'end_date': timezone.now() + timedelta(days=random.randint(1, 14)),
                })

        # Platform expenses (admin only)
        expense_items = [
            ('hosting', 'Hébergement serveur AWS', 85000, date(2026, 1, 15)),
            ('hosting', 'Hébergement serveur AWS', 85000, date(2026, 2, 15)),
            ('hosting', 'Hébergement serveur AWS', 85000, date(2026, 3, 15)),
            ('marketing', 'Campagne Facebook Ads', 150000, date(2026, 1, 20)),
            ('marketing', 'Campagne Google Ads', 120000, date(2026, 2, 10)),
            ('salary', 'Salaires équipe (5 personnes)', 1500000, date(2026, 1, 30)),
            ('salary', 'Salaires équipe (5 personnes)', 1500000, date(2026, 2, 28)),
            ('salary', 'Salaires équipe (5 personnes)', 1500000, date(2026, 3, 15)),
            ('software', 'Licence Figma + Slack', 45000, date(2026, 1, 5)),
            ('logistics', 'Partenariat livraison DHL', 200000, date(2026, 2, 1)),
            ('office', 'Loyer bureau Bonanjo', 350000, date(2026, 1, 1)),
            ('office', 'Loyer bureau Bonanjo', 350000, date(2026, 2, 1)),
            ('office', 'Loyer bureau Bonanjo', 350000, date(2026, 3, 1)),
        ]
        for cat, desc, amt, d in expense_items:
            Expense.objects.get_or_create(description=desc, date=d, defaults={
                'category': cat, 'amount': amt, 'created_by': admin,
            })

        self.stdout.write(self.style.SUCCESS(
            f'NEW: {SubscriptionPlan.objects.count()} plans, '
            f'{Subscription.objects.count()} subscriptions, '
            f'{Transaction.objects.count()} transactions, '
            f'{ProductBoost.objects.count()} boosts, '
            f'{Expense.objects.count()} expenses'
        ))
