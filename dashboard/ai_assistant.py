"""Assistant IA du dashboard, propulsé par Claude (API Anthropic) avec des outils en lecture seule.

Boucle d'outils manuelle (API stable, pas de beta pour la boucle) :
  question -> Claude choisit des outils -> on les exécute (limités à la boutique et aux permissions) ->
  Claude rédige la réponse. Au plus ASSISTANT_MAX_TOOL_ROUNDS allers-retours par question.

Règles respectées pour l'API :
- l'historique (`api_messages`) est uniquement complété, jamais réécrit : les blocs de réflexion renvoyés
  par le modèle restent valides d'un tour à l'autre ;
- le prompt système est stable (rien de variable dedans) pour que le cache de prompt fonctionne ;
  la date du jour est donnée dans le message de l'utilisateur ;
- `stop_reason` est vérifié avant de lire la réponse (refusal, max_tokens, pause_turn, tool_use) ;
- en cas de refus des garde-fous, l'API rejoue la requête sur un autre modèle (fallbacks="default").
"""
import html
import json
import logging
import re

from django.conf import settings
from django.utils import timezone
from django.utils.safestring import mark_safe

from . import ai_tools

logger = logging.getLogger(__name__)
FALLBACK_BETA = 'server-side-fallback-2026-07-01'

SYSTEM_PROMPT = """Tu es l'assistant IA de Comptoir, une place de marché en ligne. Tu aides le vendeur de la boutique « {store} » à piloter son activité : ventes, stock, commandes, clients, factures, comptabilité d'entrepôt, marketing et WhatsApp.

Comment travailler :
- Pour tout chiffre, appelle les outils : ils lisent les vraies données de la boutique. N'invente jamais un montant, une quantité ou un nom. Si un outil renvoie une erreur ou ne couvre pas la question, dis-le simplement.
- Choisis toi-même les périodes adaptées (la date du jour est indiquée dans chaque question) et précise la période dans la réponse. Pour comparer deux périodes, appelle l'outil pour chacune.
- Plusieurs outils indépendants peuvent être appelés en même temps.
- Les montants sont en FCFA. Écris-les avec des espaces comme séparateurs de milliers (ex. 1 250 000 FCFA).
- Réponds en français, de façon concise et concrète : d'abord la réponse, puis 2 à 4 recommandations actionnables quand c'est utile, en citant la page du dashboard où agir.
- Mise en forme : Markdown simple (gras, listes, tableaux courts). Pas d'emoji.
- Tu n'as accès qu'en lecture : tu ne peux rien modifier. Pour une action, explique où la faire dans le dashboard.
- Une permission refusée par un outil signifie que l'utilisateur n'a pas ce droit : dis-le sans insister.

Repères de la plateforme (pour les questions « comment faire ») :
- Produits : Produits, bouton « Ajouter un produit » ; le stock se modifie par « Ajuster le stock » (traçabilité) ; le prix d'achat sert au calcul de la marge.
- Commandes : Commandes, ouvrir une commande pour changer son statut (le client est prévenu par email et WhatsApp).
- Ventes directes au comptoir : Ventes, « Nouvelle vente ». Caisse : POS (le stock sort de l'entrepôt de la session).
- Stock et entrepôts : Entrepôts (stock, réception, transferts, comptabilité, dépenses), Inventaire (mouvements).
- Factures : Facturation, « Créer une facture » ; la TVA se règle dans Paramètres, onglet Facturation.
- Marketing : codes promo, campagnes, fidélité, emails, WhatsApp/Telegram, Facebook.
- WhatsApp : Dashboard, WhatsApp (connexion par QR code, conversations, désinscription STOP).
- Employés : Employés (rôles et permissions, paie).
- La plateforme prélève une commission de {commission} % sur les ventes en ligne, pas sur les ventes directes ni la caisse POS."""

TOOL_RESULT_HINT = 'Résultat de l’outil (données réelles de la boutique, en lecture seule).'


def is_configured():
    return bool(settings.ASSISTANT_AI_ENABLED)


def _client():
    import anthropic
    # Clé : ANTHROPIC_API_KEY (ou un profil `ant auth login`) ; jamais exposée au navigateur.
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None,
                               timeout=settings.ASSISTANT_TIMEOUT, max_retries=2)


def _system(store):
    return SYSTEM_PROMPT.format(store=store.name if store else 'Comptoir',
                                commission=round(settings.SALES_COMMISSION_RATE * 100))


def _user_turn(user, question):
    today = timezone.localdate()
    days = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
    return (f"[Contexte : nous sommes le {days[today.weekday()]} {today.isoformat()} ; "
            f"utilisateur : {user.display_name}]\n\n{question}")


def _blocks(content):
    """Blocs de la réponse, prêts à être renvoyés tels quels à l'API au tour suivant."""
    return [b.model_dump(mode='json', exclude_none=True) for b in content]


def _text_of(content):
    return '\n\n'.join(b.text for b in content if getattr(b, 'type', '') == 'text' and b.text).strip()


