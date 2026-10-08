"""Numérotation séquentielle des références (TRF-00012, PAY-00003, SALE-20261007-00001...).

Le numéro suivant part du plus grand numéro existant pour ce préfixe, et non du nombre
de lignes : une suppression ne crée donc pas de doublon. En cas de création simultanée,
la contrainte d'unicité de la base est rattrapée et un nouveau numéro est tiré.
"""
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Cast, Substr

MAX_ATTEMPTS = 5


def next_reference(model, field, prefix, width):
    """Plus grand numéro existant pour `prefix` + 1, formaté sur `width` chiffres."""
    last = (
        model.objects.filter(**{f'{field}__startswith': prefix})
        .annotate(_num=Cast(Substr(field, len(prefix) + 1), models.IntegerField()))
        .aggregate(m=models.Max('_num'))['m']
    )
    return f"{prefix}{(last or 0) + 1:0{width}d}"


def save_with_reference(instance, field, prefix, width, save):
    """Attribue une référence libre à `instance.<field>` puis appelle `save()` (le save parent).

    Ne réessaie que si l'erreur d'unicité vient bien de la référence ; toute autre
    contrainte violée (ex. fiche de paie en double pour le même mois) remonte telle quelle.
    """
    model = type(instance)
    for attempt in range(MAX_ATTEMPTS):
        value = next_reference(model, field, prefix, width)
        setattr(instance, field, value)
        try:
            with transaction.atomic():
                return save()
        except IntegrityError:
            taken = model.objects.filter(**{field: value}).exists()
            if not taken or attempt == MAX_ATTEMPTS - 1:
                raise
