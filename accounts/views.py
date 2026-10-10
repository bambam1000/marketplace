from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from .models import User

def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        user = authenticate(request, username=request.POST.get('username'), password=request.POST.get('password'))
        if user:
            login(request, user)
            # Retour à la page demandée, seulement si elle appartient à ce site
            from django.utils.http import url_has_allowed_host_and_scheme
            nxt = request.POST.get('next') or request.GET.get('next', '')
            if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                return redirect(nxt)
            return redirect('home')
        messages.error(request, 'Identifiants invalides.')
    return render(request, 'accounts/login.html')

def register_view(request):
    if request.method == 'POST':
        role = request.POST.get('role', 'buyer')
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        from .passwords import password_problem
        pw_error = password_problem(password, username or '', email or '')
        if not (username or '').strip() or not (email or '').strip():
            messages.error(request, "Indiquez un nom d'utilisateur et une adresse e-mail.")
        elif pw_error:
            messages.error(request, pw_error)
        elif User.objects.filter(username=username).exists():
            messages.error(request, 'Ce nom d\'utilisateur existe déjà.')
        elif User.objects.filter(email=email).exists():
            messages.error(request, 'Cet email est déjà utilisé.')
        else:
            phone = request.POST.get('phone', '').strip()
            user = User.objects.create_user(
                username=username, email=email, password=password,
                role=role,
                first_name=request.POST.get('first_name', ''),
                last_name=request.POST.get('last_name', ''),
                phone=phone,
            )
            if role == 'seller':
                from store.models import Store
                from inventory.models import Warehouse
                store = Store.objects.create(owner=user, name=request.POST.get('store_name', f'Boutique {username}'))
                # Entrepôt principal créé automatiquement à l'inscription
                Warehouse.objects.create(
                    store=store,
                    name='Entrepôt principal',
                    code=f'{store.id:03d}-MAIN',
                    city=store.city,
                    is_default=True,
                )
            login(request, user)
            # Email de bienvenue + notification interne
            from messaging.utils import notify
            if role == 'seller':
                msg = 'Votre boutique a été créée avec un entrepôt principal. Complétez votre profil et ajoutez vos premiers produits pour commencer à vendre.'
            else:
                msg = 'Découvrez les boutiques et leurs produits, commandez en toute sécurité et suivez vos commandes en temps réel.'
            notify(
                user, 'account',
                'Bienvenue sur Comptoir !',
                msg,
                url='/',
                send_email=True,
                email_subject='Bienvenue sur Comptoir !',
            )
            messages.success(request, 'Bienvenue ! Votre compte a été créé.')
            return redirect('home')
    return render(request, 'accounts/register.html')

def register_seller_view(request):
    """Inscription dédiée aux vendeurs (rôle forcé, template dédié)"""
    if request.method == 'POST':
        request.POST = request.POST.copy()
        request.POST['role'] = 'seller'
        return register_view(request)
    return render(request, 'accounts/register_seller.html')


def logout_view(request):
    logout(request)
    return redirect('home')

