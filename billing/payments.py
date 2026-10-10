"""Moyens de paiement des boutiques.

Chaque vendeur choisit dans ses paramètres ce qu'il accepte (PaymentConfig).
La page de commande ne propose que les moyens acceptés par toutes les boutiques du panier.
"""
import re
from collections import OrderedDict

from .models import PaymentConfig

# Code = valeur enregistrée dans Order.payment_method
METHODS = OrderedDict([
    ('momo', {'label': 'MTN Mobile Money', 'short': 'MTN MoMo', 'icon': 'fa-mobile-screen-button',
              'hint': 'Après validation, envoyez le montant au numéro MoMo de la boutique.'}),
    ('om', {'label': 'Orange Money', 'short': 'Orange Money', 'icon': 'fa-mobile-screen',
            'hint': 'Après validation, envoyez le montant au numéro Orange Money de la boutique.'}),
    ('transfer', {'label': 'Virement bancaire', 'short': 'Virement', 'icon': 'fa-building-columns',
                  'hint': 'Après validation, faites le virement sur le compte de la boutique.'}),
    ('cash', {'label': 'Paiement à la livraison', 'short': 'À la livraison', 'icon': 'fa-money-bill-wave',
              'hint': 'Vous payez en espèces au moment de recevoir votre colis.'}),
])


def config_methods(cfg):
    """{code: détails} des moyens réellement utilisables d'une configuration."""
    if cfg is None:
        return {}
    out = {}
    if cfg.momo_enabled and cfg.momo_number.strip():
        out['momo'] = {'number': cfg.momo_number, 'name': cfg.momo_name}
    if cfg.om_enabled and cfg.om_number.strip():
        out['om'] = {'number': cfg.om_number, 'name': cfg.om_name}
    if cfg.bank_enabled and cfg.bank_account_number.strip():
        out['transfer'] = {'bank': cfg.bank_name, 'number': cfg.bank_account_number, 'name': cfg.bank_account_name}
    if cfg.cash_enabled:
        out['cash'] = {}
    return out


def store_methods(store):
    return config_methods(PaymentConfig.objects.filter(user_id=store.owner_id).first())


def checkout_options(stores):
    """Moyens proposables pour un panier.

    Retourne {'methods': [...], 'missing': [boutiques sans aucun moyen], 'by_store': [(boutique, [libellés])]}.
    Chaque élément de methods : {code, label, short, icon, hint, stores: [(boutique, détails)]}.
    """
    per = OrderedDict((s, store_methods(s)) for s in stores)
    common = [c for c in METHODS if per and all(c in m for m in per.values())]
    return {
        'methods': [{'code': c, **METHODS[c], 'stores': [(s, m[c]) for s, m in per.items()]} for c in common],
        'missing': [s for s, m in per.items() if not m],
        'by_store': [(s, [METHODS[c]['short'] for c in METHODS if c in m]) for s, m in per.items()],
    }


def order_instructions(order):
    """Pour une commande : à qui payer, combien, et comment. [(boutique, montant des articles, détails)]"""
    from collections import defaultdict
    amounts, stores = defaultdict(int), {}
    for it in order.items.select_related('store'):
        if it.store_id:
            stores[it.store_id] = it.store
            amounts[it.store_id] += int(it.price * it.quantity)
    return [(s, amounts[sid], store_methods(s).get(order.payment_method)) for sid, s in stores.items()]


PHONE_RE = re.compile(r'^\+?[\d\s.\-()]{8,20}$')


def save_config(cfg, post):
    """Enregistre les moyens de paiement depuis un formulaire. Retourne la liste des erreurs (rien n'est enregistré s'il y en a)."""
    val = lambda k: (post.get(k) or '').strip()  # noqa: E731
    errors = []
    momo, om, bank, cash = ('momo_enabled' in post, 'om_enabled' in post, 'bank_enabled' in post, 'cash_enabled' in post)
    if momo and not PHONE_RE.match(val('momo_number')):
        errors.append('MTN MoMo : indiquez un numéro valide.')
    if om and not PHONE_RE.match(val('om_number')):
        errors.append('Orange Money : indiquez un numéro valide.')
    if bank and not (val('bank_name') and val('bank_account_number')):
        errors.append('Virement : indiquez la banque et le numéro de compte.')
    if errors:
        return errors
    cfg.momo_enabled, cfg.momo_number, cfg.momo_name = momo, val('momo_number'), val('momo_name')
    cfg.om_enabled, cfg.om_number, cfg.om_name = om, val('om_number'), val('om_name')
    cfg.bank_enabled, cfg.bank_name = bank, val('bank_name')
    cfg.bank_account_number, cfg.bank_account_name = val('bank_account_number'), val('bank_account_name')
    cfg.cash_enabled = cash
    cfg.save()
    return []
