"""Icônes de catégories : les emojis sont remplacés par des icônes Font Awesome (« fa-… »)."""
from django.db import migrations, models

EMOJI = {
    '\U0001f4e6': 'fa-box', '\U0001f37d': 'fa-utensils', '\U0001f457': 'fa-person-dress', '⚽': 'fa-futbol',
    '\U0001f484': 'fa-spray-can-sparkles', '\U0001f4f1': 'fa-mobile-screen', '\U0001f3a7': 'fa-headphones', '☕': 'fa-mug-hot',
    '\U0001f45f': 'fa-shoe-prints', '\U0001f4aa': 'fa-dumbbell', '\U0001f454': 'fa-shirt', '\U0001f6cb': 'fa-couch',
    '\U0001f36f': 'fa-jar', '\U0001f4bb': 'fa-laptop', '\U0001f338': 'fa-spa', '\U0001f45c': 'fa-bag-shopping',
    '\U0001f9f4': 'fa-pump-soap', '\U0001f4fa': 'fa-tv', '\U0001f3c3': 'fa-person-running', '\U0001f50c': 'fa-plug',
    '\U0001f336': 'fa-pepper-hot', '\U0001f3e0': 'fa-house', '\U0001f697': 'fa-car', '\U0001f9f8': 'fa-baby',
    '\U0001f5a5': 'fa-desktop', '\U0001f527': 'fa-screwdriver-wrench',
}
WORDS = [('electro', 'fa-mobile-screen'), ('électro', 'fa-mobile-screen'), ('mode', 'fa-shirt'), ('vêtement', 'fa-shirt'),
         ('maison', 'fa-house'), ('déco', 'fa-couch'), ('beauté', 'fa-spray-can-sparkles'), ('santé', 'fa-pills'),
         ('aliment', 'fa-apple-whole'), ('sport', 'fa-futbol'), ('auto', 'fa-car'), ('moto', 'fa-motorcycle'),
         ('bébé', 'fa-baby'), ('enfant', 'fa-baby'), ('informatique', 'fa-laptop'), ('bricolage', 'fa-screwdriver-wrench')]


def convert(apps, schema_editor):
    Category = apps.get_model('catalog', 'Category')
    for c in Category.objects.all():
        icon = (c.icon or '').strip()
        if icon.startswith('fa-'):
            continue
        new = EMOJI.get(icon.replace('️', ''))
        if not new or new == 'fa-box':
            name = c.name.lower()
            new = next((ic for w, ic in WORDS if w in name), new or 'fa-tag')
        c.icon = new
        c.save(update_fields=['icon'])


class Migration(migrations.Migration):
    dependencies = [('catalog', '0008_recommandations')]
    operations = [
        migrations.AlterField('category', 'icon', models.CharField(default='fa-tag', max_length=50)),
        migrations.RunPython(convert, migrations.RunPython.noop),
    ]
