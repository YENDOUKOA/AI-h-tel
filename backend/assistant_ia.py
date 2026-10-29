import json
import os
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from psycopg.types.json import Jsonb
from decimal import Decimal
from psycopg.errors import ExclusionViolation

import psycopg
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from openai import (
    OpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import BaseModel, Field, ValidationError

from database import get_connection


# Chargement du fichier .env situé dans backend.
load_dotenv(Path(__file__).with_name(".env"))

router = APIRouter(
    prefix="/assistant",
    tags=["Assistant IA"],
)

# Hôtel de démonstration fixé côté serveur.
HOTEL_ID = 1


# --------------------------------------------------
# Modèles de validation
# --------------------------------------------------

class MessageHistorique(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class MessageAssistant(BaseModel):
    mode: Literal["formulaire", "vocal"] = "formulaire"
    message: str = Field(min_length=1, max_length=2000)
    historique: list[MessageHistorique] = Field(
        default_factory=list,
        max_length=20,
    )


class RechercheDisponibilites(BaseModel):
    date_arrivee: date
    date_depart: date
    nombre_personnes: int = Field(strict=True, ge=1)


class PreparationReservation(RechercheDisponibilites):
    chambre_id: int = Field(strict=True, gt=0)


# --------------------------------------------------
# Outils accessibles au modèle
# --------------------------------------------------

OUTILS = [
    {
        "type": "function",
        "function": {
            "name": "chercher_disponibilites",
            "description": (
                "Consulter les chambres disponibles de l'Hôtel Démo "
                "pour des dates précises et un nombre de personnes. "
                "Le résultat fournit l'identifiant technique et "
                "le numéro de chaque chambre."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_arrivee": {
                        "type": "string",
                        "description": "Date d'arrivée au format AAAA-MM-JJ.",
                    },
                    "date_depart": {
                        "type": "string",
                        "description": "Date de départ au format AAAA-MM-JJ.",
                    },
                    "nombre_personnes": {
                        "type": "integer",
                        "minimum": 1,
                    },
                },
                "required": [
                    "date_arrivee",
                    "date_depart",
                    "nombre_personnes",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preparer_reservation",
            "description": (
                "Préparer une proposition après un choix explicite "
                "de chambre par le client. "
                "Revérifie les disponibilités et calcule le montant. "
                "Cette fonction ne crée pas de réservation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chambre_id": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Identifiant technique retourné par la "
                            "recherche, différent du numéro de chambre."
                        ),
                    },
                    "date_arrivee": {
                        "type": "string",
                        "description": "Date d'arrivée au format AAAA-MM-JJ.",
                    },
                    "date_depart": {
                        "type": "string",
                        "description": "Date de départ au format AAAA-MM-JJ.",
                    },
                    "nombre_personnes": {
                        "type": "integer",
                        "minimum": 1,
                    },
                },
                "required": [
                    "chambre_id",
                    "date_arrivee",
                    "date_depart",
                    "nombre_personnes",
                ],
                "additionalProperties": False,
            },
        },
    },
]


# --------------------------------------------------
# Consultation de PostgreSQL
# --------------------------------------------------

