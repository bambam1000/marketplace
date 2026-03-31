"""
Utilitaires pour la génération de PDF de tickets et factures
"""
from io import BytesIO
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.conf import settings


def generate_receipt_pdf(sale):
    """
    Génère un PDF de ticket de caisse
    Pour une vraie implémentation, installer: pip install weasyprint
    """
    try:
        from weasyprint import HTML, CSS
        from weasyprint.text.fonts import FontConfiguration

        # Rendre le template HTML
        html_string = render_to_string('pos/receipt.html', {
            'sale': sale,
            'store': sale.store,
        })

        # Configuration
        font_config = FontConfiguration()

        # CSS pour le PDF
        css = CSS(string='''
            @page {
                size: 80mm auto;
                margin: 0;
            }
            body {
                margin: 0;
                padding: 5mm;
            }
        ''', font_config=font_config)

        # Générer le PDF
        html = HTML(string=html_string)
        pdf_file = html.write_pdf(stylesheets=[css], font_config=font_config)

        # Retourner la réponse HTTP
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="ticket_{sale.sale_number}.pdf"'

        return response

    except ImportError:
        # Si weasyprint n'est pas installé, retourner le HTML
        return HttpResponse(
            render_to_string('pos/receipt.html', {
                'sale': sale,
                'store': sale.store,
            }),
            content_type='text/html'
        )


def generate_invoice_pdf(invoice):
    """
    Génère un PDF de facture professionnelle
    """
    try:
        from weasyprint import HTML, CSS
        from weasyprint.text.fonts import FontConfiguration

        html_string = render_to_string('invoicing/invoice_pdf_template.html', {
            'invoice': invoice,
            'settings': invoice.store.invoice_settings,
        })

        font_config = FontConfiguration()

        css = CSS(string='''
            @page {
                size: A4;
                margin: 2cm;
            }
            body {
                font-family: Arial, sans-serif;
                font-size: 10pt;
            }
        ''', font_config=font_config)

        html = HTML(string=html_string)
        pdf_file = html.write_pdf(stylesheets=[css], font_config=font_config)

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="facture_{invoice.invoice_number}.pdf"'

        return response

    except ImportError:
        return HttpResponse(
            "Veuillez installer weasyprint: pip install weasyprint",
            content_type='text/plain'
        )
