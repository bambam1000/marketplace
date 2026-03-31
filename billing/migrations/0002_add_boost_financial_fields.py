# Generated migration for ProductBoost financial tracking

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
    ]

    operations = [
        # Supprimer Instagram des choix
        migrations.AlterField(
            model_name='productboost',
            name='platform',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('internal', 'AfriMarket (page d\'accueil)'),
                    ('facebook', 'Facebook Ads'),
                    ('whatsapp', 'WhatsApp Business'),
                ]
            ),
        ),
        # Ajouter le statut "failed"
        migrations.AlterField(
            model_name='productboost',
            name='status',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('pending', 'En attente'),
                    ('active', 'Actif'),
                    ('completed', 'Terminé'),
                    ('cancelled', 'Annulé'),
                    ('failed', 'Échoué'),
                ],
                default='pending'
            ),
        ),
        # Budget payé par le client
        migrations.AddField(
            model_name='productboost',
            name='client_budget',
            field=models.DecimalField(
                max_digits=10,
                decimal_places=0,
                default=0,
                help_text='Montant payé par le client'
            ),
        ),
        # Budget effectif pour la plateforme (après commission)
        migrations.AddField(
            model_name='productboost',
            name='platform_budget',
            field=models.DecimalField(
                max_digits=10,
                decimal_places=0,
                default=0,
                help_text='Montant effectif pour Facebook/WhatsApp'
            ),
        ),
        # Commission d'AfriMarket
        migrations.AddField(
            model_name='productboost',
            name='commission',
            field=models.DecimalField(
                max_digits=10,
                decimal_places=0,
                default=0,
                help_text='Commission AfriMarket'
            ),
        ),
        # ID de campagne externe (Facebook, WhatsApp)
        migrations.AddField(
            model_name='productboost',
            name='external_campaign_id',
            field=models.CharField(
                max_length=100,
                blank=True,
                null=True,
                help_text='ID de la campagne Facebook/WhatsApp'
            ),
        ),
        # Données de ciblage (JSON)
        migrations.AddField(
            model_name='productboost',
            name='targeting_data',
            field=models.JSONField(
                blank=True,
                null=True,
                help_text='Données de ciblage (ville, âge, etc.)'
            ),
        ),
        # Message d'erreur si échec
        migrations.AddField(
            model_name='productboost',
            name='error_message',
            field=models.TextField(
                blank=True,
                null=True,
                help_text='Message d\'erreur en cas d\'échec'
            ),
        ),
    ]