def chercher_disponibilites(
    demande: RechercheDisponibilites,
):
    if demande.date_arrivee < date.today():
        return {
            "erreur": "La date d'arrivée ne peut pas être passée."
        }

    if demande.date_depart <= demande.date_arrivee:
        return {
            "erreur": "La date de départ doit être après l'arrivée."
        }

    nombre_nuits = (
        demande.date_depart - demande.date_arrivee
    ).days

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.id,
                    c.numero,
                    t.nom AS type_chambre,
                    t.capacite,
                    t.prix_base AS prix_par_nuit,
                    t.prix_base * %s AS prix_total
                FROM chambres AS c
                JOIN types_chambres AS t
                    ON t.id = c.type_chambre_id
                WHERE t.hotel_id = %s
                  AND c.active = TRUE
                  AND t.capacite >= %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM reservations AS r
                      WHERE r.chambre_id = c.id
                        AND r.statut = 'confirmee'
                        AND r.date_arrivee < %s
                        AND r.date_depart > %s
                  )
                ORDER BY t.prix_base, c.numero;
                """,
                (
                    nombre_nuits,
                    HOTEL_ID,
                    demande.nombre_personnes,
                    demande.date_depart,
                    demande.date_arrivee,
                ),
            )

            chambres = cursor.fetchall()

    return {
        "date_arrivee": demande.date_arrivee.isoformat(),
        "date_depart": demande.date_depart.isoformat(),
        "nombre_personnes": demande.nombre_personnes,
        "nombre_nuits": nombre_nuits,
        "devise": "EUR",
        "chambres_disponibles": chambres,
    }


# --------------------------------------------------
# Exécution des seuls outils autorisés
# --------------------------------------------------

def executer_outil(nom: str, arguments: str):
    try:
        if nom == "chercher_disponibilites":
            demande = RechercheDisponibilites.model_validate_json(
                arguments
            )

            return chercher_disponibilites(demande)

        if nom == "preparer_reservation":
            demande = PreparationReservation.model_validate_json(
                arguments
            )

            disponibilites = chercher_disponibilites(demande)

            if "erreur" in disponibilites:
                return disponibilites

            chambre = next(
                (
                    element
                    for element in disponibilites["chambres_disponibles"]
                    if element["id"] == demande.chambre_id
                ),
                None,
            )

            if chambre is None:
                return {
                    "erreur": (
                        "Cette chambre n'est pas disponible "
                        "pour les critères demandés."
                    )
                }

            return {
                "proposition": {
                    "chambre_id": chambre["id"],
                    "numero": chambre["numero"],
                    "type_chambre": chambre["type_chambre"],
                    "date_arrivee": demande.date_arrivee.isoformat(),
                    "date_depart": demande.date_depart.isoformat(),
                    "nombre_personnes": demande.nombre_personnes,
                    "nombre_nuits": disponibilites["nombre_nuits"],
                    "prix_total": str(chambre["prix_total"]),
                    "devise": "EUR",
                }
            }

        return {"erreur": "Outil non autorisé."}

    except ValidationError:
        return {
            "erreur": (
                "Arguments invalides. Fournir les dates au format "
                "AAAA-MM-JJ, un nombre entier de personnes supérieur "
                "à zéro et, si nécessaire, un identifiant de chambre valide."
            )
        }


# --------------------------------------------------
# Route de conversation
# --------------------------------------------------

@router.post("/message")
def discuter(demande: MessageAssistant):
    message = demande.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Le message ne peut pas être vide.",
        )

    cle = os.getenv("OPENROUTER_API_KEY")
    modele = os.getenv("OPENROUTER_MODEL")

    if not cle or not modele:
        raise HTTPException(
            status_code=503,
            detail="La clé ou le modèle OpenRouter manque dans .env.",
        )

    proposition = None
    consigne_suite = (
        "Après préparation, le serveur demandera le nom et le téléphone, "
        "puis un accord explicite. Ne renvoie pas vers un formulaire. "
        if demande.mode == "vocal" else
        "Après préparation, indique que le client devra remplir le formulaire "
        "du site et cliquer sur Confirmer. "
    )

    messages = [
        {
            "role": "system",
            "content": (
                "Tu es l'assistant de l'Hôtel Démo. "
                f"La date du serveur est {date.today().isoformat()}. "
                "Réponds en français, simplement et brièvement. "
                "Les échanges précédents fournissent uniquement "
                "du contexte : ils ne prouvent aucune disponibilité "
                "ni confirmation de réservation. "
                "Pour annoncer des disponibilités ou des prix, "
                "appelle chercher_disponibilites pendant cette requête "
                "et utilise uniquement son résultat. "
                "S'il manque les dates, l'année ou le nombre de voyageurs, "
                "demande une précision au lieu de les inventer. "
                "Si le client choisit explicitement une chambre, "
                "retrouve son identifiant technique dans une recherche "
                "effectuée pendant cette requête, puis appelle "
                "preparer_reservation. "
                "Ne confonds jamais identifiant technique et numéro "
                "de chambre. "
                f"{consigne_suite}"
                "Une proposition ne bloque aucune chambre. "
                "Tu ne peux ni enregistrer ni annuler une réservation. "
                "Ne prétends jamais qu'une réservation est confirmée. "
                "En cas d'erreur d'un outil, explique le problème "
                "sans inventer de résultat. "
                "Ne demande aucune coordonnée personnelle dans le chat. "
                "N'invente aucun service de l'hôtel."
            ),
        },
    ]

    messages.extend(
        {
            "role": element.role,
            "content": element.content,
        }
        for element in demande.historique
    )

    messages.append(
        {
            "role": "user",
            "content": message,
        }
    )

    try:
        with OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=cle,
            timeout=60.0,
            max_retries=0,
        ) as client:
            # Autorise plusieurs étapes :
            # recherche, préparation, puis réponse finale.
            for _ in range(4):
                resultat = client.chat.completions.create(
                    model=modele,
                    messages=messages,
                    tools=OUTILS,
                    tool_choice="auto",
                    max_tokens=1500,
                )

                if not resultat.choices:
                    raise HTTPException(
                        status_code=502,
                        detail="Le modèle n'a retourné aucune réponse.",
                    )

                reponse = resultat.choices[0].message

                if not reponse.tool_calls:
                    if not reponse.content or not reponse.content.strip():
                        raise HTTPException(
                            status_code=502,
                            detail="Le modèle a retourné une réponse vide.",
                        )

                    return {
                        "reponse": reponse.content,
                        "proposition": proposition,
                    }

                if len(reponse.tool_calls) > 4:
                    raise HTTPException(
                        status_code=502,
                        detail="Le modèle a demandé trop d'outils.",
                    )

                messages.append(
                    reponse.model_dump(
                        include={"role", "content", "tool_calls"},
                        exclude_none=True,
                    )
                )

                for appel in reponse.tool_calls:
                    resultat_outil = executer_outil(
                        appel.function.name,
                        appel.function.arguments,
                    )

                    if appel.function.name == "preparer_reservation":
                        proposition = resultat_outil.get("proposition")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": appel.id,
                            "content": json.dumps(
                                resultat_outil,
                                ensure_ascii=False,
                                default=str,
                            ),
                        }
                    )

                if proposition is not None:
                    suite = (
                        "Quel est votre nom complet ?"
                        if demande.mode == "vocal" else
                        "Remplissez le formulaire pour confirmer la réservation."
                    )
                    return {
                        "reponse": (
                            f"La chambre {proposition['numero']} est proposée du "
                            f"{proposition['date_arrivee']} au {proposition['date_depart']}, "
                            f"pour {proposition['nombre_personnes']} personne(s). "
                            f"Total : {proposition['prix_total']} euros. "
                            "Cette proposition ne bloque aucune chambre. " + suite
                        ),
                        "proposition": proposition,
                    }

        raise HTTPException(
            status_code=502,
            detail="L'assistant n'a pas terminé sa réponse. Réessaie.",
        )

    except AuthenticationError:
        raise HTTPException(
            status_code=502,
            detail="OpenRouter refuse la clé API.",
        )

    except RateLimitError:
        raise HTTPException(
            status_code=429,
            detail="Limite OpenRouter atteinte. Réessaie plus tard.",
        )

    except APITimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Le modèle met trop de temps à répondre.",
        )

    except APIConnectionError:
        raise HTTPException(
            status_code=502,
            detail="Connexion à OpenRouter impossible.",
        )

    except APIStatusError as erreur:
        raise HTTPException(
            status_code=502,
            detail=(
                "OpenRouter a refusé la demande "
                f"({erreur.status_code})."
            ),
        )

    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="La consultation de la base de données a échoué.",
        )

# Conversations vocales : l'IA recherche ; le serveur collecte et confirme.
# Les anciennes routes /message et les formulaires restent compatibles.

class MessageConversation(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    request_id: UUID | None = None


class CoordonneesConversation(BaseModel):
    nom: str = Field(min_length=2, max_length=150)
    telephone: str = Field(min_length=8, max_length=80)


class ConfirmationConversation(BaseModel):
    confirme: bool = Field(strict=True)


def normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte.casefold())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texte).split())


# Listes fermées : ne jamais confirmer par simple présence du mot « confirme ».
def accord_explicite(texte: str) -> bool:
    commande = normaliser(texte)
    commande = re.sub(r"^(?:d accord |oui d accord )", "", commande)
    commande = re.sub(r"(?: s il vous plait| merci(?: beaucoup)?)$", "", commande)
    return commande in {
        "oui je confirme", "je confirme", "je confirme la reservation",
        "oui je confirme la reservation", "je confirme ma reservation",
        "oui je confirme ma reservation", "oui c est bon je confirme",
    }


def demande_fin(texte: str) -> bool:
    commande = normaliser(texte)
    return commande in {
        "annuler", "annuler la demande", "arreter", "stop",
        "arreter la conversation", "terminer", "terminer la conversation",
        "fin de conversation", "au revoir", "merci au revoir",
        "c est tout", "c est tout merci", "merci c est tout",
        "non merci au revoir", "je veux arreter", "je veux terminer la conversation",
    }


def lire_telephone(texte: str) -> str | None:
    # Accepte les numéros internationaux et les mobiles français locaux.
    # Une transcription ambiguë est refusée plutôt que devinée.
    texte = texte.strip().lower()
    texte = re.sub(
        r"^(?:mon (?:numero|numéro)(?: de téléphone| de telephone)? est|"
        r"mon téléphone est|mon telephone est|c'est|c’est)\s*:?\s*", "", texte,
    )
    mots = normaliser(texte).split()
    chiffres = {"zero": "0", "un": "1", "une": "1", "deux": "2",
                "trois": "3", "quatre": "4", "cinq": "5", "six": "6",
                "sept": "7", "huit": "8", "neuf": "9"}
    if mots and all(m in chiffres or m.isdigit() for m in mots):
        prefixe = "+" if texte.startswith("+") else ""
        numero = prefixe + "".join(chiffres.get(m, m) for m in mots)
    elif re.fullmatch(r"[+\d\s().-]+", texte):
        numero = re.sub(r"[\s().-]", "", texte)
    else:
        return None
    if numero.startswith("00"):
        numero = "+" + numero[2:]
    if re.fullmatch(r"0[1-9]\d{8}", numero):
        numero = "+33" + numero[1:]
    if numero.startswith("+33") and not re.fullmatch(r"\+33[1-9]\d{8}", numero):
        return None
    return numero if re.fullmatch(r"\+[1-9]\d{7,14}", numero) else None


def lire_nom(texte: str) -> str | None:
    nom = re.sub(
        r"^(?:je m'appelle|je m’appelle|mon nom est|au nom de)\s+", "",
        texte.strip(), flags=re.IGNORECASE,
    ).strip(" .,!?:;")
    if (not 2 <= len(nom) <= 150 or any(c.isdigit() for c in nom)
            or normaliser(nom) in {"oui", "non", "je confirme", "bonjour", "annuler"}
            or not any(c.isalpha() for c in nom)):
        return None
    return nom


def charger_conversation(cursor, conversation_id, verrouiller=True):
    cursor.execute(
        """SELECT historique, proposition, coordonnees_client, etat, reservation_id
           FROM conversations_assistant
           WHERE id = %s AND hotel_id = %s"""
        + (" FOR UPDATE;" if verrouiller else ";"),
        (conversation_id, HOTEL_ID),
    )
    conversation = cursor.fetchone()
    if conversation is None:
        raise HTTPException(404, "Conversation introuvable.")
    return conversation


def euros(montant):
    return f"{Decimal(str(montant)):.2f}".replace(".", ",") + " euros"


def recapitulatif(proposition, coordonnees):
    debut = date.fromisoformat(proposition["date_arrivee"]).strftime("%d/%m/%Y")
    fin = date.fromisoformat(proposition["date_depart"]).strftime("%d/%m/%Y")
    montant = Decimal(str(proposition["prix_total"]))
    moitie = (montant / 2).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
    telephone = " ".join(coordonnees["telephone"])
    return (
        f"Récapitulatif : chambre {proposition['numero']}, du {debut} au {fin}, "
        f"pour {proposition['nombre_personnes']} personne(s), "
        f"au nom de {coordonnees['nom']}. Téléphone : {telephone}. "
        f"Total : {euros(montant)}. La moitié à régler est de {euros(moitie)}, "
        "le solde sera à payer à l'hôtel. "
        "Aucune réservation n'est encore enregistrée. "
        "Pour valider ces informations, dites exactement : Oui, je confirme. "
        "Pour corriger, dites : Modifier la réservation."
    )


def resultat(conversation_id, conversation, reponse, **extra):
    return {
        "conversation_id": str(conversation_id),
        "etat": conversation["etat"],
        "reponse": reponse,
        "proposition": conversation.get("proposition"),
        "reservation_id": conversation.get("reservation_id"),
        "confirmee": conversation["etat"] == "confirmee",
        "terminee": conversation["etat"] in {"confirmee", "abandonnee"},
        **extra,
    }


def sauvegarder(cursor, conversation_id, conversation):
    cursor.execute(
        """UPDATE conversations_assistant
           SET historique = %s, proposition = %s, coordonnees_client = %s,
               etat = %s, reservation_id = %s, modifie_le = CURRENT_TIMESTAMP
           WHERE id = %s AND hotel_id = %s;""",
        (Jsonb(conversation["historique"]),
         Jsonb(conversation["proposition"]) if conversation["proposition"] else None,
         Jsonb(conversation["coordonnees_client"]) if conversation["coordonnees_client"] else None,
         conversation["etat"], conversation["reservation_id"], conversation_id, HOTEL_ID),
    )


def confirmer_avec_curseur(cursor, conversation_id, conversation):
    # Le verrou de conversation et la contrainte anti-chevauchement SQL
    # garantissent une seule réservation, y compris en cas de double requête.
    if conversation["etat"] == "confirmee":
        return resultat(conversation_id, conversation,
                        f"Votre réservation est déjà confirmée. Référence {conversation['reservation_id']}.")
    if conversation["etat"] != "attente_confirmation":
        raise HTTPException(409, "Les coordonnées et le récapitulatif sont nécessaires.")
    proposition = conversation["proposition"]
    coordonnees = conversation["coordonnees_client"]
    recherche = RechercheDisponibilites.model_validate(proposition)
    if recherche.date_arrivee < date.today() or recherche.date_depart <= recherche.date_arrivee:
        raise HTTPException(409, "Les dates ne sont plus valides. Recommencez la recherche.")
    cursor.execute(
        """SELECT t.capacite, t.prix_base FROM chambres AS c
           JOIN types_chambres AS t ON t.id = c.type_chambre_id
           WHERE c.id = %s AND t.hotel_id = %s AND c.active = TRUE
           FOR SHARE OF c, t;""", (proposition["chambre_id"], HOTEL_ID),
    )
    chambre = cursor.fetchone()
    if chambre is None or chambre["capacite"] < recherche.nombre_personnes:
        raise HTTPException(409, "Cette chambre n'est plus adaptée. Recommencez la recherche.")
    prix_total = Decimal(str(chambre["prix_base"])) * (recherche.date_depart - recherche.date_arrivee).days
    if prix_total != Decimal(str(proposition["prix_total"])):
        raise HTTPException(409, "Le tarif a changé. Recherchez et confirmez une nouvelle proposition.")
    cursor.execute(
        """INSERT INTO clients (hotel_id, nom, telephone)
           VALUES (%s, %s, %s) RETURNING id;""",
        (HOTEL_ID, coordonnees["nom"], coordonnees["telephone"]),
    )
    client_id = cursor.fetchone()["id"]
    cursor.execute(
        """INSERT INTO reservations
           (chambre_id, client_id, date_arrivee, date_depart,
            nombre_personnes, prix_total, statut)
           VALUES (%s, %s, %s, %s, %s, %s, 'confirmee') RETURNING id;""",
        (proposition["chambre_id"], client_id, recherche.date_arrivee,
         recherche.date_depart, recherche.nombre_personnes, prix_total),
    )
    reference = cursor.fetchone()["id"]
    # Écrase aussi une ancienne valeur J-10 produite par un éventuel trigger.
    cursor.execute(
        """UPDATE reservations SET montant_a_payer = ROUND(prix_total * 0.50, 2),
           statut_paiement = 'en_attente', date_envoi_paiement = CURRENT_DATE
           WHERE id = %s;""", (reference,),
    )
    cursor.execute(
        """INSERT INTO envois_sms (reservation_id, type_message, statut)
           VALUES (%s, 'rappel_paiement', 'prepare')
           ON CONFLICT (reservation_id, type_message) DO NOTHING;""", (reference,),
    )
    conversation["etat"] = "confirmee"
    conversation["reservation_id"] = reference
    moitie = (prix_total / 2).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
    return resultat(
        conversation_id, conversation,
        f"Votre réservation est confirmée. Votre référence est {reference}. "
        f"Le total est de {euros(prix_total)}. Le montant à régler est de {euros(moitie)}, "
        "soit la moitié du séjour. Le solde sera à payer à l'hôtel. "
        "La demande de SMS de paiement est préparée. Merci et au revoir.",
        sms_statut="prepare",
    )


@router.post("/conversations", status_code=201)
def creer_conversation():
    conversation_id = uuid4()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO conversations_assistant (id, hotel_id, etat)
                   VALUES (%s, %s, 'discussion');""", (conversation_id, HOTEL_ID),
            )
    return {
        "conversation_id": str(conversation_id), "etat": "discussion",
        "message": "Bonjour, pour quelles dates et combien de voyageurs cherchez-vous une chambre ?",
    }


