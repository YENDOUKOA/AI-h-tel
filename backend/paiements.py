import os
from datetime import datetime, timezone
from pathlib import Path

import stripe
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request

from database import get_connection


# Charge le fichier .env situé dans le dossier backend.
load_dotenv(Path(__file__).with_name(".env"))

router = APIRouter(
    prefix="/paiements",
    tags=["Paiements"],
)


@router.post("/reservations/{reservation_id}/lien-test")
def creer_lien_test(reservation_id: int):
    """
    Crée ou retrouve une session Stripe de test
    pour le premier paiement de la réservation.
    """

    cle = os.getenv("STRIPE_SECRET_KEY", "").strip()

    frontend_url = os.getenv(
        "FRONTEND_URL",
        "http://localhost:5173",
    ).strip().rstrip("/")

    # Cette route accepte uniquement une clé de test.
    if not cle.startswith("sk_test_"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Configure une clé secrète Stripe de test "
                "dans le fichier .env."
            ),
        )

    # Lecture des informations de la réservation.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    prix_total,
                    montant_a_payer,
                    statut,
                    statut_paiement,
                    stripe_session_id
                FROM reservations
                WHERE id = %s;
                """,
                (reservation_id,),
            )

            reservation = cursor.fetchone()

    if reservation is None:
        raise HTTPException(
            status_code=404,
            detail="Réservation introuvable.",
        )

    if reservation["statut"] != "confirmee":
        raise HTTPException(
            status_code=409,
            detail="La réservation n'est pas confirmée.",
        )

    if reservation["statut_paiement"] != "en_attente":
        raise HTTPException(
            status_code=409,
            detail="Un paiement a déjà été enregistré.",
        )

    montant = reservation["montant_a_payer"]

    if montant is None or montant <= 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Le montant à payer n'est pas renseigné "
                "ou est nul."
            ),
        )

    # Stripe utilise les centimes : 80,00 € devient 8000.
    montant_centimes = int(montant * 100)

    try:
        session = stripe.checkout.Session.create(
            api_key=cle,
            mode="payment",
            client_reference_id=str(reservation_id),

            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "unit_amount": montant_centimes,
                        "product_data": {
                            "name": (
                                f"Réservation n° {reservation_id}"
                                " — paiement de 50 %"
                            ),
                        },
                    },
                    "quantity": 1,
                }
            ],

            metadata={
                "reservation_id": str(reservation_id),
                "type_paiement": "premiere_moitie",
            },

            success_url=(
                f"{frontend_url}/?paiement=retour"
                "&session_id={CHECKOUT_SESSION_ID}"
            ),

            cancel_url=(
                f"{frontend_url}/?paiement=annule"
            ),

            # Même clé pour éviter la création répétée
            # d'une session identique pendant le test.
            idempotency_key=(
                f"reservation-{reservation_id}"
                f"-premiere-moitie-{montant_centimes}-test-v2"
            ),
        )

    except stripe.StripeError as erreur:
        message = (
            getattr(erreur, "user_message", None)
            or "Aucun message utilisateur disponible."
        )

        # Évite d'afficher la clé si elle apparaît dans l'erreur.
        message = str(message).replace(cle, "[CLE MASQUEE]")

        print("Type d'erreur Stripe :", type(erreur).__name__)
        print("Code Stripe :", getattr(erreur, "code", None))
        print("Message Stripe :", message)

        raise HTTPException(
            status_code=502,
            detail="Impossible de créer le lien Stripe de test.",
        )

    expiration = datetime.fromtimestamp(
        session.expires_at,
        tz=timezone.utc,
    )

    # Enregistre la session et conserve la colonne utilisée
    # par le webhook actuel.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sessions_paiement (
                    reservation_id,
                    stripe_session_id,
                    expire_le
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (stripe_session_id) DO NOTHING;
                """,
                (
                    reservation_id,
                    session.id,
                    expiration,
                ),
            )

            cursor.execute(
                """
                SELECT reservation_id
                FROM sessions_paiement
                WHERE stripe_session_id = %s;
                """,
                (session.id,),
            )

            session_enregistree = cursor.fetchone()

            if session_enregistree["reservation_id"] != reservation_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cette session Stripe appartient "
                        "à une autre réservation."
                    ),
                )

            cursor.execute(
                """
                UPDATE reservations
                SET stripe_session_id = %s
                WHERE id = %s;
                """,
                (session.id, reservation_id),
            )

    if session.status != "open":
        raise HTTPException(
            status_code=409,
            detail=(
                "Cette session n'est plus ouverte. "
                "La gestion du renouvellement reste à ajouter."
            ),
        )

    return {
        "reservation_id": reservation_id,
        "montant": str(montant),
        "devise": "EUR",
        "url_paiement": session.url,
        "session_id": session.id,
        "expire_le": expiration.isoformat(),
    }


