# Guide d'intégration des Boosts Facebook & WhatsApp

## 📘 Facebook Marketing API

### Prérequis
1. Créer une application Facebook sur https://developers.facebook.com/
2. Obtenir un **Access Token** avec les permissions suivantes :
   - `ads_management`
   - `pages_manage_ads`
   - `pages_read_engagement`
3. Installer le SDK : `pip install facebook-business`

### Configuration
```python
# settings.py
FACEBOOK_APP_ID = 'your_app_id'
FACEBOOK_APP_SECRET = 'your_app_secret'
FACEBOOK_ACCESS_TOKEN = 'your_access_token'
FACEBOOK_AD_ACCOUNT_ID = 'act_xxxxx'
```

### Implémentation
```python
# billing/facebook_ads.py
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.ad import Ad

def create_facebook_campaign(product, budget, duration, targeting):
    """
    Crée une campagne Facebook Ads pour un produit
    """
    FacebookAdsApi.init(
        app_id=settings.FACEBOOK_APP_ID,
        app_secret=settings.FACEBOOK_APP_SECRET,
        access_token=settings.FACEBOOK_ACCESS_TOKEN
    )

    account = AdAccount(settings.FACEBOOK_AD_ACCOUNT_ID)

    # 1. Créer la campagne
    campaign = account.create_campaign(params={
        'name': f'AfriMarket - {product.name}',
        'objective': 'OUTCOME_TRAFFIC',  # ou 'OUTCOME_SALES'
        'status': 'ACTIVE',
        'special_ad_categories': [],
    })

    # 2. Créer l'ensemble de publicités (Ad Set)
    adset = account.create_ad_set(params={
        'name': f'AdSet - {product.name}',
        'campaign_id': campaign['id'],
        'billing_event': 'IMPRESSIONS',
        'optimization_goal': 'LINK_CLICKS',
        'bid_amount': 100,  # en centimes
        'daily_budget': budget * 100 / duration,  # Budget journalier
        'targeting': {
            'geo_locations': {
                'countries': ['CM'],  # Cameroun
                'cities': [{'key': targeting['city']}] if targeting['city'] else [],
            },
            'age_min': int(targeting['age'].split('-')[0]) if targeting['age'] else 18,
            'age_max': int(targeting['age'].split('-')[1]) if '-' in targeting['age'] else 65,
        },
        'status': 'ACTIVE',
    })

    # 3. Créer la création publicitaire
    creative = account.create_ad_creative(params={
        'name': f'Creative - {product.name}',
        'object_story_spec': {
            'page_id': targeting['fb_page_id'],
            'link_data': {
                'message': product.description[:500],
                'link': f'https://afrimarketplace.cm/produit/{product.slug}/',
                'name': product.name,
                'description': f'Prix: {product.price} FCFA',
                'image_url': product.image.url if product.image else None,
                'call_to_action': {
                    'type': 'SHOP_NOW',
                }
            }
        }
    })

    # 4. Créer l'annonce
    ad = account.create_ad(params={
        'name': f'Ad - {product.name}',
        'adset_id': adset['id'],
        'creative': {'creative_id': creative['id']},
        'status': 'ACTIVE',
    })

    return {
        'campaign_id': campaign['id'],
        'adset_id': adset['id'],
        'ad_id': ad['id'],
        'creative_id': creative['id'],
    }

def get_campaign_insights(campaign_id):
    """
    Récupère les statistiques d'une campagne
    """
    campaign = Campaign(campaign_id)
    insights = campaign.get_insights(params={
        'fields': [
            'impressions',
            'clicks',
            'ctr',
            'spend',
            'reach',
            'actions',
        ],
    })
    return insights[0] if insights else None
```

---

## 💬 WhatsApp Business API

### Prérequis
1. Compte WhatsApp Business API
2. Numéro de téléphone vérifié
3. Installer le SDK : `pip install whatsapp-business-client`

### Configuration
```python
# settings.py
WHATSAPP_API_URL = 'https://graph.facebook.com/v18.0'
WHATSAPP_PHONE_NUMBER_ID = 'your_phone_number_id'
WHATSAPP_ACCESS_TOKEN = 'your_access_token'
WHATSAPP_BUSINESS_ACCOUNT_ID = 'your_business_account_id'
```

