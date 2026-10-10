"""Recommandations de produits pour chaque client.

Signaux (du plus faible au plus fort) : catégorie consultée, produit consulté, recherche, favori, panier, achat.
Chaque signal perd la moitié de son poids tous les 14 jours : ce qu'on vient de chercher compte plus que ce
qu'on regardait il y a deux mois. Les visiteurs non connectés sont suivis dans leur session, sans rien en base ;
leur historique rejoint leur compte à la connexion. Un client peut couper la personnalisation et tout effacer.
"""
import re
import time
from collections import Counter
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import BrowsingSignal, Category, Product

WEIGHTS = {'category': 0.6, 'view': 1.0, 'search': 2.0, 'wishlist': 3.0, 'cart': 3.0, 'purchase': 4.0}
HALF_LIFE_DAYS = 14
KEEP_DAYS = 90
SESSION_KEY = 'reco_signals'
SESSION_MAX = 40
STOPWORDS = {'de', 'des', 'du', 'la', 'le', 'les', 'et', 'en', 'pour', 'avec', 'un', 'une', 'a', 'au', 'aux', 'the', 'and'}


# ───────────────────────── Enregistrement ─────────────────────────

def _enabled(request):
    user = getattr(request, 'user', None)
    return not (user and user.is_authenticated and not user.personalized)


def record(request, kind, product=None, category=None, query=''):
    """Note un signal. Ne fait rien si le client a coupé la personnalisation. Jamais bloquant."""
    try:
        if not _enabled(request):
            return
        query = _clean_query(query)
        if kind == 'search' and not query:
            return
        if category is None and product is not None:
            category = product.category if hasattr(product, 'category') else None
        pid, cid = getattr(product, 'pk', None), getattr(category, 'pk', None)
        if request.user.is_authenticated:
            recent = BrowsingSignal.objects.filter(user=request.user, kind=kind, product_id=pid, category_id=cid,
                                                   query=query, created_at__gte=timezone.now() - timedelta(minutes=30))
            if not recent.exists():  # pas de doublon pour un simple rechargement de page
                BrowsingSignal.objects.create(user=request.user, kind=kind, product_id=pid, category_id=cid, query=query)
        else:
            items = [s for s in request.session.get(SESSION_KEY, [])
                     if not (s[0] == kind and s[1] == pid and s[2] == cid and s[3] == query)]
            items.insert(0, [kind, pid, cid, query, int(time.time())])
            request.session[SESSION_KEY] = items[:SESSION_MAX]
    except Exception:  # noqa: BLE001 — une recommandation ne doit jamais casser une page
        pass


def merge_session(request, user):
    """À la connexion : l'historique de la session rejoint le compte."""
    items = request.session.pop(SESSION_KEY, [])
    if not items or not user.personalized:
        return
    now = timezone.now()
    BrowsingSignal.objects.bulk_create([
        BrowsingSignal(user=user, kind=k, product_id=p, category_id=c, query=q or '')
        for k, p, c, q, ts in items if now.timestamp() - ts < KEEP_DAYS * 86400
    ])


def clear(request):
    """Efface l'historique de recommandation du client (compte et session)."""
    request.session.pop(SESSION_KEY, None)
    if request.user.is_authenticated:
        BrowsingSignal.objects.filter(user=request.user).delete()


def purge_old():
    return BrowsingSignal.objects.filter(created_at__lt=timezone.now() - timedelta(days=KEEP_DAYS)).delete()[0]


def _clean_query(q):
    q = re.sub(r'\s+', ' ', (q or '').strip().lower())[:100]
    return q if len(q) >= 2 else ''


# ───────────────────────── Profil d'intérêts ─────────────────────────

class Profile:
    """Ce qui intéresse le client : catégories, recherches, produits vus / achetés / dans le panier."""

    def __init__(self):
        self.categories = Counter()      # catégorie → score
        self.queries = Counter()         # recherche → score
        self.anchors = Counter()         # produit (vu, favori, panier, acheté) → score
        self.viewed = []                 # produits consultés, du plus récent au plus ancien
        self.purchased = set()
        self.in_cart = set()

    @property
    def empty(self):
        return not (self.categories or self.queries or self.anchors)


def _decay(age_seconds):
    return 0.5 ** (age_seconds / 86400 / HALF_LIFE_DAYS)