@router.post("/webhook")
async def recevoir_webhook(request: Request):
    """
    Reçoit et vérifie les notifications Stripe.

    Si le premier paiement est confirmé :
    - la réservation passe à 'partiel' ;
    - la session est marquée 'payee'.
    """

    secret = os.getenv(
        "STRIPE_WEBHOOK_SECRET",
        "",
    ).strip()

    if not secret.startswith("whsec_"):
        raise HTTPException(
            status_code=503,
            detail="Le secret du webhook manque dans .env.",
        )

    # Stripe vérifie la signature à partir du corps brut.
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        evenement = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=secret,
        )

        # Avec ta version de Stripe, convertir l'objet en dictionnaire
        # permet d'utiliser les méthodes .get().
        evenement = evenement.to_dict()

    except (ValueError, stripe.SignatureVerificationError) as erreur:
        print(
            "Échec de vérification du webhook :",
            type(erreur).__name__,
        )

        raise HTTPException(
            status_code=400,
            detail="Notification Stripe invalide.",
        )

    # Cette version du projet accepte uniquement les tests.
    if evenement.get("livemode") is not False:
        raise HTTPException(
            status_code=400,
            detail="Les paiements réels ne sont pas acceptés ici.",
        )

    type_evenement = evenement["type"]

    if type_evenement not in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ):
        return {
            "recu": True,
            "ignore": True,
        }

    session = evenement["data"]["object"]

    # Une session terminée n'est pas forcément payée.
    if (
        session.get("mode") != "payment"
        or session.get("payment_status") != "paid"
    ):
        return {
            "recu": True,
            "paiement_en_attente": True,
        }

    metadata = session.get("metadata") or {}

    if metadata.get("type_paiement") != "premiere_moitie":
        return {
            "recu": True,
            "ignore": True,
        }

    session_id = session.get("id")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    montant_a_payer,
                    statut,
                    statut_paiement,
                    premier_paiement_recu_le
                FROM reservations
                WHERE stripe_session_id = %s
                FOR UPDATE;
                """,
                (session_id,),
            )

            reservation = cursor.fetchone()

            if reservation is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Session Stripe non associée "
                        "à une réservation."
                    ),
                )

            reservation_id = reservation["id"]

            if (
                metadata.get("reservation_id")
                != str(reservation_id)
                or session.get("client_reference_id")
                != str(reservation_id)
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "La référence de réservation "
                        "ne correspond pas."
                    ),
                )

            montant = reservation["montant_a_payer"]

            if (
                montant is None
                or montant <= 0
                or session.get("currency") != "eur"
                or session.get("amount_total") != int(montant * 100)
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Le montant ou la devise "
                        "ne correspond pas."
                    ),
                )

            # Stripe peut envoyer plusieurs fois le même événement.
            if reservation["premier_paiement_recu_le"] is not None:
                return {
                    "recu": True,
                    "deja_traite": True,
                }

            cursor.execute(
                """
                UPDATE reservations
                SET statut_paiement = CASE
                        WHEN statut_paiement = 'paye' THEN 'paye'
                        ELSE 'partiel'
                    END,
                    premier_paiement_recu_le = CURRENT_TIMESTAMP
                WHERE id = %s;
                """,
                (reservation_id,),
            )

            cursor.execute(
                """
                UPDATE sessions_paiement
                SET statut = 'payee',
                    payee_le = CURRENT_TIMESTAMP
                WHERE stripe_session_id = %s;
                """,
                (session_id,),
            )

            # On ne réactive pas automatiquement une réservation annulée.
            if reservation["statut"] == "annulee":
                print(
                    "Paiement reçu pour une réservation annulée :",
                    reservation_id,
                    "— vérification manuelle nécessaire.",
                )

    return {
        "recu": True,
        "paiement_enregistre": True,
    }
@router.get("/sms-a-simuler")
def simuler_sms():
    """
    Liste les SMS qui seraient envoyés aujourd'hui.
    Aucun message réel n'est envoyé.
    """

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.id,
                    r.date_arrivee,
                    r.montant_a_payer,
                    c.nom,
                    c.telephone
                FROM reservations AS r
                JOIN clients AS c ON c.id = r.client_id
                WHERE r.statut = 'confirmee'
                  AND r.statut_paiement = 'en_attente'
                  AND r.montant_a_payer > 0
                  AND r.date_envoi_paiement <= CURRENT_DATE
                  AND r.date_arrivee >= CURRENT_DATE
                  AND r.sms_paiement_envoye_le IS NULL
                ORDER BY r.date_envoi_paiement, r.id;
                """
            )

            reservations = cursor.fetchall()

    sms_simules = []

    for reservation in reservations:
        sms_simules.append(
            {
                "reservation_id": reservation["id"],
                "telephone": reservation["telephone"],
                "message": (
                    f"Bonjour {reservation['nom']}, "
                    f"pour votre réservation n° {reservation['id']}, "
                    f"un acompte de {reservation['montant_a_payer']} € "
                    "est à régler avant votre arrivée. "
                    "[Lien de paiement à ajouter]"
                ),
            }
        )

    return {
        "simulation": True,
        "nombre_sms": len(sms_simules),
        "sms": sms_simules,
    }
