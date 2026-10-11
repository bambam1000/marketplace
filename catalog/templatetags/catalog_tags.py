from django import template
from django.utils.html import format_html

register = template.Library()


@register.filter
def cat_icon(icon):
    """Icône d'une catégorie : une icône Font Awesome (« fa-shirt »), sinon le texte enregistré (anciennes catégories)."""
    icon = (icon or '').strip()
    if icon.startswith('fa-'):
        return format_html('<i class="fa-solid {}" aria-hidden="true"></i>', icon)
    return icon or format_html('<i class="fa-solid fa-tag" aria-hidden="true"></i>')