def ask(conversation, question, user, client=None):
    """Pose une question dans une conversation. Retourne {'text', 'tools', 'error'} et enregistre la conversation."""
    import anthropic
    question = (question or '').strip()[:4000]
    if not question:
        return {'text': '', 'tools': [], 'error': 'Posez une question.'}
    ctx = ai_tools.ToolContext(user, conversation.store)
    client = client or _client()
    history = list(conversation.api_messages)
    history.append({'role': 'user', 'content': _user_turn(user, question)})
    tools_used, answer, error = [], '', None
    usage_in = usage_out = 0

    try:
        for _ in range(settings.ASSISTANT_MAX_TOOL_ROUNDS):
            response = client.beta.messages.create(
                model=settings.ASSISTANT_MODEL,
                max_tokens=16000,
                system=_system(conversation.store),
                tools=ai_tools.TOOLS,
                messages=history,
                output_config={'effort': settings.ASSISTANT_EFFORT},
                cache_control={'type': 'ephemeral'},   # met en cache outils + système + historique
                betas=[FALLBACK_BETA],
                fallbacks='default',                   # refus d'un garde-fou : rejoué sur le modèle recommandé
            )
            usage_in += (response.usage.input_tokens or 0) + (getattr(response.usage, 'cache_read_input_tokens', 0) or 0)
            usage_out += response.usage.output_tokens or 0

            if response.stop_reason == 'refusal':
                # Rien n'est ajouté à l'historique : la question refusée est retirée, la conversation reste valide
                history.pop()
                answer = ("Je ne peux pas répondre à cette demande. Reformulez-la en lien avec la gestion "
                          "de votre boutique (ventes, stock, commandes, clients…).")
                break

            history.append({'role': 'assistant', 'content': _blocks(response.content)})

            if response.stop_reason == 'tool_use':
                results = []
                for block in response.content:
                    if getattr(block, 'type', '') != 'tool_use':
                        continue
                    text, is_error = ai_tools.run_tool(ctx, block.name, block.input)
                    label = ai_tools.TOOL_LABELS.get(block.name, block.name)
                    if label not in tools_used:
                        tools_used.append(label)
                    results.append({'type': 'tool_result', 'tool_use_id': block.id, 'content': text, 'is_error': is_error})
                # Tous les résultats dans un seul message (appels parallèles)
                history.append({'role': 'user', 'content': results})
                continue
            if response.stop_reason == 'pause_turn':
                continue  # tour mis en pause : on relance avec l'historique tel quel

            answer = _text_of(response.content)
            if response.stop_reason == 'max_tokens':
                answer += '\n\n*(Réponse interrompue car trop longue : demandez une partie précise.)*'
            break
        else:
            answer = ("J'ai consulté beaucoup de données sans terminer l'analyse. "
                      "Posez une question plus précise (une période, un produit, un entrepôt).")
    except anthropic.AuthenticationError:
        error = "Clé API Anthropic invalide ou absente (ANTHROPIC_API_KEY)."
    except anthropic.PermissionDeniedError:
        error = "La clé API n'a pas accès à ce modèle."
    except anthropic.RateLimitError:
        error = "Trop de demandes en même temps. Réessayez dans un instant."
    except anthropic.BadRequestError as exc:
        logger.warning('Assistant IA : requête refusée par l\'API : %s', exc)
        error = "La demande n'a pas pu être traitée. Essayez de démarrer une nouvelle conversation."
    except anthropic.APIStatusError as exc:
        logger.warning('Assistant IA : erreur API %s', exc.status_code)
        error = "Le service d'IA est momentanément indisponible. Réessayez plus tard."
    except anthropic.APIConnectionError:
        error = "Impossible de joindre le service d'IA (connexion Internet du serveur ?)."

    if error:
        return {'text': '', 'tools': tools_used, 'error': error}

    now = timezone.now().isoformat()
    conversation.api_messages = history
    conversation.transcript = list(conversation.transcript) + [
        {'role': 'user', 'text': question, 'at': now},
        {'role': 'assistant', 'text': answer, 'tools': tools_used, 'at': now},
    ]
    if not conversation.title:
        conversation.title = question[:80]
    conversation.input_tokens += usage_in
    conversation.output_tokens += usage_out
    conversation.save()
    return {'text': answer, 'tools': tools_used, 'error': None}


# ───────────────────────── Affichage Markdown (sûr) ─────────────────────────
_INLINE = [
    (re.compile(r'`([^`]+)`'), r'<code>\1</code>'),
    (re.compile(r'\*\*(.+?)\*\*'), r'<strong>\1</strong>'),
    (re.compile(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])'), r'<em>\1</em>'),
]


def _inline(text):
    out = html.escape(text, quote=False)  # tout est échappé d'abord : aucune balise du modèle n'est interprétée
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def render_markdown(text):
    """Markdown simple -> HTML sûr : titres, listes, tableaux, gras, italique, code."""
    lines = (text or '').replace('\r\n', '\n').split('\n')
    out, i = [], 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c):
                    rows.append(cells)
                i += 1
            if rows:
                head, body = rows[0], rows[1:]
                out.append('<div class="md-table"><table><thead><tr>' + ''.join(f'<th>{_inline(c)}</th>' for c in head)
                           + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{_inline(c)}</td>' for c in r) + '</tr>' for r in body)
                           + '</tbody></table></div>')
            continue
        heading = re.match(r'(#{1,4})\s+(.*)', stripped)
        if heading:
            level = min(len(heading.group(1)) + 2, 6)
            out.append(f'<h{level}>{_inline(heading.group(2))}</h{level}>')
            i += 1
            continue
        if re.match(r'([-*•])\s+', stripped) or re.match(r'\d+[.)]\s+', stripped):
            ordered = bool(re.match(r'\d+[.)]\s+', stripped))
            tag = 'ol' if ordered else 'ul'
            items = []
            while i < len(lines):
                s = lines[i].strip()
                m = re.match(r'\d+[.)]\s+(.*)', s) if ordered else re.match(r'[-*•]\s+(.*)', s)
                if not m:
                    break
                items.append(f'<li>{_inline(m.group(1))}</li>')
                i += 1
            out.append(f'<{tag}>' + ''.join(items) + f'</{tag}>')
            continue
        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r'(\||#{1,4}\s|[-*•]\s|\d+[.)]\s)', lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append('<p>' + '<br>'.join(_inline(p) for p in para) + '</p>')
    return mark_safe(''.join(out))
