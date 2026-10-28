import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from database import get_connection


# Charger le fichier .env situé dans le dossier backend.
load_dotenv(Path(__file__).with_name(".env"))

router = APIRouter(
    prefix="/sms",
    tags=["SMS"],
)


def charger_configuration_twilio():
    """
    Charge la configuration Twilio depuis le fichier .env.
    """

    account_sid = os.getenv(
        "TWILIO_ACCOUNT_SID",
        "",
    ).strip()

    auth_token = os.getenv(
        "TWILIO_AUTH_TOKEN",
        "",
    ).strip()

    numero_expediteur = os.getenv(
        "TWILIO_FROM",
        "",
    ).strip()

    # Numéro vérifié, autorisé pour notre essai.
    numero_autorise = os.getenv(
        "TWILIO_TO",
        "",
    ).strip()

    if not all(
        [
            account_sid,
            auth_token,
            numero_expediteur,
            numero_autorise,
        ]
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "La configuration Twilio est incomplète "
                "dans le fichier .env."
            ),
        )

    if not (
        numero_expediteur.startswith("+")
        and numero_autorise.startswith("+")
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Les numéros Twilio doivent commencer par + "
                "et être au format international."
            ),
        )

    return {
        "TWILIO_TEST_ACCOUNT_SID": account_sid,
        "TWILIO_TEST_AUTH_TOKEN": auth_token,
        "TWILIO_TEST_FROM": numero_expediteur,
        "TWILIO_TEST_TO": numero_autorise,
    }


@router.post("/essai")
def envoyer_sms_essai():
    """
    Envoie un SMS d'essai au numéro vérifié dans .env.

    Attention : cette route déclenche un vrai SMS à chaque appel.
    """

    configuration = charger_configuration_twilio()

    try:
        client_twilio = Client(
            configuration["account_sid"],
            configuration["auth_token"],
        )

        message = client_twilio.messages.create(
            to=configuration["numero_autorise"],
            from_=configuration["numero_expediteur"],
            body="sms_appointment_reminders",
        )

    except TwilioRestException as erreur:
        print("Code erreur Twilio :", erreur.code)
        print("Statut HTTP Twilio :", erreur.status)

        raise HTTPException(
            status_code=502,
            detail=(
                "Twilio a refusé l'envoi. "
                "Consulte le terminal pour connaître le code."
            ),
        )

    return {
        "message": "La demande d'envoi a été acceptée par Twilio.",
        "message_sid": message.sid,
        "statut_twilio": message.status,
    }


@router.post("/reservations/{reservation_id}/essai")
def envoyer_sms_reservation_essai(reservation_id: int):
    """
    Envoie le modèle Twilio d'essai au client d'une réservation.

    Pendant l'essai, seul le numéro vérifié dans TWILIO_TO
    est autorisé.

    Une seule tentative est enregistrée par réservation et
    par type de message.
    """

    configuration = charger_configuration_twilio()

    # Cette transaction vérifie la réservation puis crée
    # une ligne 'en_cours' avant de contacter Twilio.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.id,
                    r.statut,
                    r.statut_paiement,
                    c.telephone
                FROM reservations AS r
                JOIN clients AS c ON c.id = r.client_id
                WHERE r.id = %s
                FOR UPDATE OF r;
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
                    detail=(
                        "La réservation n'est pas "
                        "en attente de paiement."
                    ),
                )

            numero_client = (
                reservation["telephone"] or ""
            ).strip()

            if not numero_client.startswith("+"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Le numéro du client doit être au format "
                        "international et commencer par +."
                    ),
                )

            if numero_client != configuration["numero_autorise"]:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Pendant l'essai, le numéro du client doit "
                        "correspondre au numéro vérifié TWILIO_TO."
                    ),
                )

            cursor.execute(
                """
                INSERT INTO envois_sms (
                    reservation_id,
                    type_message,
                    statut
                )
                VALUES (
                    %s,
                    'rappel_paiement',
                    'en_cours'
                )
                ON CONFLICT (
                    reservation_id,
                    type_message
                )
                DO NOTHING
                RETURNING id;
                """,
                (reservation_id,),
            )

            envoi = cursor.fetchone()

            if envoi is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Un envoi existe déjà pour cette réservation. "
                        "Vérifie son statut dans la table envois_sms."
                    ),
                )

            envoi_id = envoi["id"]

    # La transaction précédente est validée avant l'appel à Twilio.
    try:
        client_twilio = Client(
            configuration["account_sid"],
            configuration["auth_token"],
        )

        message = client_twilio.messages.create(
            to=numero_client,
            from_=configuration["numero_expediteur"],
            body="sms_appointment_reminders",
        )

    except Exception as erreur:
        # Si la connexion est interrompue, on ne sait pas toujours
        # si Twilio a reçu la demande.
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE envois_sms
                    SET statut = 'incertain',
                        modifie_le = CURRENT_TIMESTAMP
                    WHERE id = %s;
                    """,
                    (envoi_id,),
                )

        if isinstance(erreur, TwilioRestException):
            print("Code erreur Twilio :", erreur.code)
            print("Statut HTTP Twilio :", erreur.status)
        else:
            print("Type d'erreur SMS :", type(erreur).__name__)

        raise HTTPException(
            status_code=502,
            detail=(
                "Envoi non confirmé. Vérifie le journal Twilio "
                "avant toute nouvelle tentative."
            ),
        )

    # Twilio a accepté la requête. Cela ne prouve pas encore
    # que le SMS est arrivé sur le téléphone.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE envois_sms
                SET statut = 'accepte',
                    twilio_message_sid = %s,
                    modifie_le = CURRENT_TIMESTAMP
                WHERE id = %s;
                """,
                (
                    message.sid,
                    envoi_id,
                ),
            )

    return {
        "reservation_id": reservation_id,
        "message": "Twilio a accepté la demande d'envoi.",
        "message_sid": message.sid,
        "statut_twilio": message.status,
    }