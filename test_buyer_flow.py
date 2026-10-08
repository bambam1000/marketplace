"""Test fonctionnel rapide du parcours acheteur (relançable, base isolée)."""
import os
import django

import test_utils  # noqa: F401 — base de test isolée (copie de db.sqlite3)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DJANGO_ALLOWED_HOSTS'] = '127.0.0.1,localhost,testserver'
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from catalog.models import Category, Product
from store.models import Store

User = get_user_model()

# Préparer les données
# Repartir propre (on travaille sur une copie de la base, voir test_utils)
User.objects.filter(username__in=['acheteur_test', 'autre']).delete()
seller, _ = User.objects.get_or_create(username='vendeur_test', defaults={'role': 'seller'})
seller.set_password('testpass123')
seller.save()
store, _ = Store.objects.get_or_create(owner=seller, defaults={'name': 'Boutique Test'})
cat, _ = Category.objects.get_or_create(slug='test-cat', defaults={'name': 'Test'})
product, _ = Product.objects.get_or_create(
    slug='produit-test',
    defaults={'store': store, 'category': cat, 'name': 'Produit Test',
              'description': 'Desc', 'price': 5000, 'stock': 20},
)
product.stock = 20  # stock connu au départ, même si le produit existait déjà
product.is_active = True
product.save(update_fields=['stock', 'is_active'])

c = Client()
results = []

def check(name, condition):
    results.append((name, condition))
    print(('✅' if condition else '❌'), name)

# 1. Inscription
r = c.post('/fr/compte/inscription/', {
    'username': 'acheteur_test', 'email': 'acheteur@test.com',
    'password': 'testpass123', 'role': 'buyer',
    'first_name': 'Jean', 'last_name': 'Test',
})
check('Inscription acheteur (redirection)', r.status_code == 302)
check('Utilisateur créé avec rôle buyer', User.objects.filter(username='acheteur_test', role='buyer').exists())

# 2. Déconnexion + Connexion
c.get('/fr/compte/deconnexion/')
r = c.post('/fr/compte/connexion/', {'username': 'acheteur_test', 'password': 'testpass123'})
check('Connexion (redirection)', r.status_code == 302)

# 3. Catalogue
r = c.get('/fr/')
check('Page d\'accueil accessible', r.status_code == 200)
r = c.get(f'/fr/boutique/produit/{product.slug}/')
check('Page produit accessible', r.status_code == 200)

# 4. Panier
r = c.post(f'/fr/panier/ajouter/{product.id}/', {'quantity': 2})
check('Ajout au panier', r.status_code == 302)
r = c.get('/fr/panier/')
check('Page panier accessible', r.status_code == 200)
from cart.models import CartItem  # panier persistant en base pour les utilisateurs connectés
item = CartItem.objects.filter(user__username='acheteur_test', product=product).first()
check('Panier contient 2 articles', item is not None and item.quantity == 2)

# 5. Commande (checkout)
r = c.post('/fr/panier/commander/', {
    'payment_method': 'momo', 'shipping_name': 'Jean Test',
    'shipping_phone': '677123456', 'shipping_address': 'Rue 1',
    'shipping_city': 'Douala',
})
check('Commande créée (redirection)', r.status_code == 302)
from orders.models import Order
order = Order.objects.filter(buyer__username='acheteur_test').first()
check('Commande en base avec numéro', order is not None and order.order_number.startswith('ALB-'))
check('Montant total correct (2×5000 + 2000 livraison)', order and order.total_amount == 12000)
product.refresh_from_db()
check('Stock décrémenté (20 → 18)', product.stock == 18)

# 6. Suivi des commandes
r = c.get('/fr/commandes/')
check('Liste des commandes accessible', r.status_code == 200)
r = c.get(f'/fr/commandes/{order.order_number}/')
check('Détail commande accessible', r.status_code == 200)

# 6b. Sécurité : un autre acheteur ne peut PAS voir cette commande
other = User.objects.create_user(username='autre', password='testpass123', role='buyer')
c2 = Client()
c2.login(username='autre', password='testpass123')
r = c2.get(f'/fr/commandes/{order.order_number}/')
check('Un autre acheteur ne peut pas voir la commande (404)', r.status_code == 404)

# 7. Messagerie
r = c.get(f'/fr/messages/nouveau/{seller.id}/')
check('Création conversation avec vendeur', r.status_code == 302)
from messaging.models import Conversation
convo = Conversation.objects.filter(buyer__username='acheteur_test', seller=seller).first()
check('Conversation créée', convo is not None)
r = c.post(f'/fr/messages/conversation/{convo.id}/', {'content': 'Bonjour, question sur le produit'})
check('Envoi message', r.status_code == 200)
check('Message enregistré', convo.messages.filter(content__contains='Bonjour').exists())
r = c.get('/fr/messages/')
check('Boîte de réception accessible', r.status_code == 200)

# 8. Dashboard vendeur : réservé aux vendeurs, l'acheteur est redirigé
r = c.get('/fr/dashboard/')
check('Acheteur redirigé hors du dashboard vendeur', r.status_code == 302)

# 9. Pages publiques
r = c.get('/fr/compte/mot-de-passe-oublie/')
check('Page mot de passe oublié accessible', r.status_code == 200)

print()
failed = [n for n, ok in results if not ok]
print(f'RÉSULTAT : {len(results) - len(failed)}/{len(results)} réussis')
if failed:
    print('ÉCHECS :', failed)