@login_required
def profile_view(request):
    """Espace client : à faire, dernières commandes, informations, adresse et mot de passe."""
    import re
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    from django.db.models import Sum
    from orders.models import Order, RFQ
    from .account import account_nav, profile_completion, ACTIVE_ORDER

    user = request.user
    errors, section = {}, request.POST.get('section')
    if request.method == 'POST':
        val = lambda k: (request.POST.get(k) or '').strip()  # noqa: E731
        if section == 'info':
            email = val('email')
            try:
                validate_email(email)
                if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                    errors['email'] = 'Cette adresse e-mail est déjà utilisée par un autre compte.'
            except ValidationError:
                errors['email'] = 'Adresse e-mail invalide.'
            phone = val('phone')
            if phone and not 8 <= len(re.sub(r'\D', '', phone)) <= 15:
                errors['phone'] = 'Numéro de téléphone invalide.'
            avatar = request.FILES.get('avatar')
            if avatar and (avatar.size > 3 * 1024 * 1024 or not (avatar.content_type or '').startswith('image/')):
                errors['avatar'] = 'Choisissez une image de moins de 3 Mo.'
            if not errors:
                user.first_name, user.last_name = val('first_name')[:150], val('last_name')[:150]
                user.email, user.phone = email, phone[:20]
                if avatar:
                    user.avatar = avatar
                elif request.POST.get('remove_avatar') and user.avatar:
                    user.avatar.delete(save=False)
                    user.avatar = None
                user.save()
                messages.success(request, 'Vos informations sont enregistrées.')
        elif section == 'address':
            user.address, user.city = val('address')[:500], val('city')[:100]
            user.country = val('country')[:100] or user.country
            user.save(update_fields=['address', 'city', 'country'])
            messages.success(request, 'Adresse enregistrée : elle sera proposée à votre prochaine commande.')
        elif section == 'reco':
            from catalog import recommend
            if request.POST.get('action') == 'clear':
                recommend.clear(request)
                messages.success(request, 'Votre historique de recherche et de navigation est effacé.')
            else:
                user.personalized = bool(request.POST.get('personalized'))
                user.save(update_fields=['personalized'])
                if not user.personalized:
                    recommend.clear(request)
                messages.success(request, 'Recommandations personnalisées activées.' if user.personalized
                                 else 'Recommandations personnalisées désactivées, historique effacé.')
        elif section == 'password':
            if not user.check_password(request.POST.get('current_password', '')):
                errors['current_password'] = 'Mot de passe actuel incorrect.'
            new = request.POST.get('new_password', '')
            if new != request.POST.get('confirm_password', ''):
                errors['confirm_password'] = 'Les deux mots de passe ne correspondent pas.'
            elif not errors:
                from .passwords import password_problem
                problem = password_problem(new, user.username, user.email)
                if problem:
                    errors['new_password'] = problem
                else:
                    try:
                        validate_password(new, user)
                    except ValidationError as e:
                        errors['new_password'] = ' '.join(e.messages)
            if not errors:
                user.set_password(new)
                user.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Mot de passe modifié.')
        if not errors:
            anchor = {'info': '#infos', 'address': '#adresse', 'password': '#securite', 'reco': '#recommandations'}.get(section, '')
            return redirect(reverse('accounts:profile') + anchor)
        messages.error(request, 'Certaines informations sont à corriger.')

    orders = Order.objects.filter(buyer=user)
    nav = account_nav(user, 'profile')
    completion = profile_completion(user)
    done = sum(1 for _, ok in completion if ok)
    form = {k: getattr(user, k) for k in ('first_name', 'last_name', 'email', 'phone')}
    if errors and section == 'info':
        form.update({k: request.POST.get(k, '') for k in form})
    from catalog import recommend
    from catalog.models import Category
    prof = recommend.profile(request)
    reco = {
        'categories': list(Category.objects.filter(pk__in=[c for c, _ in prof.categories.most_common(4)]).values_list('name', flat=True)),
        'queries': [q for q, _ in prof.queries.most_common(5)],
        'count': user.browsing_signals.count(),
    }
    context = {
        'reco': reco,
        'acc': nav,
        'recent_orders': orders.prefetch_related('items__product')[:3],
        'stats': {
            'orders': orders.count(),
            'active': orders.filter(status__in=ACTIVE_ORDER).count(),
            'delivered': orders.filter(status='delivered').count(),
            'paid': orders.filter(is_paid=True).exclude(status__in=('cancelled', 'refunded')).aggregate(t=Sum('total_amount'))['t'] or 0,
        },
        'to_pay': orders.filter(status='pending', is_paid=False).exclude(payment_method='cash')[:3],
        'rfqs_count': RFQ.objects.filter(buyer=user).count(),
        'completion': completion, 'completion_pct': done * 100 // len(completion), 'completion_done': done == len(completion),
        'form': form, 'errors': errors, 'section': section,
    }
    return render(request, 'accounts/profile.html', context, status=400 if errors else 200)


