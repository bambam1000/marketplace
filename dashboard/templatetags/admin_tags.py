from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def page_url(context, number):
    """Lien vers une autre page d'une liste, en gardant les filtres en cours."""
    params = context['request'].GET.copy()
    params['page'] = number
    return '?' + params.urlencode()


@register.filter
def evo_class(value):
    return 'up' if value is not None and value >= 0 else 'down'