@router.get("/conversations/{conversation_id}")
def recuperer_conversation(conversation_id: UUID):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            conversation = charger_conversation(cursor, conversation_id, False)
    historique = conversation["historique"] or []
    # Reprise après une interruption du microphone ou une réponse réseau perdue.
    if (historique and historique[-1].get("resultat")
            and conversation["etat"] not in {"confirmee", "abandonnee"}):
        return {**historique[-1]["resultat"], "terminee": False}
    if conversation["etat"] == "confirmee":
        texte = f"Votre réservation est confirmée. Référence {conversation['reservation_id']}."
    elif conversation["etat"] == "attente_confirmation":
        texte = recapitulatif(conversation["proposition"], conversation["coordonnees_client"])
    elif conversation["etat"] == "collecte_coordonnees":
        texte = "Quel est votre numéro de téléphone ?" if (conversation["coordonnees_client"] or {}).get("nom") else "Quel est votre nom complet ?"
    elif conversation["etat"] == "abandonnee":
        texte = "Cette conversation est terminée. Démarrez une nouvelle conversation."
    else:
        texte = "Bonjour, indiquez vos dates de séjour et le nombre de voyageurs."
    return resultat(conversation_id, conversation, texte)


@router.post("/conversations/{conversation_id}/messages")
def envoyer_message_conversation(conversation_id: UUID, demande: MessageConversation):
    texte = demande.message.strip()
    if not texte:
        raise HTTPException(400, "Le message ne peut pas être vide.")
    commande = normaliser(texte)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            conversation = charger_conversation(cursor, conversation_id)
            historique = conversation["historique"] or []
            # Rejouer la même requête ne fait pas avancer deux fois la collecte.
            if demande.request_id:
                for entree in historique:
                    if entree.get("request_id") == str(demande.request_id):
                        if entree["texte_requete"] != texte:
                            raise HTTPException(409, "Cet identifiant de requête a déjà été utilisé.")
                        return entree["resultat"]
            etat = conversation["etat"]
            if etat == "confirmee":
                reponse = resultat(conversation_id, conversation,
                    f"Votre réservation est déjà confirmée. Référence {conversation['reservation_id']}.")
            elif etat == "abandonnee":
                reponse = resultat(conversation_id, conversation, "Cette conversation est terminée.")
            elif demande_fin(texte):
                conversation["etat"] = "abandonnee"
                reponse = resultat(conversation_id, conversation, "La conversation est terminée. Aucune réservation n'a été enregistrée. Au revoir.")
            elif commande in {"modifier la reservation", "modifier", "changer les dates", "corriger"}:
                conversation.update(etat="discussion", proposition=None, coordonnees_client=None)
                historique = []  # Les coordonnées ne repartent pas vers le modèle.
                reponse = resultat(conversation_id, conversation,
                    "Reprenons la demande. Indiquez vos dates et le nombre de voyageurs.")
            elif etat == "collecte_coordonnees":
                coordonnees = conversation["coordonnees_client"] or {}
                if not coordonnees.get("nom"):
                    nom = lire_nom(texte)
                    if nom:
                        coordonnees = {"nom": nom}
                        conversation["coordonnees_client"] = coordonnees
                        question = f"Merci {nom}. Quel est votre numéro de téléphone ? Dictez-le chiffre par chiffre, avec l'indicatif pays si nécessaire."
                    else:
                        question = "Indiquez seulement votre nom et votre prénom, ou dites Modifier la réservation."
                    reponse = resultat(conversation_id, conversation, question)
                else:
                    telephone = lire_telephone(texte)
                    if telephone:
                        coordonnees["telephone"] = telephone
                        conversation["coordonnees_client"] = coordonnees
                        conversation["etat"] = "attente_confirmation"
                        question = recapitulatif(conversation["proposition"], coordonnees)
                    else:
                        question = "Je n'ai pas reconnu un numéro valide. Dictez les chiffres séparément. Pour un numéro hors France, commencez par zéro zéro puis l'indicatif pays."
                    reponse = resultat(conversation_id, conversation, question)
            elif etat == "attente_confirmation":
                if accord_explicite(texte):
                    try:
                        # Sous-transaction : un conflit annule client + réservation.
                        with connection.transaction():
                            reponse = confirmer_avec_curseur(cursor, conversation_id, conversation)
                    except (ExclusionViolation, HTTPException) as erreur:
                        if isinstance(erreur, HTTPException) and erreur.status_code != 409:
                            raise
                        conversation.update(etat="discussion", proposition=None, coordonnees_client=None, reservation_id=None)
                        historique = []
                        explication = erreur.detail if isinstance(erreur, HTTPException) else "Cette chambre vient d'être réservée sur ces dates."
                        reponse = resultat(conversation_id, conversation,
                            f"{explication} Aucune réservation n'a été créée. Indiquez à nouveau vos dates et le nombre de voyageurs.")
                elif commande in {"non", "non merci", "je ne confirme pas"}:
                    conversation.update(etat="discussion", proposition=None, coordonnees_client=None)
                    historique = []
                    reponse = resultat(conversation_id, conversation,
                        "Aucune réservation n'a été créée. Indiquez les nouveaux critères, ou dites Annuler.")
                else:
                    reponse = resultat(conversation_id, conversation,
                        "Je n'ai pas enregistré de réservation. Dites Oui, je confirme pour valider le récapitulatif, ou Modifier la réservation pour le corriger.")
            else:
                # Seule la recherche passe par OpenRouter. La collecte et l'accord
                # sont traités localement après proposition.
                contexte = [MessageHistorique(role=e["role"], content=e["content"]) for e in historique[-20:]]
                sortie = discuter(MessageAssistant(message=texte, historique=contexte, mode="vocal"))
                if sortie.get("proposition"):
                    conversation.update(etat="collecte_coordonnees", proposition=sortie["proposition"], coordonnees_client=None)
                    p = conversation["proposition"]
                    question = (
                        f"La chambre {p['numero']} est proposée du {p['date_arrivee']} "
                        f"au {p['date_depart']} pour {p['nombre_personnes']} personne(s), "
                        f"au total {euros(p['prix_total'])}. Elle n'est pas encore réservée. "
                        "Quel est votre nom complet ?"
                    )
                    reponse = resultat(conversation_id, conversation, question)
                else:
                    reponse = resultat(conversation_id, conversation, sortie["reponse"])
            historique.extend([
                {"role": "user", "content": texte},
                {"role": "assistant", "content": reponse["reponse"],
                 "request_id": str(demande.request_id) if demande.request_id else None,
                 "texte_requete": texte, "resultat": reponse},
            ])
            conversation["historique"] = historique[-20:]
            sauvegarder(cursor, conversation_id, conversation)
    return reponse


