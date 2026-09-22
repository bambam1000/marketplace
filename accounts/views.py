from django.shortcuts import render, redirect
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
            return redirect(request.GET.get('next', 'home'))
        messages.error(request, 'Identifiants invalides.')
    return render(request, 'accounts/login.html')

def register_view(request):
    if request.method == 'POST':
        role = request.POST.get('role', 'buyer')
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Ce nom d\'utilisateur existe déjà.')
        elif User.objects.filter(email=email).exists():
            messages.error(request, 'Cet email est déjà utilisé.')
        else:
            user = User.objects.create_user(
                username=username, email=email, password=password,
                role=role,
                first_name=request.POST.get('first_name', ''),
                last_name=request.POST.get('last_name', ''),
                phone=request.POST.get('phone', ''),
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
            messages.success(request, 'Bienvenue ! Votre compte a été créé.')
            return redirect('home')
    return render(request, 'accounts/register.html')

def logout_view(request):
    logout(request)
    return redirect('home')

@login_required
def profile_view(request):
    if request.method == 'POST':
        request.user.first_name = request.POST.get('first_name', '')
        request.user.last_name = request.POST.get('last_name', '')
        request.user.phone = request.POST.get('phone', '')
        request.user.address = request.POST.get('address', '')
        request.user.city = request.POST.get('city', '')
        request.user.save()
        messages.success(request, 'Profil mis à jour !')

    from orders.models import Order, RFQ
    from messaging.models import Message
    from catalog.models import Wishlist
    from django.db.models import Sum

    user_orders = Order.objects.filter(buyer=request.user)
    context = {
        'recent_orders': user_orders[:5],
        'orders_count': user_orders.count(),
        'pending_orders': user_orders.filter(status__in=['pending', 'confirmed', 'processing', 'shipped']).count(),
        'delivered_orders': user_orders.filter(status='delivered').count(),
        'total_spent': user_orders.filter(is_paid=True).aggregate(t=Sum('total_amount'))['t'] or 0,
        'rfqs': RFQ.objects.filter(buyer=request.user)[:5],
        'rfqs_count': RFQ.objects.filter(buyer=request.user).count(),
        'wishlist_count': Wishlist.objects.filter(user=request.user).count(),
        'unread_messages': Message.objects.filter(
            conversation__buyer=request.user, is_read=False
        ).exclude(sender=request.user).count(),
    }
    return render(request, 'accounts/profile.html', context)


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
                    send_mail(
                        'Réinitialisation de votre mot de passe — AfriMarket',
                        f'Bonjour {user.username},\n\n'
                        f'Vous avez demandé la réinitialisation de votre mot de passe.\n\n'
                        f'Cliquez sur ce lien pour créer un nouveau mot de passe :\n{reset_url}\n\n'
                        f'Ce lien est valide pendant 24 heures.\n\n'
                        f'Si vous n\'avez pas fait cette demande, ignorez ce message.\n\n'
                        f'Cordialement,\nL\'équipe AfriMarket',
                        settings.DEFAULT_FROM_EMAIL,
                        [user.email],
                        fail_silently=True,
                    )
                except:
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
                if len(password1) >= 8:
                    user.set_password(password1)
                    user.save()
                    messages.success(request, '✅ Votre mot de passe a été réinitialisé avec succès ! Vous pouvez maintenant vous connecter.')
                    return redirect('accounts:login')
                else:
                    messages.error(request, 'Le mot de passe doit contenir au moins 8 caractères.')
            else:
                messages.error(request, 'Les mots de passe ne correspondent pas.')

    return render(request, 'accounts/password_reset_confirm.html', {
        'validlink': validlink,
    })
