"""File SMS persistante. Par défaut : simulation, aucun SMS personnalisé envoyé."""
import asyncio
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from twilio.base.exceptions import TwilioRestException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from database import get_connection
from paiements import (
    charger_reservation_sms,
    creer_lien_test,
    composer_message_paiement,
    montant_en_centimes,
    simuler_sms_avec_lien,
)

load_dotenv(Path(__file__).with_name(".env"))
_arret = threading.Event()
_fil = None


def option_active(nom, defaut="false"):
    return os.getenv(nom, defaut).strip().lower() in {"true", "1", "oui"}


def mode_sms():
    mode = os.getenv("SMS_MODE", "simulation").strip().lower()
    if mode not in {"simulation", "paiement"}:
        raise HTTPException(503, "SMS_MODE doit valoir simulation ou paiement.")
    return mode


def numero_international(texte):
    numero = re.sub(r"[\s().-]", "", str(texte or ""))
    if numero.startswith("00"):
        numero = "+" + numero[2:]
    if not re.fullmatch(r"\+[1-9]\d{7,14}", numero):
        raise HTTPException(400, "Le téléphone doit être au format international, par exemple +33612345678.")
    return numero


def charger_configuration_twilio(paiement=False):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    expediteur = os.getenv("TWILIO_FROM", "").strip()
    # Autorisations de test uniquement. Le destinataire provient toujours du client.
    liste = os.getenv("TWILIO_TO", "").strip()
    if not all([account_sid, auth_token, expediteur, liste]):
        raise HTTPException(503, "Renseigne TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM et TWILIO_TO dans .env.")
    if not account_sid.startswith("AC"):
        raise HTTPException(503, "TWILIO_ACCOUNT_SID doit être l'Account SID commençant par AC.")
    numeros = tuple(numero_international(n.strip()) for n in liste.split(",") if n.strip())
    if not numeros:
        raise HTTPException(503, "TWILIO_TO doit contenir au moins un numéro de test autorisé.")
    if paiement and option_active("TWILIO_COMPTE_ESSAI", "true"):
        raise HTTPException(503, "L'essai Twilio impose des modèles. Garde SMS_MODE=simulation pour le lien de paiement personnalisé.")
    return {
        "account_sid": account_sid, "auth_token": auth_token,
        "numero_expediteur": numero_international(expediteur),
        "numeros_autorises": numeros, "numero_autorise": numeros[0],
    }


def client_twilio(configuration):
    # Aucun réessai applicatif automatique de messages.create().
    return Client(configuration["account_sid"], configuration["auth_token"],
                  http_client=TwilioHttpClient(timeout=20))


def verifier_destinataire(numero, configuration):
    numero = numero_international(numero)
    if numero not in configuration["numeros_autorises"]:
        raise HTTPException(403, "Le téléphone du client ne figure pas dans TWILIO_TO, la liste des destinataires autorisés pour ce test.")
    return numero


def reserver_envoi(reservation_id, type_message):
    """Réutilise une ligne prepare et empêche deux processus de l'envoyer."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, statut, statut_paiement FROM reservations
                   WHERE id = %s FOR UPDATE;""", (reservation_id,),
            )
            r = cursor.fetchone()
            if r is None:
                raise HTTPException(404, "Réservation introuvable.")
            if r["statut"] != "confirmee" or r["statut_paiement"] != "en_attente":
                raise HTTPException(409, "La réservation n'est pas confirmée et en attente de paiement.")
            cursor.execute(
                """INSERT INTO envois_sms (reservation_id, type_message, statut)
                   VALUES (%s, %s, 'prepare')
                   ON CONFLICT (reservation_id, type_message) DO NOTHING;""",
                (reservation_id, type_message),
            )
            cursor.execute(
                """UPDATE envois_sms SET statut = 'en_cours', modifie_le = CURRENT_TIMESTAMP
                   WHERE reservation_id = %s AND type_message = %s AND statut = 'prepare'
                   RETURNING id;""", (reservation_id, type_message),
            )
            envoi = cursor.fetchone()
            if envoi is None:
                raise HTTPException(409, "Un envoi est déjà en cours, accepté, livré ou à vérifier. Consulte son statut avant toute nouvelle tentative.")
    return envoi["id"]