@router.post("/conversations/{conversation_id}/coordonnees")
def enregistrer_coordonnees(conversation_id: UUID, demande: CoordonneesConversation):
    nom, telephone = lire_nom(demande.nom), lire_telephone(demande.telephone)
    if not nom or not telephone:
        raise HTTPException(422, "Nom ou numéro de téléphone invalide.")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            conversation = charger_conversation(cursor, conversation_id)
            if conversation["etat"] not in {"collecte_coordonnees", "attente_confirmation"}:
                raise HTTPException(409, "Préparez d'abord une proposition.")
            conversation["coordonnees_client"] = {"nom": nom, "telephone": telephone}
            conversation["etat"] = "attente_confirmation"
            # Invalide l'ancienne question de reprise.
            conversation["historique"] = []
            sauvegarder(cursor, conversation_id, conversation)
    return resultat(conversation_id, conversation, recapitulatif(conversation["proposition"], conversation["coordonnees_client"]))


@router.post("/conversations/{conversation_id}/confirmer")
def confirmer_conversation(conversation_id: UUID, demande: ConfirmationConversation):
    if not demande.confirme:
        return {"confirmee": False, "reponse": "Aucune nouvelle réservation n'a été enregistrée."}
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                conversation = charger_conversation(cursor, conversation_id)
                reponse = confirmer_avec_curseur(cursor, conversation_id, conversation)
                conversation["historique"] = []
                sauvegarder(cursor, conversation_id, conversation)
        return reponse
    except ExclusionViolation:
        raise HTTPException(409, "La chambre vient d'être réservée. Choisissez une nouvelle proposition.")
