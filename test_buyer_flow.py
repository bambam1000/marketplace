"""Test fonctionnel du parcours acheteur â€” Ã  supprimer aprÃ¨s vÃ©rification."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DJANGO_ALLOWED_HOSTS'] = '127.0.0.1,localhost,testserver'
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from catalog.models import Category, Product
from store.models import Store

User = get_user_model()

# PrÃ©parer les donnÃ©es
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

c = Client()
results = []

def check(name, condition):
    results.append((name, condition))
    print(('âœ…' if condition else 'âŒ'), name)

# 1. Inscription
r = c.post('/fr/compte/inscription/', {
    'username': 'acheteur_test', 'email': 'acheteur@test.com',
    'password': 'testpass123', 'role': 'buyer',
    'first_name': 'Jean', 'last_name': 'Test',
})
check('Inscription acheteur (redirection)', r.status_code == 302)
check('Utilisateur crÃ©Ã© avec rÃ´le buyer', User.objects.filter(username='acheteur_test', role='buyer').exists())

# 2. DÃ©connexion + Connexion
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
check('Panier contient 2 articles', c.session.get('cart', {}).get(str(product.id), {}).get('quantity') == 2)

# 5. Commande (checkout)
r = c.post('/fr/panier/commander/', {
    'payment_method': 'momo', 'shipping_name': 'Jean Test',
    'shipping_phone': '677123456', 'shipping_address': 'Rue 1',
    'shipping_city': 'Douala',
})
check('Commande crÃ©Ã©e (redirection)', r.status_code == 302)
from orders.models import Order
order = Order.objects.filter(buyer__username='acheteur_test').first()
check('Commande en base avec numÃ©ro', order is not None and order.order_number.startswith('ALB-'))
check('Montant total correct (2Ã—5000 + 2000 livraison)', order and order.total_amount == 12000)
product.refresh_from_db()
check('Stock dÃ©crÃ©mentÃ© (20 â†’ 18)', product.stock == 18)

# 6. Suivi des commandes
r = c.get('/fr/commandes/')
check('Liste des commandes accessible', r.status_code == 200)
r = c.get(f'/fr/commandes/{order.order_number}/')
check('DÃ©tail commande accessible', r.status_code == 200)

# 6b. SÃ©curitÃ© : un autre acheteur ne peut PAS voir cette commande
other = User.objects.create_user(username='autre', password='testpass123', role='buyer')
c2 = Client()
c2.login(username='autre', password='testpass123')
r = c2.get(f'/fr/commandes/{order.order_number}/')
check('Un autre acheteur ne peut pas voir la commande (404)', r.status_code == 404)

# 7. Messagerie
r = c.get(f'/fr/messages/nouveau/{seller.id}/')
check('CrÃ©ation conversation avec vendeur', r.status_code == 302)
from messaging.models import Conversation
convo = Conversation.objects.filter(buyer__username='acheteur_test', seller=seller).first()
check('Conversation crÃ©Ã©e', convo is not None)
r = c.post(f'/fr/messages/conversation/{convo.id}/', {'content': 'Bonjour, question sur le produit'})
check('Envoi message', r.status_code == 200)
check('Message enregistrÃ©', convo.messages.filter(content__contains='Bonjour').exists())
r = c.get('/fr/messages/')
check('BoÃ®te de rÃ©ception accessible', r.status_code == 200)

# 8. Dashboard acheteur
r = c.get('/fr/dashboard/')
check('Dashboard accessible Ã  l\'acheteur', r.status_code == 200)

# 9. Pages publiques
r = c.get('/fr/compte/mot-de-passe-oublie/')
check('Page mot de passe oubliÃ© accessible', r.status_code == 200)

print()
failed = [n for n, ok in results if not ok]
print(f'RÃ‰SULTAT : {len(results) - len(failed)}/{len(results)} rÃ©ussis')
if failed:
    print('Ã‰CHECS :', failed)


