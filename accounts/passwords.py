"""Règle unique pour tout mot de passe choisi sur Comptoir (inscription, réinitialisation, profil, employés)."""

MIN_LENGTH = 8
RULE = '8 caractères minimum, pas uniquement des chiffres, et différent de votre identifiant.'
COMMON = {
    'password', 'password1', 'motdepasse', 'azertyui', 'azerty123', 'qwertyui', 'qwerty123',
    'abcdefgh', 'abcd1234', 'iloveyou', 'comptoir', 'comptoir1', 'bonjour1', 'football',
}


def password_problem(password, username='', email=''):
    """Message d'erreur si le mot de passe ne respecte pas la règle, sinon None."""
    password = password or ''
    low = password.lower()
    if len(password) < MIN_LENGTH:
        return f'Le mot de passe doit contenir au moins {MIN_LENGTH} caractères.'
    if password.isdigit():
        return 'Le mot de passe ne doit pas contenir uniquement des chiffres.'
    if username and low == username.lower():
        return 'Le mot de passe doit être différent de votre identifiant.'
    if email and low in (email.lower(), email.split('@')[0].lower()):
        return 'Le mot de passe doit être différent de votre adresse e-mail.'
    if low in COMMON or len(set(password)) <= 2:
        return 'Ce mot de passe est trop facile à deviner.'
    return None
