# 💰 Modèle de Paiement pour les Boosts Facebook & WhatsApp

## 🔄 Deux approches possibles

### **Option 1 : Modèle Intermédiaire (RECOMMANDÉ)**

**Comment ça fonctionne :**

```
Client AfriMarket
    ↓ (paie)
AfriMarket (Plateforme)
    ↓ (configure & paie)
Facebook/WhatsApp
```

#### Avantages :
✅ **Contrôle total** : AfriMarket gère tout le processus
✅ **Marge commerciale** : Vous prenez une commission (ex: 20-30%)
✅ **Support client** : Vous gérez les problèmes
✅ **Analytics unifiés** : Toutes les stats dans votre dashboard
✅ **Simplicité pour le client** : Un seul compte, un seul paiement

#### Fonctionnement détaillé :

1. **Client paie AfriMarket** : 25,000 FCFA
2. **AfriMarket prend sa marge** : 5,000 FCFA (20%)
3. **AfriMarket paie Facebook** : 20,000 FCFA
4. **Facebook diffuse la pub** avec votre compte Facebook Ads

#### Configuration requise :

```python
# settings.py
BOOST_COMMISSION_RATE = 0.20  # 20% de commission
FACEBOOK_AD_ACCOUNT_ID = 'act_xxxxx'  # Votre compte Facebook Ads
FACEBOOK_PAYMENT_METHOD = 'credit_card'  # Carte de crédit d'AfriMarket
```

#### Implémentation :

```python
# billing/views.py
def boost_product(request, product_id):
    # ... code existant ...

    if platform == 'facebook':
        # 1. Client paie AfriMarket
        client_payment = budget  # Ex: 25,000 FCFA

        # 2. Calculer la commission d'AfriMarket
        commission = int(client_payment * settings.BOOST_COMMISSION_RATE)
        facebook_budget = client_payment - commission

        # 3. Débiter le client
        Transaction.objects.create(
            user=request.user,
            type='boost',
            amount=client_payment,
            status='completed',
            reference=f"Boost Facebook - {product.name}",
        )

        # 4. Enregistrer la commission d'AfriMarket
        Transaction.objects.create(
            user=None,  # Transaction plateforme
            type='platform_revenue',
            amount=commission,
            status='completed',
            reference=f"Commission boost Facebook - {product.name}",
        )

        # 5. Créer la campagne Facebook avec le budget net
        try:
            fb_data = create_facebook_campaign(
                product=product,
                budget=facebook_budget,  # 20,000 FCFA pour Facebook
                duration=duration,
                targeting=targeting_data
            )

            boost.external_campaign_id = fb_data['campaign_id']
            boost.platform_budget = facebook_budget
            boost.commission = commission
            boost.status = 'active'
            boost.save()

        except Exception as e:
            # Si la campagne Facebook échoue, rembourser le client
            Transaction.objects.create(
                user=request.user,
                type='refund',
                amount=client_payment,
                status='completed',
                reference=f"Remboursement boost Facebook - Erreur",
            )
```

---

### **Option 2 : Modèle Direct (NON RECOMMANDÉ)**

**Comment ça fonctionne :**

```
Client
    ↓ (paie directement)
Facebook/WhatsApp
```

#### Inconvénients :
❌ **Pas de marge** pour AfriMarket
❌ **Client doit avoir compte Facebook Ads**
❌ **Complexe** pour le client
❌ **Support difficile**
❌ **Pas d'analytics unifiés**

---

## 🏦 Configuration du compte Facebook Ads d'AfriMarket

### Étape 1 : Créer un compte Facebook Business