### Implémentation
```python
# billing/whatsapp_business.py
import requests
import json

def create_whatsapp_catalog_product(product):
    """
    Ajoute un produit au catalogue WhatsApp Business
    """
    url = f"{settings.WHATSAPP_API_URL}/{settings.WHATSAPP_BUSINESS_ACCOUNT_ID}/catalog"

    headers = {
        'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }

    data = {
        'name': product.name,
        'description': product.description,
        'price': int(product.price),
        'currency': 'XAF',  # FCFA
        'image_url': product.image.url if product.image else None,
        'url': f'https://afrimarketplace.cm/produit/{product.slug}/',
        'availability': 'in stock' if product.in_stock else 'out of stock',
        'retailer_id': str(product.id),
    }

    response = requests.post(url, headers=headers, json=data)
    return response.json()

def send_whatsapp_broadcast(product, recipients, budget):
    """
    Envoie un message broadcast pour promouvoir un produit
    """
    url = f"{settings.WHATSAPP_API_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"

    headers = {
        'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }

    for recipient in recipients:
        data = {
            'messaging_product': 'whatsapp',
            'to': recipient,
            'type': 'template',
            'template': {
                'name': 'product_promotion',  # Template pré-approuvé
                'language': {
                    'code': 'fr'
                },
                'components': [
                    {
                        'type': 'header',
                        'parameters': [
                            {
                                'type': 'image',
                                'image': {
                                    'link': product.image.url
                                }
                            }
                        ]
                    },
                    {
                        'type': 'body',
                        'parameters': [
                            {'type': 'text', 'text': product.name},
                            {'type': 'text', 'text': f'{product.price:,} FCFA'},
                        ]
                    },
                    {
                        'type': 'button',
                        'sub_type': 'url',
                        'index': '0',
                        'parameters': [
                            {
                                'type': 'text',
                                'text': f'produit/{product.slug}/'
                            }
                        ]
                    }
                ]
            }
        }

        response = requests.post(url, headers=headers, json=data)

def get_whatsapp_analytics(phone_number_id):
    """
    Récupère les statistiques WhatsApp Business
    """
    url = f"{settings.WHATSAPP_API_URL}/{phone_number_id}/analytics"

    headers = {
        'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}',
    }

    params = {
        'fields': 'messages_sent,messages_delivered,messages_read',
        'start': '2024-01-01',
        'end': '2024-12-31',
    }

    response = requests.get(url, headers=headers, params=params)
    return response.json()
```

---

## 🔄 Mise à jour de la vue

```python
# billing/views.py
from .facebook_ads import create_facebook_campaign, get_campaign_insights
from .whatsapp_business import create_whatsapp_catalog_product, send_whatsapp_broadcast

@login_required
def boost_product(request, product_id):
    # ... code existant ...

    elif boost.platform == 'facebook':
        try:
            fb_data = create_facebook_campaign(
                product=product,
                budget=budget,
                duration=duration,
                targeting=targeting_data
            )
            boost.external_campaign_id = fb_data['campaign_id']
            boost.status = 'active'
            boost.save()
            messages.success(request, '✅ Campagne Facebook Ads activée avec succès !')
        except Exception as e:
            messages.error(request, f'Erreur lors de la création de la campagne Facebook: {str(e)}')

    elif boost.platform == 'whatsapp':
        try:
            # Ajouter au catalogue
            catalog_data = create_whatsapp_catalog_product(product)

            # Envoyer aux clients (optionnel)
            # recipients = get_customer_phone_numbers(request.user)
            # send_whatsapp_broadcast(product, recipients, budget)

            boost.status = 'active'
            boost.save()
            messages.success(request, '✅ Produit ajouté au catalogue WhatsApp !')
        except Exception as e:
            messages.error(request, f'Erreur WhatsApp: {str(e)}')
```

---

## 📊 Suivi des performances

Créer une tâche Celery pour mettre à jour les statistiques quotidiennement :

```python
# billing/tasks.py
from celery import shared_task
from .models import ProductBoost
from .facebook_ads import get_campaign_insights

@shared_task
def update_boost_statistics():
    """
    Met à jour les statistiques de toutes les campagnes actives
    """
    active_boosts = ProductBoost.objects.filter(status='active')

    for boost in active_boosts:
        if boost.platform == 'facebook' and boost.external_campaign_id:
            insights = get_campaign_insights(boost.external_campaign_id)
            if insights:
                boost.impressions = insights.get('impressions', 0)
                boost.clicks = insights.get('clicks', 0)
                boost.save(update_fields=['impressions', 'clicks'])
```

---

## 🔐 Sécurité

1. **Ne jamais exposer les tokens** dans le code source
2. Utiliser des **variables d'environnement** (.env)
3. Implémenter une **rotation des tokens**
4. Limiter les **permissions des tokens**
5. Logger toutes les **transactions financières**

---

## 📝 Template de message WhatsApp

Sur WhatsApp Business Manager, créer un template nommé `product_promotion` :

```
[IMAGE]

🛍️ *{{1}}* en promotion !

💰 Prix: {{2}}

✨ Livraison rapide partout au Cameroun
🔒 Paiement sécurisé

[Bouton: Voir le produit]
```

---

## ✅ Checklist de déploiement

- [ ] Créer l'app Facebook Developer
- [ ] Obtenir les tokens d'accès
- [ ] Configurer le compte WhatsApp Business
- [ ] Créer les templates de messages
- [ ] Tester les campagnes en sandbox
- [ ] Implémenter les webhooks pour les callbacks
- [ ] Configurer le monitoring (Sentry)
- [ ] Mettre en place les alertes budgétaires
- [ ] Documenter le processus pour l'équipe

---

**Note importante :** Pour le moment, le système fonctionne en mode "manuel" où l'équipe AfriMarket configure les campagnes. L'intégration complète des APIs peut être faite progressivement.
