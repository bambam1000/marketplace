"""
Générateur de PDF pour les factures
Utilise ReportLab pour créer des PDFs professionnels
"""

from io import BytesIO
from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfgen import canvas
import os


def generate_invoice_pdf(invoice):
    """
    Génère un PDF pour une facture

    Args:
        invoice: Instance du modèle Invoice

    Returns:
        BytesIO: Buffer contenant le PDF généré
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm,
                           leftMargin=2*cm, rightMargin=2*cm)

    # Container pour les éléments du PDF
    elements = []

    # Styles
    styles = getSampleStyleSheet()

    # Style personnalisé pour le titre
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1a56db'),
        spaceAfter=30,
        alignment=TA_CENTER
    )

    # Style pour les en-têtes
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=colors.HexColor('#374151'),
        spaceAfter=12,
    )

    # Style normal
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#6b7280'),
    )

    # === EN-TÊTE ===
    # Logo de la boutique
    settings_obj = invoice.store.invoice_settings
    if settings_obj.logo and os.path.exists(settings_obj.logo.path):
        try:
            logo = Image(settings_obj.logo.path, width=4*cm, height=2*cm)
            elements.append(logo)
            elements.append(Spacer(1, 0.5*cm))
        except:
            pass

    # Titre du document
    doc_title = "FACTURE PRO FORMA" if invoice.invoice_type == 'proforma' else \
                "AVOIR" if invoice.invoice_type == 'credit_note' else "FACTURE"
    elements.append(Paragraph(doc_title, title_style))
    elements.append(Spacer(1, 0.5*cm))

    # === INFORMATIONS ENTREPRISE ET CLIENT ===
    # Tableau avec infos entreprise et client
    company_info = f"""
    <b>{settings_obj.company_name or invoice.store.name}</b><br/>
    {settings_obj.address}<br/>
    {settings_obj.city}, {settings_obj.country}<br/>
    Tél: {settings_obj.phone}<br/>
    Email: {settings_obj.email}<br/>
    """
    if settings_obj.tax_id:
        company_info += f"N° Contribuable: {settings_obj.tax_id}<br/>"
    if settings_obj.registration_number:
        company_info += f"RCCM: {settings_obj.registration_number}<br/>"

    customer_info = f"""
    <b>FACTURÉ À:</b><br/>
    {invoice.customer_name}<br/>
    {invoice.customer_address or ''}<br/>
    Tél: {invoice.customer_phone}<br/>
    Email: {invoice.customer_email}<br/>
    """

    info_table_data = [
        [Paragraph(company_info, normal_style), Paragraph(customer_info, normal_style)]
    ]

    info_table = Table(info_table_data, colWidths=[9*cm, 8*cm])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 1*cm))

    # === DÉTAILS FACTURE ===
    details_data = [
        [Paragraph("<b>N° Facture:</b>", normal_style), Paragraph(invoice.invoice_number, normal_style),
         Paragraph("<b>Date:</b>", normal_style), Paragraph(invoice.issue_date.strftime('%d/%m/%Y'), normal_style)],
    ]
    if invoice.due_date:
        details_data.append([
            Paragraph("<b>Date d'échéance:</b>", normal_style),
            Paragraph(invoice.due_date.strftime('%d/%m/%Y'), normal_style),
            Paragraph("", normal_style), Paragraph("", normal_style)
        ])
    if invoice.order:
        details_data.append([
            Paragraph("<b>N° Commande:</b>", normal_style),
            Paragraph(invoice.order.order_number, normal_style),
            Paragraph("", normal_style), Paragraph("", normal_style)
        ])

    details_table = Table(details_data, colWidths=[4*cm, 5*cm, 4*cm, 4*cm])
    details_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f9fafb')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(details_table)
    elements.append(Spacer(1, 1*cm))

    # === TABLEAU DES ARTICLES ===
    items_data = [
        [Paragraph('<b>Description</b>', normal_style),
         Paragraph('<b>Qté</b>', normal_style),
         Paragraph('<b>P.U.</b>', normal_style),
         Paragraph('<b>Total</b>', normal_style)]
    ]

    for item in invoice.items.all():
        items_data.append([
            Paragraph(item.description, normal_style),
            Paragraph(str(item.quantity), normal_style),
            Paragraph(f"{int(item.unit_price):,} F", normal_style),
            Paragraph(f"{int(item.total):,} F", normal_style)
        ])

    items_table = Table(items_data, colWidths=[9*cm, 2*cm, 3*cm, 3*cm])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a56db')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.5*cm))

    # === TOTAUX ===
    totals_data = []

    # Sous-total
    totals_data.append([
        Paragraph('<b>Sous-total:</b>', normal_style),
        Paragraph(f"{int(invoice.subtotal):,} F", normal_style)
    ])

    # Réduction
    if invoice.discount_amount > 0:
        totals_data.append([
            Paragraph('Réduction:', normal_style),
            Paragraph(f"-{int(invoice.discount_amount):,} F", normal_style)
        ])

    # Frais de livraison
    if invoice.shipping_amount > 0:
        totals_data.append([
            Paragraph('Livraison:', normal_style),
            Paragraph(f"{int(invoice.shipping_amount):,} F", normal_style)
        ])

    # TVA
    if invoice.tva_amount > 0:
        totals_data.append([
            Paragraph(f'TVA ({settings_obj.tva_rate}%):', normal_style),
            Paragraph(f"{int(invoice.tva_amount):,} F", normal_style)
        ])

    # Total
    total_style = ParagraphStyle(
        'TotalStyle',
        parent=normal_style,
        fontSize=14,
        textColor=colors.HexColor('#1a56db'),
    )
    totals_data.append([
        Paragraph('<b>TOTAL:</b>', total_style),
        Paragraph(f"<b>{int(invoice.total_amount):,} F CFA</b>", total_style)
    ])

    totals_table = Table(totals_data, colWidths=[14*cm, 3*cm])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#1a56db')),
        ('TOPPADDING', (0, -1), (-1, -1), 12),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 12),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 1*cm))

    # === NOTES ===
    if invoice.notes:
        elements.append(Paragraph('<b>Notes:</b>', heading_style))
        elements.append(Paragraph(invoice.notes, normal_style))
        elements.append(Spacer(1, 0.5*cm))

    # === CONDITIONS DE PAIEMENT ===
    if settings_obj.payment_terms:
        elements.append(Paragraph('<b>Conditions de paiement:</b>', heading_style))
        elements.append(Paragraph(settings_obj.payment_terms, normal_style))
        elements.append(Spacer(1, 0.5*cm))

    # === COORDONNÉES BANCAIRES ===
    if settings_obj.bank_details:
        elements.append(Paragraph('<b>Coordonnées bancaires:</b>', heading_style))
        elements.append(Paragraph(settings_obj.bank_details, normal_style))
        elements.append(Spacer(1, 1*cm))

    # === SIGNATURE ===
    if settings_obj.signature and os.path.exists(settings_obj.signature.path):
        try:
            signature = Image(settings_obj.signature.path, width=4*cm, height=2*cm)
            elements.append(signature)
        except:
            pass

    if settings_obj.signature_name:
        sig_text = f"<b>{settings_obj.signature_name}</b>"
        if settings_obj.signature_title:
            sig_text += f"<br/>{settings_obj.signature_title}"
        elements.append(Paragraph(sig_text, normal_style))

    elements.append(Spacer(1, 1*cm))

    # === PIED DE PAGE ===
    if settings_obj.footer_text:
        footer_style = ParagraphStyle(
            'Footer',
            parent=normal_style,
            fontSize=8,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#9ca3af'),
        )
        elements.append(Paragraph(settings_obj.footer_text, footer_style))

    # Construire le PDF
    doc.build(elements)

    buffer.seek(0)
    return buffer


def generate_delivery_note_pdf(delivery_note):
    """
    Génère un PDF pour un bon de livraison

    Args:
        delivery_note: Instance du modèle DeliveryNote

    Returns:
        BytesIO: Buffer contenant le PDF généré
    """
    # Similaire à generate_invoice_pdf mais adapté pour bon de livraison
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm,
                           leftMargin=2*cm, rightMargin=2*cm)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('Title', parent=styles['Heading1'],
                                fontSize=24, alignment=TA_CENTER, spaceAfter=30)

    elements.append(Paragraph("BON DE LIVRAISON", title_style))
    elements.append(Spacer(1, 1*cm))

    # Informations de base
    info_data = [
        ["N° Bon:", delivery_note.delivery_number],
        ["Date:", delivery_note.delivery_date.strftime('%d/%m/%Y')],
        ["Commande:", delivery_note.order.order_number],
    ]

    info_table = Table(info_data, colWidths=[6*cm, 11*cm])
    elements.append(info_table)
    elements.append(Spacer(1, 1*cm))

    # Articles
    items_data = [["Description", "Quantité"]]
    for item in delivery_note.invoice.items.all():
        items_data.append([item.description, str(item.quantity)])

    items_table = Table(items_data, colWidths=[13*cm, 4*cm])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(items_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer


def generate_payment_receipt_pdf(receipt):
    """
    Génère un PDF pour un reçu de paiement

    Args:
        receipt: Instance du modèle PaymentReceipt

    Returns:
        BytesIO: Buffer contenant le PDF généré
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm,
                           leftMargin=2*cm, rightMargin=2*cm)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('Title', parent=styles['Heading1'],
                                fontSize=24, alignment=TA_CENTER, spaceAfter=30)

    elements.append(Paragraph("REÇU DE PAIEMENT", title_style))
    elements.append(Spacer(1, 1*cm))

    # Informations
    info_data = [
        ["N° Reçu:", receipt.receipt_number],
        ["Date:", receipt.payment_date.strftime('%d/%m/%Y')],
        ["Facture:", receipt.invoice.invoice_number],
        ["Montant payé:", f"{int(receipt.amount_paid):,} F CFA"],
        ["Méthode:", receipt.payment_method],
    ]

    if receipt.transaction_reference:
        info_data.append(["Référence:", receipt.transaction_reference])

    info_table = Table(info_data, colWidths=[6*cm, 11*cm])
    info_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 12),
    ]))
    elements.append(info_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer
