from fastapi import FastAPI
from assistant_ia import router as assistant_router
from sms import router as sms_router
from paiements import router as paiements_router
from datetime import date
from pydantic import BaseModel, Field
from psycopg.errors import ExclusionViolation

from fastapi import FastAPI, HTTPException, Query

from database import get_connection

app = FastAPI(title="API Assistant Hôtel")
app.include_router(assistant_router)
app.include_router(paiements_router)
app.include_router(sms_router)

@app.get("/")
def accueil():
    return {"message": "Le serveur de réservation fonctionne"}


@app.get("/hotels/{hotel_id}/chambres")
def lister_chambres(hotel_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.id,
                    c.numero,
                    t.nom AS type_chambre,
                    t.capacite,
                    t.prix_base,
                    c.active
                FROM chambres AS c
                JOIN types_chambres AS t
                    ON t.id = c.type_chambre_id
                WHERE t.hotel_id = %s
                ORDER BY c.numero;
                """,
                (hotel_id,),
            )
            return cursor.fetchall()
@app.get("/hotels/{hotel_id}/disponibilites")
def chercher_disponibilites(
    hotel_id: int,
    date_arrivee: date,
    date_depart: date,
    nombre_personnes: int = Query(ge=1),
):
    if date_depart <= date_arrivee:
        raise HTTPException(
            status_code=400,
            detail="La date de départ doit être après la date d'arrivée.",
        )

    nombre_nuits = (date_depart - date_arrivee).days

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
                    hotel_id,
                    nombre_personnes,
                    date_depart,
                    date_arrivee,
                ),
            )
            chambres = cursor.fetchall()

    return {
        "hotel_id": hotel_id,
        "date_arrivee": date_arrivee,
        "date_depart": date_depart,
        "nombre_nuits": nombre_nuits,
        "nombre_personnes": nombre_personnes,
        "chambres_disponibles": chambres,
    }
class ReservationCreation(BaseModel):
    chambre_id: int = Field(gt=0)
    client_id: int = Field(gt=0)
    date_arrivee: date
    date_depart: date
    nombre_personnes: int = Field(gt=0)


@app.post("/hotels/{hotel_id}/reservations", status_code=201)
def creer_reservation(hotel_id: int, demande: ReservationCreation):
    if demande.date_arrivee < date.today():
        raise HTTPException(
            status_code=400,
            detail="La date d'arrivée ne peut pas être passée.",
        )

    if demande.date_depart <= demande.date_arrivee:
        raise HTTPException(
            status_code=400,
            detail="La date de départ doit être après l'arrivée.",
        )

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
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
                    (demande.chambre_id, hotel_id),
                )
                chambre = cursor.fetchone()

                if chambre is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Chambre introuvable ou inactive dans cet hôtel.",
                    )

                if demande.nombre_personnes > chambre["capacite"]:
                    raise HTTPException(
                        status_code=400,
                        detail="La capacité de la chambre est dépassée.",
                    )

                cursor.execute(
                    """
                    SELECT id
                    FROM clients
                    WHERE id = %s AND hotel_id = %s
                    FOR SHARE;
                    """,
                    (demande.client_id, hotel_id),
                )

                if cursor.fetchone() is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Client introuvable dans cet hôtel.",
                    )

                nuits = (
                    demande.date_depart - demande.date_arrivee
                ).days
                prix_total = chambre["prix_base"] * nuits

                cursor.execute(
                    """
                    INSERT INTO reservations (
                        chambre_id, client_id,
                        date_arrivee, date_depart,
                        nombre_personnes, prix_total
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *;
                    """,
                    (
                        demande.chambre_id,
                        demande.client_id,
                        demande.date_arrivee,
                        demande.date_depart,
                        demande.nombre_personnes,
                        prix_total,
                    ),
                )
                reservation = cursor.fetchone()

        return reservation

    except ExclusionViolation:
        raise HTTPException(
            status_code=409,
            detail="Cette chambre est déjà réservée sur ces dates.",
        )
@app.get("/hotels/{hotel_id}/reservations")
def lister_reservations(hotel_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.id,
                    c.numero AS numero_chambre,
                    t.nom AS type_chambre,
                    cl.nom AS nom_client,
                    r.date_arrivee,
                    r.date_depart,
                    r.date_depart - r.date_arrivee AS nombre_nuits,
                    r.nombre_personnes,
                    r.prix_total,
                    r.statut
                FROM reservations AS r
                JOIN chambres AS c ON c.id = r.chambre_id
                JOIN types_chambres AS t ON t.id = c.type_chambre_id
                JOIN clients AS cl ON cl.id = r.client_id
                WHERE t.hotel_id = %s
                  AND cl.hotel_id = t.hotel_id
                ORDER BY r.date_arrivee, r.id;
                """,
                (hotel_id,),
            )
            return cursor.fetchall()

@app.patch("/hotels/{hotel_id}/reservations/{reservation_id}/annuler")
def annuler_reservation(hotel_id: int, reservation_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reservations AS r
                SET statut = 'annulee'
                FROM chambres AS c
                JOIN types_chambres AS t
                    ON t.id = c.type_chambre_id
                WHERE r.chambre_id = c.id
                  AND r.id = %s
                  AND t.hotel_id = %s
                RETURNING r.*;
                """,
                (reservation_id, hotel_id),
            )
            reservation = cursor.fetchone()

            if reservation is None:
                raise HTTPException(
                    status_code=404,
                    detail="Réservation introuvable dans cet hôtel.",
                )

    return reservation
class ClientCreation(BaseModel):
    nom: str = Field(min_length=1, max_length=150)
    telephone: str = Field(min_length=1, max_length=30)
    email: str | None = Field(default=None, max_length=254)


@app.post("/hotels/{hotel_id}/clients", status_code=201)
def creer_client(hotel_id: int, demande: ClientCreation):
    nom = demande.nom.strip()
    telephone = demande.telephone.strip()
    email = demande.email.strip() if demande.email else None

    if not nom or not telephone:
        raise HTTPException(
            status_code=400,
            detail="Le nom et le téléphone sont obligatoires.",
        )

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM hotels WHERE id = %s FOR KEY SHARE;",
                (hotel_id,),
            )

            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Hôtel introuvable.",
                )

            cursor.execute(
                """
                INSERT INTO clients (hotel_id, nom, telephone, email)
                VALUES (%s, %s, %s, %s)
                RETURNING id, hotel_id, nom, telephone, email, cree_le;
                """,
                (hotel_id, nom, telephone, email or None),
            )
            client = cursor.fetchone()

    return client
class ReservationAvecClient(BaseModel):
    client: ClientCreation
    chambre_id: int = Field(gt=0)
    date_arrivee: date
    date_depart: date
    nombre_personnes: int = Field(gt=0)


@app.post("/hotels/{hotel_id}/reservations-completes", status_code=201)
def creer_reservation_complete(
    hotel_id: int,
    demande: ReservationAvecClient,
):
    if demande.date_arrivee < date.today():
        raise HTTPException(
            status_code=400,
            detail="La date d'arrivée ne peut pas être passée.",
        )

    if demande.date_depart <= demande.date_arrivee:
        raise HTTPException(
            status_code=400,
            detail="La date de départ doit être après l'arrivée.",
        )

    nom = demande.client.nom.strip()
    telephone = demande.client.telephone.strip()
    email = (
        demande.client.email.strip()
        if demande.client.email
        else None
    )

    if not nom or not telephone:
        raise HTTPException(
            status_code=400,
            detail="Le nom et le téléphone sont obligatoires.",
        )

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
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
                    (demande.chambre_id, hotel_id),
                )
                chambre = cursor.fetchone()

                if chambre is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Chambre introuvable ou inactive dans cet hôtel.",
                    )

                if demande.nombre_personnes > chambre["capacite"]:
                    raise HTTPException(
                        status_code=400,
                        detail="La capacité de la chambre est dépassée.",
                    )

                cursor.execute(
                    """
                    INSERT INTO clients (
                        hotel_id, nom, telephone, email
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (hotel_id, nom, telephone, email or None),
                )
                client = cursor.fetchone()

                nombre_nuits = (
                    demande.date_depart - demande.date_arrivee
                ).days
                prix_total = chambre["prix_base"] * nombre_nuits

                cursor.execute(
                    """
                    INSERT INTO reservations (
                        chambre_id,
                        client_id,
                        date_arrivee,
                        date_depart,
                        nombre_personnes,
                        prix_total
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *;
                    """,
                    (
                        demande.chambre_id,
                        client["id"],
                        demande.date_arrivee,
                        demande.date_depart,
                        demande.nombre_personnes,
                        prix_total,
                    ),
                )
                reservation = cursor.fetchone()

        # La transaction est validée en sortant du bloc "with".
        return reservation

    except ExclusionViolation:
        # La transaction a été annulée, y compris le nouveau client.
        raise HTTPException(
            status_code=409,
            detail="Cette chambre est déjà réservée sur ces dates.",
        )