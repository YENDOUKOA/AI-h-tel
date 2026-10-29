"""Paiements de démonstration : Stripe TEST uniquement."""
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import stripe
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request

from database import get_connection

load_dotenv(Path(__file__).with_name(".env"))
router = APIRouter(prefix="/paiements", tags=["Paiements"])


def montant_en_centimes(valeur):
    """Refuse un montant invalide plutôt que de tronquer des fractions."""
    try:
        montant = Decimal(str(valeur))
        centimes = montant * 100
        if not montant.is_finite() or montant <= 0 or centimes != centimes.to_integral_value():
            raise ValueError()
        return int(centimes)
    except (ValueError, TypeError, ArithmeticError):
        raise HTTPException(409, "Le montant à payer doit être positif et exprimé au centime.")


def dictionnaire_stripe(objet):
    # Les StripeObject récents ne sont pas des dict et ne possèdent pas .get().
    if isinstance(objet, dict):
        return objet
    return objet.to_dict()


def verifier_session(session, reservation_id, centimes):
    metadata = session.get("metadata") or {}
    if (session.get("livemode") is not False
            or session.get("mode") != "payment"
            or session.get("currency") != "eur"
            or session.get("amount_total") != centimes
            or session.get("client_reference_id") != str(reservation_id)
            or metadata.get("reservation_id") != str(reservation_id)
            or metadata.get("type_paiement") != "premiere_moitie"):
        raise HTTPException(409, "La session Stripe ne correspond pas au paiement attendu.")


@router.post("/reservations/{reservation_id}/lien-test")
def creer_lien_test(reservation_id: int):
    cle = os.getenv("STRIPE_SECRET_KEY", "").strip()
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").strip().rstrip("/")
    if not cle.startswith("sk_test_"):
        raise HTTPException(503, "Configure une clé secrète Stripe de test dans .env.")
    adresse = urlsplit(frontend_url)
    if adresse.scheme not in {"http", "https"} or not adresse.netloc or adresse.query or adresse.fragment:
        raise HTTPException(503, "FRONTEND_URL doit être une adresse http(s) sans paramètres ni fragment.")

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                # Les créations concurrentes pour la même réservation sont sérialisées.
                cursor.execute(
                    """SELECT id, prix_total, montant_a_payer, statut,
                              statut_paiement, stripe_session_id
                       FROM reservations WHERE id = %s FOR UPDATE;""",
                    (reservation_id,),
                )
                reservation = cursor.fetchone()
                if reservation is None:
                    raise HTTPException(404, "Réservation introuvable.")
                if reservation["statut"] != "confirmee":
                    raise HTTPException(409, "La réservation n'est pas confirmée.")
                if reservation["statut_paiement"] != "en_attente":
                    raise HTTPException(409, "Un paiement a déjà été enregistré.")
                centimes = montant_en_centimes(reservation["montant_a_payer"])
                ancien_id = reservation["stripe_session_id"]
                session = None
                if ancien_id:
                    precedente = dictionnaire_stripe(
                        stripe.checkout.Session.retrieve(ancien_id, api_key=cle)
                    )
                    # Un paiement déjà terminé doit attendre son webhook.
                    if precedente.get("status") == "complete" or precedente.get("payment_status") == "paid":
                        raise HTTPException(409, "Cette session est terminée. Vérifie la réception du webhook Stripe.")
                    if precedente.get("status") == "open":
                        verifier_session(precedente, reservation_id, centimes)
                        if precedente.get("expires_at", 0) <= datetime.now(timezone.utc).timestamp():
                            raise HTTPException(409, "Stripe n'a pas encore clôturé cette session. Réessaie plus tard.")
                        session = precedente
                    elif precedente.get("status") == "expired":
                        cursor.execute(
                            """UPDATE sessions_paiement SET statut = 'expiree'
                               WHERE stripe_session_id = %s AND statut != 'payee';""", (ancien_id,),
                        )
                    else:
                        raise HTTPException(409, "La session précédente doit être vérifiée dans Stripe.")

                if session is None:
                    parametres = {
                        "mode": "payment",
                        "client_reference_id": str(reservation_id),
                        "line_items": [{"price_data": {
                            "currency": "eur", "unit_amount": centimes,
                            "product_data": {"name": f"Réservation n° {reservation_id} — paiement de 50 %"},
                        }, "quantity": 1}],
                        "metadata": {"reservation_id": str(reservation_id), "type_paiement": "premiere_moitie"},
                        "success_url": frontend_url + "/?paiement=retour&session_id={CHECKOUT_SESSION_ID}",
                        "cancel_url": frontend_url + "/?paiement=annule",
                    }
                    # Clé stable en cas de répétition, différente après expiration
                    # ou changement des paramètres. Aucun secret dans cette clé.
                    empreinte = hashlib.sha256(json.dumps(
                        {"params": parametres, "precedente": ancien_id},
                        sort_keys=True, ensure_ascii=False,
                    ).encode()).hexdigest()
                    session = dictionnaire_stripe(stripe.checkout.Session.create(
                        **parametres, api_key=cle,
                        idempotency_key=f"hotel-r{reservation_id}-test-v3-{empreinte}",
                    ))
                    verifier_session(session, reservation_id, centimes)
                if session.get("status") != "open" or not session.get("url"):
                    raise HTTPException(409, "Cette session Stripe n'est pas ouverte.")
                expiration = datetime.fromtimestamp(session["expires_at"], tz=timezone.utc)
                cursor.execute(
                    """INSERT INTO sessions_paiement (reservation_id, stripe_session_id, expire_le)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (stripe_session_id) DO NOTHING;""",
                    (reservation_id, session["id"], expiration),
                )
                cursor.execute("SELECT reservation_id FROM sessions_paiement WHERE stripe_session_id = %s;", (session["id"],))
                association = cursor.fetchone()
                if not association or association["reservation_id"] != reservation_id:
                    raise HTTPException(409, "Cette session Stripe appartient à une autre réservation.")
                cursor.execute("UPDATE reservations SET stripe_session_id = %s WHERE id = %s;", (session["id"], reservation_id))
    except stripe.StripeError as erreur:
        # Ne pas écrire de clé, de réponse complète ou de coordonnées dans les logs.
        print("Stripe :", type(erreur).__name__, "code :", getattr(erreur, "code", None))
        raise HTTPException(502, "Impossible de préparer le lien Stripe de test.")
    return {
        "reservation_id": reservation_id, "montant": str(Decimal(centimes) / 100),
        "devise": "EUR", "url_paiement": session["url"],
        "session_id": session["id"], "expire_le": expiration.isoformat(),
    }