1. Aller sur https://business.facebook.com
2. Créer un compte Business Manager
3. Ajouter une méthode de paiement (carte bancaire d'AfriMarket)

### Étape 2 : Créer un compte publicitaire

1. Dans Business Manager → Paramètres → Comptes publicitaires
2. Créer un nouveau compte (récupérer l'ID : `act_xxxxx`)
3. Définir le fuseau horaire : **UTC+1** (Cameroun)
4. Devise : **XAF** (Franc CFA)

### Étape 3 : Configuration de la carte de crédit

```
Facebook facture automatiquement selon deux seuils :
- Quand le solde atteint 25€ (~16,000 FCFA)
- Tous les 15 du mois
```

**⚠️ Important :** Avoir une réserve de trésorerie pour couvrir les campagnes en cours.

---

## 💳 Flux de trésorerie

### Scénario exemple :

**10 clients lancent des boosts Facebook de 25,000 FCFA chacun**

```
Revenus :
- Total payé par clients : 250,000 FCFA
- Commission AfriMarket (20%) : 50,000 FCFA
- À payer à Facebook : 200,000 FCFA

Trésorerie :
- AfriMarket reçoit : 250,000 FCFA
- AfriMarket dépense sur Facebook : 200,000 FCFA
- Profit net : 50,000 FCFA
```

### Gestion du cash-flow :

```python
# billing/models.py
class PlatformBalance(models.Model):
    """Solde de trésorerie pour les campagnes externes"""
    platform = models.CharField(max_length=20)  # facebook, whatsapp
    balance = models.DecimalField(max_digits=15, decimal_places=0)
    pending_expenses = models.DecimalField(max_digits=15, decimal_places=0)
    last_updated = models.DateTimeField(auto_now=True)

    def has_sufficient_balance(self, amount):
        """Vérifie si le solde est suffisant pour une nouvelle campagne"""
        return self.balance >= (self.pending_expenses + amount)
```

---

## 📊 Dashboard administrateur

Créer une page pour suivre les finances des boosts :

```python
# dashboard/views.py
@staff_required
def boost_finances(request):
    """Dashboard financier des boosts"""

    # Statistiques globales
    total_boosts = ProductBoost.objects.filter(
        platform__in=['facebook', 'whatsapp']
    ).aggregate(
        total_revenue=Sum('budget'),
        total_commission=Sum('commission'),
        count=Count('id')
    )

    # Solde Facebook actuel (via API)
    fb_balance = get_facebook_ad_account_balance()

    # Dépenses du mois
    this_month = timezone.now().replace(day=1)
    monthly_spending = ProductBoost.objects.filter(
        platform='facebook',
        created_at__gte=this_month
    ).aggregate(total=Sum('platform_budget'))

    return render(request, 'dashboard/boost_finances.html', {
        'total_boosts': total_boosts,
        'fb_balance': fb_balance,
        'monthly_spending': monthly_spending,
    })

def get_facebook_ad_account_balance():
    """Récupère le solde du compte Facebook Ads"""
    from facebook_business.api import FacebookAdsApi
    from facebook_business.adobjects.adaccount import AdAccount

    FacebookAdsApi.init(...)
    account = AdAccount(settings.FACEBOOK_AD_ACCOUNT_ID)

    balance_data = account.api_get(fields=['balance', 'currency'])
    return {
        'balance': balance_data['balance'],
        'currency': balance_data['currency'],
    }
```

---

## 🔄 Webhooks Facebook

Configurer des webhooks pour recevoir les notifications de Facebook :

```python
# billing/webhooks.py
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
import hmac
import hashlib

@csrf_exempt
def facebook_webhook(request):
    """Webhook pour recevoir les événements Facebook"""

    if request.method == 'GET':
        # Vérification du webhook
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        if mode == 'subscribe' and token == settings.FACEBOOK_WEBHOOK_TOKEN:
            return HttpResponse(challenge)

    elif request.method == 'POST':
        # Recevoir les événements
        data = json.loads(request.body)

        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                field = change.get('field')
                value = change.get('value')

                if field == 'ads':
                    # Campagne mise à jour
                    campaign_id = value.get('campaign_id')
                    status = value.get('status')

                    # Mettre à jour le boost dans la DB
                    ProductBoost.objects.filter(
                        external_campaign_id=campaign_id
                    ).update(status=status)

        return JsonResponse({'status': 'ok'})
```

---

## 🚨 Gestion des erreurs

```python
# billing/utils.py
class BoostError(Exception):
    """Erreur lors de la création d'un boost"""
    pass

def handle_boost_failure(boost, client_payment, error):
    """Gère l'échec d'un boost et rembourse le client"""

    # 1. Marquer le boost comme échoué
    boost.status = 'failed'
    boost.error_message = str(error)
    boost.save()

    # 2. Rembourser le client
    Transaction.objects.create(
        user=boost.user,
        type='refund',
        amount=client_payment,
        status='completed',
        reference=f"Remboursement boost #{boost.id} - Erreur technique",
    )

    # 3. Notifier le client
    send_notification(
        user=boost.user,
        title="Erreur de boost",
        message=f"Votre campagne {boost.product.name} n'a pas pu être lancée. "
                f"Vous avez été remboursé de {client_payment:,} FCFA."
    )

    # 4. Alerter l'équipe
    send_admin_alert(
        subject="Échec de création de boost",
        message=f"Boost #{boost.id} échoué : {error}"
    )
```

---

## 💡 Recommandations

### Pour démarrer (Phase 1) :

1. ✅ **Mode manuel** : Équipe AfriMarket configure les campagnes manuellement
2. ✅ **Budget modéré** : Limiter à 50,000 FCFA par campagne
3. ✅ **Validation manuelle** : Approuver chaque boost avant activation
4. ✅ **Commencer avec AfriMarket interne** uniquement

### Pour évoluer (Phase 2) :

1. 🔄 Automatiser avec Facebook Marketing API
2. 📊 Dashboard analytics avancé
3. 🤖 Webhooks pour le suivi en temps réel
4. 💳 Augmenter les limites de budget

### Pour optimiser (Phase 3) :

1. 🎯 Machine learning pour optimisation des campagnes
2. 📈 A/B testing automatique
3. 🔄 Remarketing dynamique
4. 🌍 Expansion vers d'autres pays

---

## 📝 Checklist de mise en place

### Technique :
- [ ] Créer compte Facebook Business
- [ ] Configurer le compte publicitaire
- [ ] Ajouter méthode de paiement (carte AfriMarket)
- [ ] Obtenir les tokens d'accès
- [ ] Implémenter le modèle de commission
- [ ] Créer le dashboard financier
- [ ] Mettre en place les webhooks
- [ ] Tester en mode sandbox

### Juridique :
- [ ] Conditions générales mentionnant la commission
- [ ] Politique de remboursement
- [ ] Contrat avec Facebook Business (si nécessaire)
- [ ] Conformité RGPD pour les données de ciblage

### Opérationnel :
- [ ] Former l'équipe à Facebook Ads Manager
- [ ] Créer des procédures de support
- [ ] Définir les SLA (délai d'activation)
- [ ] Mettre en place le monitoring

---

## 🎯 TL;DR

**Solution recommandée :**
- Client paie AfriMarket : **25,000 FCFA**
- AfriMarket garde commission : **5,000 FCFA** (20%)
- AfriMarket paie Facebook : **20,000 FCFA**
- Facebook diffuse avec le compte d'AfriMarket

**Bénéfices :**
- ✅ Revenue stream pour AfriMarket
- ✅ Contrôle total du processus
- ✅ Expérience simplifiée pour le client
- ✅ Support centralisé