def privacy_view(request):
    """Page de politique de confidentialité"""
    return render(request, 'accounts/privacy.html', {'current_date': timezone.now()})


def password_reset_view(request):
    """Page de demande de réinitialisation du mot de passe"""
    if request.method == 'POST':
        username_or_email = request.POST.get('username_or_email')

        # Try to find user by username or email
        user = None
        if '@' in username_or_email:
            try:
                user = User.objects.get(email=username_or_email)
            except User.DoesNotExist:
                pass
        else:
            try:
                user = User.objects.get(username=username_or_email)
            except User.DoesNotExist:
                pass

        if user:
            # Generate reset token
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))

            # Create reset URL
            reset_url = request.build_absolute_uri(
                f'/compte/reinitialiser/{uid}/{token}/'
            )

            # Send email (if email is configured)
            if user.email:
                try:
                    from django.core.mail import EmailMessage
                    html = f'''<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:#fff;">
    <div style="background:#ff6a00;padding:24px;text-align:center;">
        <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;"><tr><td style="padding-right:10px;vertical-align:middle;"><img src="{settings.SITE_URL.rstrip("/")}/static/images/brand/mark-inverse-96.png" width="40" height="40" alt="" style="display:block;border:0;border-radius:10px;"></td><td style="vertical-align:middle;color:#fff;font-size:24px;font-weight:800;letter-spacing:-.5px;font-family:Arial,sans-serif;">Comptoir</td></tr></table>
    </div>
    <div style="padding:32px 28px;color:#333;font-size:14px;line-height:1.7;">
        <p>Bonjour {user.first_name or user.username},</p>
        <p>Vous avez demandé la réinitialisation de votre mot de passe.</p>
        <div style="text-align:center;margin:24px 0;">
            <a href="{reset_url}" style="display:inline-block;background:#ff6a00;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;">Créer un nouveau mot de passe</a>
        </div>
        <p style="font-size:12px;color:#98a2b3;">Ce lien est valide pendant 24 heures. Si vous n'avez pas fait cette demande, ignorez ce message.</p>
    </div>
    <div style="background:#f9fafb;padding:18px;text-align:center;font-size:11px;color:#98a2b3;">
        Comptoir
    </div>
</div>
</body></html>'''
                    email = EmailMessage(
                        'Réinitialisation de votre mot de passe — Comptoir',
                        html,
                        settings.DEFAULT_FROM_EMAIL,
                        [user.email],
                    )
                    email.content_subtype = 'html'
                    email.send(fail_silently=True)
                except Exception:
                    pass

            messages.success(request,
                f'Un lien de réinitialisation a été envoyé. '
                f'Si vous ne recevez pas d\'email, voici votre lien : {reset_url}')
        else:
            messages.error(request, 'Aucun compte ne correspond à cet identifiant.')

    return render(request, 'accounts/password_reset.html')


def password_reset_confirm_view(request, uidb64, token):
    """Confirmation de réinitialisation du mot de passe"""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    validlink = False
    if user is not None and default_token_generator.check_token(user, token):
        validlink = True

        if request.method == 'POST':
            password1 = request.POST.get('new_password1')
            password2 = request.POST.get('new_password2')

            if password1 and password1 == password2:
                from .passwords import password_problem
                pw_error = password_problem(password1, user.username, user.email)
                if pw_error is None:
                    user.set_password(password1)
                    user.save()
                    messages.success(request, 'Votre mot de passe a été réinitialisé. Vous pouvez maintenant vous connecter.')
                    return redirect('accounts:login')
                else:
                    messages.error(request, pw_error)
            else:
                messages.error(request, 'Les mots de passe ne correspondent pas.')

    return render(request, 'accounts/password_reset_confirm.html', {
        'validlink': validlink,
    })
