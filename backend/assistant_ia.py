import json
import os
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import uuid4
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
                "Après préparation, présente le récapitulatif et indique "
                "que le client devra remplir le formulaire du site "
                "et cliquer sur Confirmer. "
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

@router.post("/conversations", status_code=201)
def creer_conversation():
    conversation_id = uuid4()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO conversations_assistant (
                    id,
                    hotel_id
                )
                VALUES (%s, %s)
                RETURNING id, etat;
                """,
                (conversation_id, HOTEL_ID),
            )

            conversation = cursor.fetchone()

    return {
        "conversation_id": str(conversation["id"]),
        "etat": conversation["etat"],
        "message": "Bonjour, pour quelles dates cherchez-vous une chambre ?",
    }
class MessageConversation(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/conversations/{conversation_id}/messages")
def envoyer_message_conversation(
    conversation_id: UUID,
    demande: MessageConversation,
):
    texte = demande.message.strip()

    if not texte:
        raise HTTPException(
            status_code=400,
            detail="Le message ne peut pas être vide.",
        )

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT historique, etat, proposition, coordonnees_client
                FROM conversations_assistant
                WHERE id = %s AND hotel_id = %s
                FOR UPDATE;
                """,
                (conversation_id, HOTEL_ID),
            )

            conversation = cursor.fetchone()

            if conversation is None:
                raise HTTPException(
                    status_code=404,
                    detail="Conversation introuvable.",
                )

            if conversation["etat"] in (
                "confirmee",
                "abandonnee",
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Cette conversation est terminée.",
                )

            historique = conversation["historique"]

            resultat = discuter(
                MessageAssistant(
                    message=texte,
                    historique=historique[-20:],
                )
            )

            nouveaux_messages = [
                *historique,
                {"role": "user", "content": texte},
                {
                    "role": "assistant",
                    "content": resultat["reponse"],
                },
            ][-20:]
            nouvelle_proposition = resultat.get("proposition")

            # Une nouvelle proposition remplace l'ancienne.
            # Sans nouvelle proposition, on conserve celle enregistrée.
            if nouvelle_proposition is not None:
                proposition_json = json.loads(
                    json.dumps(nouvelle_proposition, default=str)
                )
                coordonnees = None
                nouvel_etat = "collecte_coordonnees"
            else:
                proposition_json = conversation["proposition"]
                coordonnees = conversation["coordonnees_client"]
                nouvel_etat = conversation["etat"]

            cursor.execute(
                """
                UPDATE conversations_assistant
                SET historique = %s,
                    proposition = %s,
                    coordonnees_client = %s,
                    etat = %s,
                    modifie_le = CURRENT_TIMESTAMP
                WHERE id = %s AND hotel_id = %s;
                """,
                (
                    Jsonb(nouveaux_messages),
                    (
                        Jsonb(proposition_json)
                        if proposition_json is not None
                        else None
                    ),
                    Jsonb(coordonnees) if coordonnees is not None else None,
                    nouvel_etat,
                    conversation_id,
                    HOTEL_ID,
                ),
            )

    return {
        "conversation_id": str(conversation_id),
        "reponse": resultat["reponse"],
        "proposition": proposition_json,
        "etat": nouvel_etat,
    }
class CoordonneesConversation(BaseModel):
    nom: str = Field(min_length=1, max_length=150)
    telephone: str = Field(min_length=1, max_length=30)