def noter_echec(envoi_id, statut):
    # incertain n'est jamais remis automatiquement dans la file.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE envois_sms SET statut = %s, modifie_le = CURRENT_TIMESTAMP
                   WHERE id = %s AND statut = 'en_cours';""", (statut, envoi_id),
            )


def statut_local(statut_twilio):
    if statut_twilio in {"delivered", "read"}:
        return "livre"
    if statut_twilio in {"failed", "undelivered", "canceled"}:
        return "echec"
    return "accepte"


def envoyer_pour_reservation(reservation_id, modele_essai=False):
    if not modele_essai and mode_sms() == "simulation":
        # Ce POST peut créer une session Stripe de test, mais pas de SMS.
        return simuler_sms_avec_lien(reservation_id)
    configuration = charger_configuration_twilio(paiement=not modele_essai)
    reservation = charger_reservation_sms(reservation_id)
    verifier_destinataire(reservation["telephone"], configuration)
    type_message = "essai_twilio" if modele_essai else "rappel_paiement"
    envoi_id = reserver_envoi(reservation_id, type_message)
    appel_twilio_commence = False
    try:
        lien = None if modele_essai else creer_lien_test(reservation_id)
        corps = "sms_appointment_reminders" if modele_essai else composer_message_paiement(
            reservation_id, lien["montant"], lien["url_paiement"],
        )
        # Verrou jusqu'à la réponse de Twilio : une annulation ou un webhook
        # concurrent doit attendre la fin de cette demande d'envoi.
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT r.statut, r.statut_paiement, r.montant_a_payer, c.telephone
                       FROM reservations AS r JOIN clients AS c ON c.id = r.client_id
                       WHERE r.id = %s FOR UPDATE OF r;""", (reservation_id,),
                )
                actuel = cursor.fetchone()
                if not actuel or actuel["statut"] != "confirmee" or actuel["statut_paiement"] != "en_attente":
                    raise HTTPException(409, "La réservation a changé. Aucun SMS n'a été demandé.")
                numero = verifier_destinataire(actuel["telephone"], configuration)
                if lien and montant_en_centimes(actuel["montant_a_payer"]) != montant_en_centimes(lien["montant"]):
                    raise HTTPException(409, "Le montant a changé. Aucun SMS n'a été demandé.")
                client = client_twilio(configuration)
                appel_twilio_commence = True
                message = client.messages.create(
                    to=numero, from_=configuration["numero_expediteur"], body=corps,
                )
                statut = statut_local(message.status)
                cursor.execute(
                    """UPDATE envois_sms SET statut = %s, twilio_message_sid = %s,
                              modifie_le = CURRENT_TIMESTAMP WHERE id = %s;""",
                    (statut, message.sid, envoi_id),
                )
                if not modele_essai and statut in {"accepte", "livre"}:
                    # Ce champ signifie acceptation du SMS de paiement, pas livraison.
                    cursor.execute(
                        """UPDATE reservations SET sms_paiement_envoye_le = CURRENT_TIMESTAMP
                           WHERE id = %s;""", (reservation_id,),
                    )
    except Exception as erreur:
        certain_non_envoye = not appel_twilio_commence
        if isinstance(erreur, TwilioRestException):
            print("Twilio : code", erreur.code, "HTTP", erreur.status)
            certain_non_envoye = erreur.status is not None and 400 <= erreur.status < 500
        else:
            print("Traitement SMS :", type(erreur).__name__)
        etat = "echec" if certain_non_envoye else "incertain"
        try:
            noter_echec(envoi_id, etat)
        except Exception:
            print("SMS : état non enregistré, laisser l'envoi en vérification.")
        if isinstance(erreur, HTTPException):
            raise
        raise HTTPException(502, f"Envoi {etat}. Vérifie le journal Twilio avant toute nouvelle tentative.")
    return {
        "reservation_id": reservation_id, "envoi_id": envoi_id,
        "simulation": False, "modele_essai": modele_essai,
        "message_sid": message.sid, "statut": statut, "statut_twilio": message.status,
        "message": "Réponse Twilio enregistrée. Le statut accepte ne prouve pas la livraison.",
    }


def liste_en_attente():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT e.reservation_id FROM envois_sms AS e
                   JOIN reservations AS r ON r.id = e.reservation_id
                   WHERE e.type_message = 'rappel_paiement' AND e.statut = 'prepare'
                     AND r.statut = 'confirmee' AND r.statut_paiement = 'en_attente'
                     AND r.montant_a_payer > 0 AND r.date_arrivee >= CURRENT_DATE
                     AND r.date_envoi_paiement <= CURRENT_DATE
                   ORDER BY e.id LIMIT 20;"""
            )
            return [r["reservation_id"] for r in cursor.fetchall()]


def traiter_file():
    if mode_sms() == "simulation":
        return {"simulation": True, "reservations_en_attente": liste_en_attente(), "sms_envoyes": 0}
    # Valide la configuration avant toute prise de ligne dans la file.
    charger_configuration_twilio(paiement=True)
    resultats = []
    for reservation_id in liste_en_attente():
        if _arret.is_set():
            break
        try:
            resultats.append(envoyer_pour_reservation(reservation_id))
        except HTTPException as erreur:
            # 409 peut signifier qu'un autre processus a pris la même ligne.
            resultats.append({"reservation_id": reservation_id, "code": erreur.status_code, "detail": erreur.detail})
    return {"simulation": False, "resultats": resultats}


def boucle_sms():
    dernier_resume = None
    while not _arret.is_set():
        try:
            # Un processus tombé après l'appel à Twilio laisse un état ambigu.
            # Ne jamais renvoyer automatiquement ces messages.
            with get_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE envois_sms SET statut = 'incertain', modifie_le = CURRENT_TIMESTAMP
                           WHERE statut = 'en_cours'
                             AND modifie_le < CURRENT_TIMESTAMP - INTERVAL '10 minutes';"""
                    )
            resultat = traiter_file()
            if resultat.get("simulation"):
                resume = tuple(resultat["reservations_en_attente"])
                if resume != dernier_resume:
                    print("SMS simulation :", len(resume), "demande(s) prête(s), aucun SMS envoyé.")
                dernier_resume = resume
            else:
                for ligne in resultat["resultats"]:
                    print("File SMS : réservation", ligne["reservation_id"], "statut", ligne.get("statut", ligne.get("code")))
        except Exception as erreur:
            # Pas de clés, de numéros, de liens ni de réponse fournisseur complète.
            print("File SMS interrompue pour ce passage :", type(erreur).__name__)
        _arret.wait(5)