def profile(request):
    """Profil du client courant (calculé une fois par requête)."""
    cached = getattr(request, '_reco_profile', None)
    if cached is not None:
        return cached
    prof = Profile()
    now = time.time()
    rows = []  # (kind, product_id, category_id, query, timestamp)
    user = request.user
    if user.is_authenticated:
        from cart.models import CartItem
        prof.in_cart = set(CartItem.objects.filter(user=user).values_list('product_id', flat=True))
        if user.personalized:
            since = timezone.now() - timedelta(days=KEEP_DAYS)
            rows = [(s.kind, s.product_id, s.category_id, s.query, s.created_at.timestamp())
                    for s in BrowsingSignal.objects.filter(user=user, created_at__gte=since)[:300]]
            from orders.models import OrderItem
            for pid, cid, at in (OrderItem.objects.filter(order__buyer=user, order__created_at__gte=since)
                                 .exclude(order__status__in=('cancelled', 'refunded'))
                                 .values_list('product_id', 'product__category_id', 'order__created_at')[:100]):
                rows.append(('purchase', pid, cid, '', at.timestamp()))
                prof.purchased.add(pid)
    else:
        rows = [tuple(s) for s in request.session.get(SESSION_KEY, [])]
        prof.in_cart = {int(k) for k in request.session.get('cart', {}) if str(k).isdigit()}

    parents = dict(Category.objects.filter(parent__isnull=False).values_list('pk', 'parent_id'))
    for kind, pid, cid, query, ts in rows:
        w = WEIGHTS.get(kind, 1) * _decay(max(0, now - ts))
        if cid:
            prof.categories[cid] += w
            if cid in parents:  # la catégorie mère profite un peu de l'intérêt pour ses sous-catégories
                prof.categories[parents[cid]] += w * 0.3
        if query:
            prof.queries[query] += w
        if pid:
            prof.anchors[pid] += w
            if kind == 'view' and pid not in prof.viewed:
                prof.viewed.append(pid)
    request._reco_profile = prof
    return prof


# ───────────────────────── Recommandations ─────────────────────────

def _available():
    return (Product.objects.filter(is_active=True, stock__gt=0, store__is_active=True)
            .select_related('store', 'category'))


def _terms(query):
    return [t for t in re.findall(r'\w+', query) if len(t) >= 3 and t not in STOPWORDS][:4]


def bought_together(product_ids, exclude=(), limit=6):
    """Produits présents dans les mêmes commandes que ces produits (« souvent achetés ensemble »)."""
    from orders.models import OrderItem
    product_ids = list(product_ids)
    if not product_ids:
        return []
    orders = (OrderItem.objects.filter(product_id__in=product_ids)
              .exclude(order__status__in=('cancelled', 'refunded')).values('order_id')[:500])
    ranked = (OrderItem.objects.filter(order_id__in=orders).exclude(product_id__in=set(product_ids) | set(exclude))
              .values('product_id').annotate(n=Count('order_id', distinct=True)).order_by('-n')[:limit * 3])
    ids = [r['product_id'] for r in ranked]
    found = {p.pk: p for p in _available().filter(pk__in=ids)}
    return [found[i] for i in ids if i in found][:limit]


def similar(product, limit=6):
    """Même catégorie, prix proche, mots du nom en commun ; les plus vendus d'abord."""
    price = float(product.price or 0)
    words = _terms(product.name.lower())
    qs = _available().filter(category=product.category).exclude(pk=product.pk)
    if price:
        qs = qs.filter(price__gte=price * 0.4, price__lte=price * 2.5)
    candidates = list(qs.order_by('-orders_count', '-views_count')[:40])
    if len(candidates) < limit and product.category.parent_id:  # élargir à la catégorie mère
        more = (_available().filter(category__parent_id=product.category.parent_id).exclude(pk=product.pk)
                .exclude(pk__in=[c.pk for c in candidates]).order_by('-orders_count')[:limit * 2])
        candidates += list(more)

    def score(p):
        name = p.name.lower()
        shared = sum(1 for w in words if w in name)
        closeness = 1 - min(abs(float(p.price or 0) - price) / price, 1) if price else 0
        return shared * 3 + closeness * 2 + min(p.orders_count, 200) / 100 + (p.store_id == product.store_id) * 0.5
    return sorted(candidates, key=score, reverse=True)[:limit]