@router.post("/reservations/{reservation_id}/simuler-sms")
def simuler_sms_avec_lien(reservation_id: int):
    """
    Prépare un message de test contenant un lien Stripe.
    Aucun SMS réel n'est envoyé.
    """

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.id,
                    r.date_arrivee,
                    r.montant_a_payer,
                    r.statut,
                    r.statut_paiement,
                    c.nom,
                    c.telephone
                FROM reservations AS r
                JOIN clients AS c ON c.id = r.client_id
                WHERE r.id = %s;
                """,
                (reservation_id,),
            )
            reservation = cursor.fetchone()

    if reservation is None:
        raise HTTPException(
            status_code=404,
            detail="Réservation introuvable.",
        )

    if reservation["statut"] != "confirmee":
        raise HTTPException(
            status_code=409,
            detail="La réservation n'est pas confirmée.",
        )

    if reservation["statut_paiement"] != "en_attente":
        raise HTTPException(
            status_code=409,
            detail="Cette réservation n'est plus en attente de paiement.",
        )

    # Réutilise la fonction existante de création du lien Stripe.
    resultat_lien = creer_lien_test(reservation_id)

    message = (
        f"Bonjour {reservation['nom']}, "
        f"pour votre réservation n° {reservation_id}, "
        f"un acompte de {reservation['montant_a_payer']} € "
        f"est à régler. Lien de paiement : "
        f"{resultat_lien['url_paiement']}"
    )

    return {
        "simulation": True,
        "reservation_id": reservation_id,
        "telephone": reservation["telephone"],
        "message": message,
        "expire_le": resultat_lien["expire_le"],
    }