def charger_reservation_sms(reservation_id):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT r.id, r.date_arrivee, r.montant_a_payer, r.statut,
                          r.statut_paiement, c.nom, c.telephone
                   FROM reservations AS r JOIN clients AS c ON c.id = r.client_id
                   WHERE r.id = %s;""", (reservation_id,),
            )
            reservation = cursor.fetchone()
    if reservation is None:
        raise HTTPException(404, "Réservation introuvable.")
    if reservation["statut"] != "confirmee" or reservation["statut_paiement"] != "en_attente":
        raise HTTPException(409, "Cette réservation n'est pas confirmée et en attente de paiement.")
    montant_en_centimes(reservation["montant_a_payer"])
    return reservation


def composer_message_paiement(reservation_id, montant, lien):
    # Message court sans nom complet, pour limiter les données envoyées par SMS.
    somme = f"{Decimal(str(montant)):.2f}".replace(".", ",")
    return (f"Reservation n° {reservation_id} confirmee. "
            f"50 % du sejour a regler : {somme} EUR. "
            f"Paiement TEST : {lien} Le solde sera a payer a l'hotel.")


@router.post("/reservations/{reservation_id}/simuler-sms")
def simuler_sms_avec_lien(reservation_id: int):
    """Crée/retrouve une session Stripe TEST, sans envoyer de SMS."""
    reservation = charger_reservation_sms(reservation_id)
    lien = creer_lien_test(reservation_id)
    return {
        "simulation": True, "reservation_id": reservation_id,
        "telephone": reservation["telephone"],
        "message": composer_message_paiement(reservation_id, lien["montant"], lien["url_paiement"]),
        "url_paiement": lien["url_paiement"], "session_id": lien["session_id"],
        "expire_le": lien["expire_le"],
    }


@router.get("/sms-a-simuler")
def simuler_sms():
    """Aperçu de la file : ni appel à Stripe ni SMS."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT e.id AS envoi_id, e.reservation_id, e.statut, r.montant_a_payer
                   FROM envois_sms AS e JOIN reservations AS r ON r.id = e.reservation_id
                   WHERE e.type_message = 'rappel_paiement' AND e.statut = 'prepare'
                     AND r.statut = 'confirmee' AND r.statut_paiement = 'en_attente'
                   ORDER BY e.id;"""
            )
            lignes = cursor.fetchall()
    return {"simulation": True, "nombre_sms": len(lignes), "sms": lignes}


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
        evenement = dictionnaire_stripe(evenement)

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
            # Accepte aussi une ancienne session enregistrée dans l'historique.
            cursor.execute(
                "SELECT reservation_id FROM sessions_paiement WHERE stripe_session_id = %s;",
                (session_id,),
            )
            association = cursor.fetchone()
            if association is None:
                raise HTTPException(409, "Session Stripe non associée à une réservation.")
            cursor.execute(
                """SELECT id, montant_a_payer, statut, statut_paiement,
                          premier_paiement_recu_le
                   FROM reservations WHERE id = %s FOR UPDATE;""",
                (association["reservation_id"],),
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
                or session.get("amount_total") != montant_en_centimes(montant)
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Le montant ou la devise "
                        "ne correspond pas."
                    ),
                )

            cursor.execute(
                """UPDATE sessions_paiement SET statut = 'payee',
                          payee_le = COALESCE(payee_le, CURRENT_TIMESTAMP)
                   WHERE stripe_session_id = %s;""", (session_id,),
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