@router.post("/conversations/{conversation_id}/coordonnees")
def enregistrer_coordonnees(
    conversation_id: UUID,
    demande: CoordonneesConversation,
):
    nom = demande.nom.strip()
    telephone = demande.telephone.strip()

    if not nom or not telephone:
        raise HTTPException(
            status_code=400,
            detail="Le nom et le téléphone sont obligatoires.",
        )

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT proposition, etat
                FROM conversations_assistant
                WHERE id = %s AND hotel_id = %s
                FOR UPDATE;
                """,
                (conversation_id, HOTEL_ID),
            )
            conversation = cursor.fetchone()

            if conversation is None:
                raise HTTPException(
                    status_code=404,
                    detail="Conversation introuvable.",
                )

            if conversation["etat"] not in (
                "collecte_coordonnees",
                "attente_confirmation",
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Préparez d'abord une proposition de réservation.",
                )

            proposition = conversation["proposition"]

            if proposition is None:
                raise HTTPException(
                    status_code=409,
                    detail="Aucune proposition à confirmer.",
                )

            coordonnees = {
                "nom": nom,
                "telephone": telephone,
            }

            recapitulatif = (
                f"Réservation au nom de {nom}, "
                f"téléphone {telephone}. "
                f"Chambre {proposition['numero']}, "
                f"du {proposition['date_arrivee']} "
                f"au {proposition['date_depart']}, "
                f"pour {proposition['nombre_personnes']} personne(s). "
                f"Montant proposé : {proposition['prix_total']} euros. "
                "Confirmez-vous cette réservation ?"
            )

            cursor.execute(
                """
                UPDATE conversations_assistant
                SET coordonnees_client = %s,
                    etat = 'attente_confirmation',
                    modifie_le = CURRENT_TIMESTAMP
                WHERE id = %s AND hotel_id = %s;
                """,
                (
                    Jsonb(coordonnees),
                    conversation_id,
                    HOTEL_ID,
                ),
            )

    return {
        "conversation_id": str(conversation_id),
        "etat": "attente_confirmation",
        "reponse": recapitulatif,
    }
class ConfirmationConversation(BaseModel):
    confirme: bool = Field(strict=True)


@router.post("/conversations/{conversation_id}/confirmer")
def confirmer_conversation(
    conversation_id: UUID,
    demande: ConfirmationConversation,
):
    if not demande.confirme:
        return {
            "reponse": "Aucune réservation n'a été enregistrée.",
            "confirmee": False,
        }

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                # Une seule confirmation à la fois par conversation.
                cursor.execute(
                    """
                    SELECT proposition, coordonnees_client,
                           etat, reservation_id
                    FROM conversations_assistant
                    WHERE id = %s AND hotel_id = %s
                    FOR UPDATE;
                    """,
                    (conversation_id, HOTEL_ID),
                )
                conversation = cursor.fetchone()

                if conversation is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Conversation introuvable.",
                    )

                # Un deuxième appel retourne la même référence.
                if conversation["etat"] == "confirmee":
                    reference = conversation["reservation_id"]

                    return {
                        "confirmee": True,
                        "reservation_id": reference,
                        "reponse": (
                            "Votre réservation est déjà confirmée. "
                            f"Votre référence est {reference}."
                        ),
                    }

                if conversation["etat"] != "attente_confirmation":
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Les coordonnées et le récapitulatif "
                            "doivent être préparés avant confirmation."
                        ),
                    )

                proposition = conversation["proposition"]
                coordonnees = conversation["coordonnees_client"]

                recherche = RechercheDisponibilites(
                    date_arrivee=proposition["date_arrivee"],
                    date_depart=proposition["date_depart"],
                    nombre_personnes=proposition["nombre_personnes"],
                )

                if (
                    recherche.date_arrivee < date.today()
                    or recherche.date_depart <= recherche.date_arrivee
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Les dates ne sont plus valides.",
                    )

                cursor.execute(
                    """
                    SELECT t.capacite, t.prix_base
                    FROM chambres AS c
                    JOIN types_chambres AS t
                        ON t.id = c.type_chambre_id
                    WHERE c.id = %s
                      AND t.hotel_id = %s
                      AND c.active = TRUE
                    FOR SHARE OF c, t;
                    """,
                    (proposition["chambre_id"], HOTEL_ID),
                )
                chambre = cursor.fetchone()

                if chambre is None:
                    raise HTTPException(
                        status_code=409,
                        detail="Cette chambre n'est plus proposée.",
                    )

                if recherche.nombre_personnes > chambre["capacite"]:
                    raise HTTPException(
                        status_code=409,
                        detail="La capacité de la chambre est insuffisante.",
                    )

                nuits = (
                    recherche.date_depart - recherche.date_arrivee
                ).days
                prix_total = chambre["prix_base"] * nuits

                if prix_total != Decimal(str(proposition["prix_total"])):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Le tarif a changé. Préparez une nouvelle "
                            "proposition et confirmez le nouveau prix."
                        ),
                    )

                cursor.execute(
                    """
                    INSERT INTO clients (hotel_id, nom, telephone)
                    VALUES (%s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        HOTEL_ID,
                        coordonnees["nom"],
                        coordonnees["telephone"],
                    ),
                )
                client_id = cursor.fetchone()["id"]

                # La contrainte PostgreSQL refuse tout chevauchement.
                cursor.execute(
                    """
                    INSERT INTO reservations (
                        chambre_id, client_id,
                        date_arrivee, date_depart,
                        nombre_personnes, prix_total
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        proposition["chambre_id"],
                        client_id,
                        recherche.date_arrivee,
                        recherche.date_depart,
                        recherche.nombre_personnes,
                        prix_total,
                    ),
                )
                reservation_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    UPDATE reservations
                    SET montant_a_payer = ROUND(prix_total * 0.50, 2),
                        statut_paiement = 'en_attente',
                        date_envoi_paiement = GREATEST(
                            date_arrivee - 10,
                            CURRENT_DATE
                        )
                    WHERE id = %s;
                    """,
                    (reservation_id,),
                )

                cursor.execute(
                    """
                    UPDATE conversations_assistant
                    SET etat = 'confirmee',
                        reservation_id = %s,
                        modifie_le = CURRENT_TIMESTAMP
                    WHERE id = %s AND hotel_id = %s;
                    """,
                    (reservation_id, conversation_id, HOTEL_ID),
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
                        'prepare'
                    )
                    ON CONFLICT (
                        reservation_id,
                        type_message
                    )
                    DO NOTHING;
                    """,
                    (reservation_id,),
                )

        # Le client, la réservation et l'état sont validés ensemble.
        return {
            "confirmee": True,
            "reservation_id": reservation_id,
            "reponse": (
                "Votre réservation est confirmée. "
                f"Votre référence est {reservation_id}. "
                "Paiement à l'hôtel."
            ),
        }

    except ExclusionViolation:
        raise HTTPException(
            status_code=409,
            detail=(
                "La chambre vient d'être réservée sur ces dates. "
                "Veuillez choisir une autre proposition."
            ),
        )