@asynccontextmanager
async def vie_sms(app):
    global _fil
    if option_active("SMS_AUTOMATIQUE"):
        _arret.clear()
        _fil = threading.Thread(target=boucle_sms, name="file-sms", daemon=True)
        _fil.start()
        print("File SMS démarrée. Mode :", os.getenv("SMS_MODE", "simulation"))
    try:
        yield
    finally:
        _arret.set()
        if _fil is not None:
            await asyncio.to_thread(_fil.join, 25)
            _fil = None


router = APIRouter(prefix="/sms", tags=["SMS"], lifespan=vie_sms)


@router.get("/configuration")
def configuration_sms():
    return {
        "mode": mode_sms(), "automatique_demande": option_active("SMS_AUTOMATIQUE"),
        "traitement_actif": _fil is not None and _fil.is_alive(),
        "compte_essai_declare": option_active("TWILIO_COMPTE_ESSAI", "true"),
        "delai_verification_secondes": 5,
    }


@router.post("/essai")
def envoyer_sms_essai():
    """VRAI SMS modèle, même en mode simulation. Chaque clic peut en envoyer un."""
    configuration = charger_configuration_twilio()
    try:
        message = client_twilio(configuration).messages.create(
            to=configuration["numero_autorise"], from_=configuration["numero_expediteur"],
            body="sms_appointment_reminders",
        )
    except TwilioRestException as erreur:
        print("Twilio essai : code", erreur.code, "HTTP", erreur.status)
        raise HTTPException(502, "Twilio a refusé l'essai. Consulte le code dans le terminal.")
    return {"modele_essai": True, "message_sid": message.sid, "statut_twilio": message.status}


@router.post("/reservations/{reservation_id}/essai")
def envoyer_sms_reservation_essai(reservation_id: int):
    """VRAI SMS modèle. Ne consomme pas la ligne rappel_paiement."""
    return envoyer_pour_reservation(reservation_id, modele_essai=True)


@router.post("/reservations/{reservation_id}/envoyer")
def envoyer_sms_paiement(reservation_id: int):
    """Simulation avec lien ou VRAI SMS selon SMS_MODE."""
    return envoyer_pour_reservation(reservation_id)


@router.post("/traiter-file")
def traiter_file_manuellement():
    """Permet un passage manuel, sans attendre le traitement automatique."""
    return traiter_file()


@router.get("/reservations/{reservation_id}/statut")
def statut_sms(reservation_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, type_message, statut, twilio_message_sid, modifie_le
                   FROM envois_sms WHERE reservation_id = %s ORDER BY id;""", (reservation_id,),
            )
            lignes = cursor.fetchall()
    return {"reservation_id": reservation_id, "envois": lignes}


@router.post("/reservations/{reservation_id}/actualiser-statut")
def actualiser_statut(reservation_id: int):
    """Lit Twilio sans envoyer de message. Aucune relance SMS automatique."""
    configuration = charger_configuration_twilio()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, statut, twilio_message_sid FROM envois_sms
                   WHERE reservation_id = %s AND type_message = 'rappel_paiement';""", (reservation_id,),
            )
            envoi = cursor.fetchone()
    if not envoi or not envoi["twilio_message_sid"]:
        raise HTTPException(409, "Aucun message Twilio associé. Consulte les journaux si l'envoi est incertain.")
    try:
        message = client_twilio(configuration).messages(envoi["twilio_message_sid"]).fetch()
    except TwilioRestException as erreur:
        print("Twilio statut :", erreur.code)
        raise HTTPException(502, "Impossible de consulter le statut Twilio.")
    nouvel_etat = statut_local(message.status)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE envois_sms SET statut = %s, modifie_le = CURRENT_TIMESTAMP
                   WHERE id = %s AND statut != 'livre';""", (nouvel_etat, envoi["id"]),
            )
    return {"reservation_id": reservation_id, "statut_twilio": message.status, "statut": nouvel_etat}
