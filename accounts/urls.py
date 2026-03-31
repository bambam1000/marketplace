from django.urls import path
from . import views
app_name = 'accounts'
urlpatterns = [
    path('connexion/', views.login_view, name='login'),
    path('inscription/', views.register_view, name='register'),
    path('deconnexion/', views.logout_view, name='logout'),
    path('profil/', views.profile_view, name='profile'),
    path('confidentialite/', views.privacy_view, name='privacy'),
    path('mot-de-passe-oublie/', views.password_reset_view, name='password_reset'),
    path('reinitialiser/<uidb64>/<token>/', views.password_reset_confirm_view, name='password_reset_confirm'),
]
