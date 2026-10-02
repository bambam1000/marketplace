"""Assistant IA du dashboard — conversationnel, analyse les données réelles, guide complet."""
from django.utils import timezone
from django.db.models import Sum, Count, F
from datetime import timedelta


# ═══════════════════════════════════════════
# BRIEFING (alertes + stats en temps réel)
# ═══════════════════════════════════════════

def get_briefing(store):
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    alerts = []
    stats = {}

    from catalog.models import Product
    from orders.models import Order, OrderItem, RFQ
    from invoicing.models import Invoice

    low_stock = Product.objects.filter(store=store, is_active=True, stock__lte=F('low_stock_threshold'), stock__gt=0)
    out_of_stock = Product.objects.filter(store=store, is_active=True, stock=0)
    if out_of_stock.exists():
        alerts.append({'icon': 'fa-circle-xmark', 'color': 'var(--dash-red)', 'title': f'{out_of_stock.count()} produit(s) en rupture', 'detail': ', '.join(p.name for p in out_of_stock[:3]), 'url': '/dashboard/produits/'})
    if low_stock.exists():
        alerts.append({'icon': 'fa-triangle-exclamation', 'color': 'var(--dash-yellow)', 'title': f'{low_stock.count()} produit(s) en stock faible', 'detail': ', '.join(f'{p.name} ({p.stock})' for p in low_stock[:3]), 'url': '/dashboard/produits/'})

    pending = Order.objects.filter(items__store=store, status='pending').distinct()
    if pending.exists():
        alerts.append({'icon': 'fa-box', 'color': 'var(--dash-accent)', 'title': f'{pending.count()} commande(s) en attente', 'detail': 'Confirmez-les pour notifier les clients.', 'url': '/dashboard/commandes/?status=pending'})

    open_rfqs = RFQ.objects.filter(status='open').exclude(quotes__store=store)
    if open_rfqs.exists():
        alerts.append({'icon': 'fa-file-invoice', 'color': 'var(--dash-blue)', 'title': f'{open_rfqs.count()} devis sans votre réponse', 'detail': 'Répondez vite pour ne pas perdre le client.', 'url': '/dashboard/devis/'})

    unpaid = Invoice.objects.filter(store=store, status='sent')
    if unpaid.exists():
        total_unpaid = unpaid.aggregate(t=Sum('total_amount'))['t'] or 0
        alerts.append({'icon': 'fa-receipt', 'color': 'var(--dash-red)', 'title': f'{unpaid.count()} facture(s) impayée(s) — {total_unpaid:,.0f} FCFA', 'detail': 'Relancez vos clients.', 'url': '/facturation/invoices/?status=sent'})

    week_items = OrderItem.objects.filter(product__store=store, order__created_at__gte=week_ago, order__is_paid=True)
    stats['week_revenue'] = week_items.aggregate(t=Sum(F('price') * F('quantity')))['t'] or 0
    stats['week_orders'] = Order.objects.filter(items__store=store, created_at__gte=week_ago).distinct().count()
    stats['total_products'] = Product.objects.filter(store=store, is_active=True).count()
    stats['total_customers'] = Order.objects.filter(items__store=store).values('buyer').distinct().count()

    suggestions = []
    if stats['week_revenue'] == 0:
        suggestions.append("Aucune vente cette semaine. Créez un code promo ou une campagne pour relancer.")
    if low_stock.exists() or out_of_stock.exists():
        suggestions.append("Réapprovisionnez vos produits en rupture pour ne pas perdre de ventes.")
    if pending.exists():
        suggestions.append("Confirmez vos commandes en attente pour améliorer la satisfaction client.")
    if not suggestions:
        suggestions.append("Tout va bien ! Pensez à lancer une campagne marketing pour booster vos ventes.")

    return {'alerts': alerts, 'stats': stats, 'suggestions': suggestions}


# ═══════════════════════════════════════════
# CONVERSATION
# ═══════════════════════════════════════════

