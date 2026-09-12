from django import template

register = template.Library()

EMOJI_TO_FA = {
    '🎧': 'fa-solid fa-headphones',
    '☕': 'fa-solid fa-mug-hot',
    '👟': 'fa-solid fa-shoe-prints',
    '🍽️': 'fa-solid fa-utensils',
    '🍽': 'fa-solid fa-utensils',
    '👗': 'fa-solid fa-shirt',
    '💪': 'fa-solid fa-dumbbell',
    '⚽': 'fa-solid fa-futbol',
    '👔': 'fa-solid fa-user-tie',
    '💄': 'fa-solid fa-wand-magic-sparkles',
    '🛋️': 'fa-solid fa-couch',
    '🛋': 'fa-solid fa-couch',
    '🍯': 'fa-solid fa-jar',
    '💻': 'fa-solid fa-laptop',
    '🌸': 'fa-solid fa-spray-can-sparkles',
    '👜': 'fa-solid fa-bag-shopping',
    '📱': 'fa-solid fa-mobile-screen',
    '🧴': 'fa-solid fa-pump-soap',
    '📺': 'fa-solid fa-tv',
    '🏃': 'fa-solid fa-person-running',
    '🔌': 'fa-solid fa-plug',
    '🌶️': 'fa-solid fa-pepper-hot',
    '🌶': 'fa-solid fa-pepper-hot',
    '🏠': 'fa-solid fa-house',
    '🚗': 'fa-solid fa-car',
    '🧸': 'fa-solid fa-baby',
    '🖥️': 'fa-solid fa-desktop',
    '🖥': 'fa-solid fa-desktop',
    '🔧': 'fa-solid fa-screwdriver-wrench',
    '📦': 'fa-solid fa-box',
    '🛍️': 'fa-solid fa-bag-shopping',
    '🛍': 'fa-solid fa-bag-shopping',
    '🛒': 'fa-solid fa-cart-shopping',
    '🏪': 'fa-solid fa-store',
    '⚡': 'fa-solid fa-bolt',
    '⭐': 'fa-solid fa-star',
    '🏆': 'fa-solid fa-trophy',
    '🆕': 'fa-solid fa-sparkles',
    '📧': 'fa-regular fa-envelope',
    '📋': 'fa-solid fa-clipboard-list',
    '🛡️': 'fa-solid fa-shield-halved',
    '🛡': 'fa-solid fa-shield-halved',
    '💳': 'fa-solid fa-credit-card',
    '💬': 'fa-regular fa-comments',
    '✅': 'fa-solid fa-circle-check',
    '✓': 'fa-solid fa-check',
}


@register.filter(name='to_fa')
def to_fa(value):
    if not value:
        return 'fa-solid fa-tag'
    s = str(value).strip()
    if s in EMOJI_TO_FA:
        return EMOJI_TO_FA[s]
    return EMOJI_TO_FA.get(s.rstrip('\ufe0f'), 'fa-solid fa-tag')