def for_you(request, limit=12, exclude=()):
    """Produits recommandés au client, chacun avec la raison : [(produit, raison)].
    Sans historique : les plus demandés du moment (raison « Populaire en ce moment »)."""
    prof = profile(request)
    # Déjà dans le panier, déjà achetés, ou déjà vus (ils ont leur bande « Vus récemment »)
    skip = set(exclude) | prof.in_cart | prof.purchased | set(prof.viewed)
    scored = {}  # pid → [score, raison, produit]

    def add(products, base, reason):
        for rank, p in enumerate(products):
            if p.pk in skip:
                continue
            s = base * (1 - rank * 0.04) + min(p.orders_count, 300) / 300 + (0.3 if p.old_price else 0)
            cur = scored.get(p.pk)
            if cur is None or s > cur[0]:
                scored[p.pk] = [s if cur is None else s + cur[0] * 0.3, reason, p]
            else:
                cur[0] += s * 0.3  # plusieurs raisons : le produit remonte

    if not prof.empty:
        # 1. Recherches récentes
        for query, w in prof.queries.most_common(3):
            terms = _terms(query) or [query]
            cond = Q()
            for t in terms:
                cond &= Q(name__icontains=t) | Q(category__name__icontains=t)
            add(_available().filter(cond).order_by('-orders_count')[:12], 3 + w, f'Selon votre recherche « {query} »')
        # 2. Achetés avec ce que vous regardez / achetez
        anchors = [pid for pid, _ in prof.anchors.most_common(5)]
        if anchors:
            names = dict(Product.objects.filter(pk__in=anchors[:1]).values_list('pk', 'name'))
            label = f'Souvent acheté avec « {names[anchors[0]][:40]} »' if anchors[0] in names else 'Souvent acheté ensemble'
            add(bought_together(anchors, exclude=skip, limit=10), 3, label)
        # 3. Catégories favorites
        top = prof.categories.most_common(4)
        cat_names = dict(Category.objects.filter(pk__in=[c for c, _ in top]).values_list('pk', 'name'))
        for cid, w in top:
            qs = _available().filter(Q(category_id=cid) | Q(category__parent_id=cid)).order_by('-orders_count', '-created_at')[:10]
            add(qs, 1.5 + min(w, 5) / 2, f'Dans « {cat_names.get(cid, "vos catégories")} », que vous consultez')
        # 4. Proches des produits consultés
        for pid in prof.viewed[:2]:
            p = Product.objects.filter(pk=pid).select_related('category').first()
            if p:
                add(similar(p, limit=6), 2, f'Proche de « {p.name[:40]} »')

    # Diversité : 3 produits au plus par boutique
    out, per_store = [], Counter()
    for s, reason, p in sorted(scored.values(), key=lambda x: x[0], reverse=True):
        if per_store[p.store_id] >= 3:
            continue
        per_store[p.store_id] += 1
        out.append((p, reason))
        if len(out) >= limit:
            break
    if len(out) < limit:  # compléter avec ce qui marche en ce moment, toujours 3 par boutique au plus
        taken = {p.pk for p, _ in out} | skip
        spare = []
        for p in popular(limit * 3):
            if p.pk in taken or len(out) >= limit:
                continue
            if per_store[p.store_id] >= 3:
                spare.append(p)
                continue
            per_store[p.store_id] += 1
            out.append((p, 'Populaire en ce moment'))
        out += [(p, 'Populaire en ce moment') for p in spare][:limit - len(out)]  # catalogue trop petit
    return out


def popular(limit=12):
    """Les plus demandés des 30 derniers jours, sinon les plus vendus de tous les temps."""
    from orders.models import OrderItem
    since = timezone.now() - timedelta(days=30)
    ids = list(OrderItem.objects.filter(order__created_at__gte=since).exclude(order__status__in=('cancelled', 'refunded'))
               .values('product_id').annotate(n=Count('id')).order_by('-n').values_list('product_id', flat=True)[:limit * 2])
    found = {p.pk: p for p in _available().filter(pk__in=ids)}
    out = [found[i] for i in ids if i in found]
    if len(out) < limit:
        out += list(_available().exclude(pk__in=[p.pk for p in out]).order_by('-orders_count', '-views_count')[:limit - len(out)])
    return out[:limit]


def recently_viewed(request, limit=8, exclude=()):
    prof = profile(request)
    ids = [pid for pid in prof.viewed if pid not in set(exclude)][:limit]
    found = {p.pk: p for p in Product.objects.filter(pk__in=ids, is_active=True).select_related('store', 'category')}
    return [found[i] for i in ids if i in found]


def for_cart(request, cart_ids, limit=8):
    """Pour le panier : souvent achetés avec ces produits, complété par les recommandations du client."""
    cart_ids = list(cart_ids)
    items = [(p, 'Souvent acheté avec votre panier') for p in bought_together(cart_ids, limit=limit)]
    if len(items) < limit:
        taken = {p.pk for p, _ in items} | set(cart_ids)
        items += [(p, r) for p, r in for_you(request, limit=limit, exclude=taken) if p.pk not in taken][:limit - len(items)]
    return items