def ask(question, store, user=None):
    """Répond à n'importe quelle question — conversation, données réelles, guide."""
    q = question.lower().strip()
    if not q:
        return "Posez-moi une question !"

    name = user.first_name or user.username if user else ''
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    from catalog.models import Product
    from orders.models import Order, OrderItem, RFQ
    from invoicing.models import Invoice
    from marketing.models import PromoCode, Campaign, LoyaltyProgram, Newsletter, MessagingCampaign, FacebookPost
    from store.models import StoreMember, Payslip
    from inventory.models import Warehouse, StockTransfer

    # ── Salutations ──
    if any(w in q for w in ['bonjour', 'salut', 'hello', 'bonsoir', 'coucou', 'hey']):
        return (
            f"Bonjour {name} ! 👋\n\n"
            f"Je suis votre assistant AfriMarket. Je peux :\n\n"
            f"📊 **Analyser votre boutique** — « ventes du jour », « état du stock », « mes clients »\n"
            f"📖 **Vous guider** — « comment ajouter un produit », « créer un code promo »\n"
            f"⚡ **Vous alerter** — ruptures, commandes en attente, factures impayées\n\n"
            f"Que puis-je faire pour vous ?"
        )

    # ── Remerciements ──
    if any(w in q for w in ['merci', 'thanks', 'super', 'génial', 'parfait']):
        return f"Avec plaisir {name} ! 😊 N'hésitez pas si vous avez d'autres questions."

    # ── Comment ça va ──
    if any(w in q for w in ['ça va', 'ca va', 'comment vas', 'comment allez']):
        return f"Très bien {name}, merci ! Je suis là pour vous aider à gérer votre boutique. Que voulez-vous savoir ?"

    # ── Qui es-tu ──
    if any(w in q for w in ['qui es', 't\'es qui', 'tu es qui', 'ton nom', 't\'appelles']):
        return "Je suis l'**assistant AfriMarket** 🤖 — votre copilote pour gérer votre boutique. Je connais toute la plateforme et j'analyse vos données en temps réel."

    # ── Aide ──
    if any(w in q for w in ['aide', 'help', 'quoi faire', 'tu peux faire', 'tu sais faire']):
        return (
            "Voici tout ce que je sais faire :\n\n"
            "📊 **Analyser** — « ventes du jour », « état du stock », « commandes en attente », « meilleurs produits », « mes clients », « factures impayées », « mon équipe », « devis »\n\n"
            "📖 **Guider** — « comment ajouter un produit », « créer un code promo », « envoyer une facture », « ajouter un employé », « transférer du stock », « configurer la TVA »\n\n"
            "⚡ **Alerter** — je surveille votre stock, vos commandes et vos factures en permanence\n\n"
            "Posez votre question !"
        )

    # ═══════════════════════════════════════════
    # DONNÉES RÉELLES
    # ═══════════════════════════════════════════

    # ── Ventes / Revenus ──
    if any(w in q for w in ['vente', 'revenu', 'chiffre', 'gagné', 'gagne', 'ca ', 'chiffre d\'affaire']):
        if any(w in q for w in ['jour', 'aujourd']):
            items = OrderItem.objects.filter(product__store=store, order__created_at__date=now.date(), order__is_paid=True)
            total = items.aggregate(t=Sum(F('price') * F('quantity')))['t'] or 0
            count = Order.objects.filter(items__store=store, created_at__date=now.date()).distinct().count()
            return f"**Ventes du jour** 📊\n\n💰 Revenu : **{total:,.0f} FCFA**\n📦 Commandes : **{count}**"
        if 'semaine' in q:
            items = OrderItem.objects.filter(product__store=store, order__created_at__gte=week_ago, order__is_paid=True)
            total = items.aggregate(t=Sum(F('price') * F('quantity')))['t'] or 0
            count = Order.objects.filter(items__store=store, created_at__gte=week_ago).distinct().count()
            return f"**Ventes cette semaine** 📊\n\n💰 Revenu : **{total:,.0f} FCFA**\n📦 Commandes : **{count}**"
        if 'mois' in q:
            items = OrderItem.objects.filter(product__store=store, order__created_at__gte=month_ago, order__is_paid=True)
            total = items.aggregate(t=Sum(F('price') * F('quantity')))['t'] or 0
            count = Order.objects.filter(items__store=store, created_at__gte=month_ago).distinct().count()
            return f"**Ventes ce mois** 📊\n\n💰 Revenu : **{total:,.0f} FCFA**\n📦 Commandes : **{count}**"
        items = OrderItem.objects.filter(product__store=store, order__is_paid=True)
        total = items.aggregate(t=Sum(F('price') * F('quantity')))['t'] or 0
        count = Order.objects.filter(items__store=store).distinct().count()
        return f"**Ventes totales** 📊\n\n💰 Revenu : **{total:,.0f} FCFA**\n📦 Commandes : **{count}**\n\nDemandez par période : « ventes du jour », « de la semaine », « du mois »."

    # ── Stock ──
    if any(w in q for w in ['stock', 'rupture', 'épuisé', 'inventaire']):
        out = Product.objects.filter(store=store, is_active=True, stock=0)
        low = Product.objects.filter(store=store, is_active=True, stock__lte=F('low_stock_threshold'), stock__gt=0)
        total = Product.objects.filter(store=store, is_active=True).count()
        msg = f"**État du stock** 📦\n\nProduits actifs : **{total}**\n"
        if out.exists():
            msg += f"🔴 Rupture : **{out.count()}** — {', '.join(p.name for p in out[:5])}\n"
        if low.exists():
            msg += f"🟡 Stock faible : **{low.count()}** — {', '.join(f'{p.name} ({p.stock})' for p in low[:5])}\n"
        if not out.exists() and not low.exists():
            msg += "✅ Tout est en stock suffisant."
        return msg

    # ── Commandes ──
    if any(w in q for w in ['commande', 'commandes']):
        if any(w in q for w in ['attente', 'pending', 'nouvelle']):
            pending = Order.objects.filter(items__store=store, status='pending').distinct()
            if pending.exists():
                lines = '\n'.join(f'• {o.order_number} — {o.buyer.display_name} — {o.total_amount:,.0f} FCFA' for o in pending[:5])
                return f"**{pending.count()} commande(s) en attente** ⏳\n\n{lines}\n\nAllez dans **Commandes** pour les confirmer."
            return "✅ Aucune commande en attente. Tout est à jour !"
        total = Order.objects.filter(items__store=store).distinct().count()
        pending = Order.objects.filter(items__store=store, status='pending').distinct().count()
        delivered = Order.objects.filter(items__store=store, status='delivered').distinct().count()
        return f"**Vos commandes** 📦\n\nTotal : **{total}**\n⏳ En attente : **{pending}**\n✅ Livrées : **{delivered}**"

    # ── Clients ──
    if any(w in q for w in ['client', 'clients']):
        total = Order.objects.filter(items__store=store).values('buyer').distinct().count()
        new_week = Order.objects.filter(items__store=store, created_at__gte=week_ago).values('buyer').distinct().count()
        top = (
            OrderItem.objects.filter(product__store=store, order__is_paid=True)
            .values('order__buyer__first_name', 'order__buyer__last_name', 'order__buyer__username')
            .annotate(total=Sum(F('price') * F('quantity')))
            .order_by('-total')[:3]
        )
        msg = f"**Vos clients** 👥\n\nTotal : **{total}**\n🆕 Cette semaine : **{new_week}**\n"
        if top:
            msg += "\n**Top 3 :**\n"
            for t in top:
                n = f"{t['order__buyer__first_name']} {t['order__buyer__last_name']}".strip() or t['order__buyer__username']
                msg += f"• {n} — {t['total']:,.0f} FCFA\n"
        return msg

    # ── Produits (guide d'abord si « comment ») ──
    if any(w in q for w in ['comment', 'ajouter', 'créer', 'creer', 'nouveau']) and any(w in q for w in ['produit', 'article']):
        return "**Ajouter un produit** 📦\n\nAllez dans **Produits** (via un entrepôt) → **« + Nouveau produit »**. Remplissez le nom, prix, stock, catégorie et ajoutez jusqu'à 4 images. Le produit est lié à l'entrepôt sélectionné."
    if any(w in q for w in ['produit', 'produits']):
        if any(w in q for w in ['meilleur', 'top', 'vendu', 'populaire']):
            top = (
                OrderItem.objects.filter(product__store=store, order__is_paid=True)
                .values('product__name')
                .annotate(qty=Sum('quantity'), revenue=Sum(F('price') * F('quantity')))
                .order_by('-qty')[:5]
            )
            if top:
                lines = '\n'.join(f"• {t['product__name']} — {t['qty']} vendus ({t['revenue']:,.0f} FCFA)" for t in top)
                return f"**Top 5 produits** 🏆\n\n{lines}"
            return "Aucune vente enregistrée pour le moment."
        total = Product.objects.filter(store=store, is_active=True).count()
        archived = Product.objects.filter(store=store, is_active=False).count()
        return f"**Vos produits** 📦\n\nActifs : **{total}**\nArchivés : **{archived}**\n\nDemandez « meilleurs produits » pour le top 5."

    # ── Factures (guide d'abord si « comment ») ──
    if any(w in q for w in ['comment', 'créer', 'creer', 'nouvelle', 'faire', 'émettre']) and 'facture' in q:
        return "**Créer une facture** 📄\n\n**Facturation** → **« + Nouvelle facture »**. Remplissez le client, les articles, la TVA si applicable. Numérotation automatique (préfixe dans Paramètres)."
    if any(w in q for w in ['facture', 'factures', 'impayé', 'impayée']):
        total = Invoice.objects.filter(store=store).count()
        paid = Invoice.objects.filter(store=store, status='paid').count()
        unpaid = Invoice.objects.filter(store=store, status='sent')
        unpaid_total = unpaid.aggregate(t=Sum('total_amount'))['t'] or 0
        msg = f"**Vos factures** 📄\n\nTotal : **{total}**\n✅ Payées : **{paid}**\n"
        if unpaid.exists():
            msg += f"🔴 Impayées : **{unpaid.count()}** — **{unpaid_total:,.0f} FCFA** à encaisser\n"
        return msg

    # ── Marketing ──
    if any(w in q for w in ['marketing', 'promo', 'campagne', 'pub', 'publicité']):
        promos = PromoCode.objects.filter(store=store, is_active=True).count()
        campaigns = Campaign.objects.filter(store=store, status='active').count()
        emails = Newsletter.objects.filter(store=store, status='sent').count()
        loyalty = LoyaltyProgram.objects.filter(store=store).count()
        fb = FacebookPost.objects.filter(store=store).count()
        wa = MessagingCampaign.objects.filter(store=store).count()
        return (
            f"**Votre marketing** 📣\n\n"
            f"🎫 Codes promo actifs : **{promos}**\n"
            f"🎯 Campagnes actives : **{campaigns}**\n"
            f"📧 Emails envoyés : **{emails}**\n"
            f"⭐ Membres fidélité : **{loyalty}**\n"
            f"📘 Posts Facebook : **{fb}**\n"
            f"💬 Campagnes WhatsApp : **{wa}**\n\n"
            f"Allez dans **Marketing** pour gérer tout ça."
        )

    # ── Employés (guide d'abord si « comment ») ──
    if any(w in q for w in ['comment', 'ajouter', 'créer', 'creer', 'recruter']) and any(w in q for w in ['employé', 'employe', 'membre']):
        return "**Ajouter un employé** 👔\n\n**Employés** → carte **Employés** → **« + Ajouter un employé »**. Créez son compte (identifiants envoyés par email), assignez un rôle et un entrepôt."
    if any(w in q for w in ['employé', 'employés', 'employe', 'employes', 'équipe', 'equipe', 'salaire', 'paie']):
        members = StoreMember.objects.filter(store=store, is_active=True)
        total_salary = members.aggregate(t=Sum('salary'))['t'] or 0
        payslips = Payslip.objects.filter(member__store=store).count()
        return (
            f"**Votre équipe** 👔\n\n"
            f"Employés actifs : **{members.count()}**\n"
            f"💰 Masse salariale : **{total_salary:,.0f} FCFA/mois**\n"
            f"📄 Bulletins créés : **{payslips}**\n\n"
            f"Gérez tout ça dans **Employés**."
        )

    # ── Devis ──
    if any(w in q for w in ['devis', 'rfq', 'offre']):
        open_rfqs = RFQ.objects.filter(status='open').exclude(quotes__store=store).count()
        my_quotes = RFQ.objects.filter(quotes__store=store).distinct().count()
        return (
            f"**Devis (RFQ)** 📋\n\n"
            f"Demandes ouvertes sans votre réponse : **{open_rfqs}**\n"
            f"Vos offres soumises : **{my_quotes}**\n\n"
            f"Allez dans **Devis RFQ** pour répondre."
        )

    # ── Entrepôts ──
    if any(w in q for w in ['entrepôt', 'entrepôts', 'entrepot', 'entrepots', 'dépôt', 'depot']):
        warehouses = Warehouse.objects.filter(store=store, is_active=True)
        transfers = StockTransfer.objects.filter(from_warehouse__store=store).count()
        msg = f"**Vos entrepôts** 🏭\n\n"
        for w in warehouses:
            msg += f"• {w.name} ({w.code}) — {w.city}\n"
        msg += f"\n🔄 Transferts effectués : **{transfers}**"
        return msg

    # ═══════════════════════════════════════════
    # GUIDE (comment faire)
    # ═══════════════════════════════════════════

    # Produits
    if any(w in q for w in ['ajouter', 'créer', 'nouveau']) and any(w in q for w in ['produit', 'article']):
        return "**Ajouter un produit** 📦\n\nAllez dans **Produits** (via un entrepôt) → **« + Nouveau produit »**. Remplissez le nom, prix, stock, catégorie et ajoutez jusqu'à 4 images. Le produit est lié à l'entrepôt sélectionné."
    if any(w in q for w in ['modifier', 'éditer', 'changer']) and 'produit' in q:
        return "**Modifier un produit** ✏️\n\nPage **Produits** → menu **⋮** → **Modifier**. Prix, description, images modifiables. Le stock se gère via « Ajuster le stock »."
    if any(w in q for w in ['archiver', 'supprimer']) and 'produit' in q:
        return "**Archiver un produit** 📁\n\nMenu **⋮** → **Archiver**. L'historique est conservé, désarchivable à tout moment."
    if any(w in q for w in ['ajuster', 'corriger']) and 'stock' in q:
        return "**Ajuster le stock** 📦\n\nMenu **⋮** → **Ajuster le stock**, ou **Inventaire** → **Ajustement**. Choisissez le type (entrée, sortie, correction) et la quantité."

    # Commandes
    if any(w in q for w in ['statut', 'changer', 'confirmer', 'expédier', 'livrer']) and 'commande' in q:
        return "**Changer le statut d'une commande** 📦\n\n**Commandes** → cliquez sur la commande → changez le statut (confirmée, expédiée, livrée, annulée). Le client reçoit une notification et un email."

    # Ventes & Factures
    if any(w in q for w in ['créer', 'nouvelle', 'faire']) and 'vente' in q:
        return "**Créer une vente directe** 💰\n\n**Ventes** → **« + Nouvelle vente »**. Sélectionnez les produits, les quantités, le client. Le stock est décrémenté automatiquement."
    if any(w in q for w in ['créer', 'nouvelle', 'faire', 'émettre']) and 'facture' in q:
        return "**Créer une facture** 📄\n\n**Facturation** → **« + Nouvelle facture »**. Remplissez le client, les articles, la TVA si applicable. Numérotation automatique (préfixe dans Paramètres)."
    if any(w in q for w in ['envoyer', 'envoyer']) and 'facture' in q:
        return "**Envoyer une facture** 📧\n\nSur la facture → **Envoyer**. Le client reçoit un email avec le PDF en pièce jointe + une notification interne."
    if 'tva' in q or 'taxe' in q:
        return "**Configurer la TVA** 🧾\n\n**Paramètres** → onglet **Facturation** → cochez **Appliquer la TVA** et définissez le taux (ex: 19,25%). Calculée automatiquement sur les factures."

    # Entrepôts
    if any(w in q for w in ['créer', 'ajouter', 'nouveau']) and any(w in q for w in ['entrepôt', 'dépôt']):
        return "**Créer un entrepôt** 🏭\n\n**Entrepôts** → **« + Nouvel entrepôt »**. Nom, code, ville. Liez des boutiques à cet entrepôt."
    if any(w in q for w in ['transfert', 'transférer', 'déplacer']) and any(w in q for w in ['stock', 'produit', 'marchandise']):
        return "**Transférer du stock** 🔄\n\n**Mouvements** → **« + Nouveau transfert »**. Entrepôt source → destination, produits, quantités. Statuts : brouillon → en transit → reçu."
    if 'retour' in q and any(w in q for w in ['marchandise', 'stock', 'produit']):
        return "**Retour de marchandise** ↩️\n\n**Mouvements** → nouveau transfert → type **Retour**. La marchandise revient à l'entrepôt d'origine."

    # Employés
    if any(w in q for w in ['ajouter', 'créer', 'recruter']) and any(w in q for w in ['employé', 'employés', 'membre']):
        return "**Ajouter un employé** 👔\n\n**Employés** → carte **Employés** → **« + Ajouter un employé »**. Créez son compte (identifiants envoyés par email), assignez un rôle et un entrepôt."
    if any(w in q for w in ['rôle', 'permission', 'droit']):
        return "**Gérer les rôles** 🛡️\n\n**Employés** → carte **Rôles**. Créez des rôles avec des permissions précises (produits, commandes, stock, etc.). Assignez un rôle à chaque employé."
    if any(w in q for w in ['paie', 'salaire', 'bulletin', 'payer']):
        return "**Gérer la paie** 💰\n\n**Employés** → carte **Paie** → **« + Nouveau bulletin »**. Choisissez l'employé, le mois, ajoutez primes et retenues. Le net à payer est calculé automatiquement."

    # Marketing
    if any(w in q for w in ['créer', 'faire', 'nouveau']) and any(w in q for w in ['code', 'promo', 'réduction', 'coupon']):
        return "**Créer un code promo** 🎫\n\n**Marketing** → **Codes Promo** → **« + Nouveau code »**. Réduction (% ou montant), validité, montant minimum d'achat. Le client l'applique dans son panier."
    if any(w in q for w in ['email', 'mail', 'newsletter']) and any(w in q for w in ['envoyer', 'campagne', 'créer']):
        return "**Campagne email** 📧\n\n**Marketing** → **Email Marketing** → **« + Nouvelle campagne »**. Rédigez le message, choisissez les produits et l'audience. Envoi immédiat ou programmé."
    if any(w in q for w in ['whatsapp', 'telegram', 'message']):
        return "**Campagne WhatsApp** 💬\n\n**Marketing** → **WhatsApp & Telegram** → **« + Nouvelle campagne »**. Les numéros de vos clients sont récupérés automatiquement. Cliquez « Envoyer » pour chaque numéro."
    if any(w in q for w in ['facebook', 'fb', 'post']):
        return "**Publier sur Facebook** 📘\n\n**Marketing** → **Facebook** → **« + Nouveau post »**. Créez le post avec produits et hashtags, prévisualisez, puis publiez. Configurez votre page dans Paramètres boutique."
    if any(w in q for w in ['fidélité', 'fidelite', 'points', 'niveau', 'récompense', 'recompense']):
        return "**Programme fidélité** ⭐\n\nLes clients gagnent **1 point par tranche de 1 000 FCFA** dépensés. Configurez les seuils et réductions dans **Marketing → Fidélité → Configurer**. Email automatique à chaque changement de niveau."
    if any(w in q for w in ['campagne']) and 'marketing' in q:
        return "**Campagne marketing** 🎯\n\n**Marketing** → **Campagnes** → **« + Nouvelle campagne »** (modale 2 étapes). Dates, produits, code promo. Visible sur la page d'accueil pendant la période."

    # Compte & Paramètres
    if any(w in q for w in ['mot de passe', 'password', 'mdp']):
        return "**Changer le mot de passe** 🔒\n\n**Paramètres** → onglet **Compte** → « Changer le mot de passe ». Entrez l'actuel puis le nouveau."
    if any(w in q for w in ['notification', 'notif', 'alerte email']):
        return "**Gérer les notifications** 🔔\n\n**Paramètres** → onglet **Notifications**. Activez/désactivez les notifications internes et les emails par type (commandes, devis, factures, paiements, stock, compte)."
    if any(w in q for w in ['boutique', 'logo', 'bannière', 'personnaliser']):
        return "**Personnaliser ma boutique** 🏪\n\n**Mes Boutiques** → **Gérer**. Nom, logo, bannière, description, horaires, réseaux sociaux."
    if any(w in q for w in ['préfixe', 'numéro', 'numérotation']) and 'facture' in q:
        return "**Personnaliser les factures** 📄\n\n**Paramètres** → **Facturation**. Raison sociale, RCCM, TVA, préfixe des numéros, logo, signature, conditions de paiement."

    # Devis
    if any(w in q for w in ['répondre', 'soumettre']) and any(w in q for w in ['devis', 'rfq', 'offre']):
        return "**Répondre à un devis** 📋\n\n**Devis RFQ** → onglet **Ouvertes** → cliquez sur la demande → soumettez votre offre (prix, délai, conditions). L'acheteur est notifié."

    # Général
    if any(w in q for w in ['commission', 'frais', 'pourcentage']):
        return "**Commission** 💰\n\nAfriMarket prélève **10%** sur chaque vente. Le reste est crédité sur votre portefeuille."
    if any(w in q for w in ['paiement', 'payer', 'mobile money', 'momo', 'orange money']):
        return "**Modes de paiement** 💳\n\nMTN Mobile Money, Orange Money, cash à la livraison, carte bancaire et virement bancaire."

    # ── Par défaut ──
    return (
        f"Hmm, je ne suis pas sûr de comprendre « {question} ». 🤔\n\n"
        f"Voici ce que je peux faire :\n\n"
        f"📊 **Analyser** — « ventes du jour », « état du stock », « commandes en attente », « meilleurs produits », « mes clients », « factures impayées », « mon équipe », « devis », « entrepôts »\n\n"
        f"📖 **Guider** — « comment ajouter un produit », « créer un code promo », « envoyer une facture », « ajouter un employé », « transférer du stock », « configurer la TVA », « changer mot de passe »\n\n"
        f"Essayez une de ces questions ou reformulez !"
    )


def get_suggestions():
    return [
        "Bonjour",
        "Ventes du jour",
        "État du stock",
        "Commandes en attente",
        "Meilleurs produits",
        "Comment ajouter un produit ?",
    ]
