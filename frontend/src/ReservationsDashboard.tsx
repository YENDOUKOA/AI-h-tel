import { useEffect, useRef, useState } from "react";

type Reservation = {
  id: number;
  numero_chambre: string;
  type_chambre: string;
  nom_client: string;
  date_arrivee: string;
  date_depart: string;
  nombre_nuits: number;
  nombre_personnes: number;
  prix_total: number;
  statut: "confirmee" | "annulee";
};

function afficherDate(valeur: string) {
  const [annee, mois, jour] = valeur.split("-");
  return `${jour}/${mois}/${annee}`;
}

export default function ReservationsDashboard() {
  const [reservations, setReservations] = useState<Reservation[]>([]);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState("");
  const [message, setMessage] = useState("");
  const [annulationId, setAnnulationId] = useState<number | null>(null);
  const [actualisation, setActualisation] = useState(0);

  const verrouAnnulation = useRef(false);

  useEffect(() => {
    const controller = new AbortController();

    async function charger() {
      setChargement(true);
      setErreur("");

      try {
        const response = await fetch("/api/hotels/1/reservations", {
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(
            `Impossible de charger les réservations (${response.status}).`
          );
        }

        const data: Reservation[] = await response.json();

        if (!controller.signal.aborted) {
          setReservations(data);
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setErreur(
            error instanceof Error
              ? error.message
              : "Impossible de contacter le serveur."
          );
        }
      } finally {
        if (!controller.signal.aborted) {
          setChargement(false);
        }
      }
    }

    charger();

    return () => controller.abort();
  }, [actualisation]);

  async function annuler(reservation: Reservation) {
    if (verrouAnnulation.current) return;

    const confirmation = window.confirm(
      `Annuler la réservation n° ${reservation.id} de ${reservation.nom_client} ?`
    );

    if (!confirmation) return;

    verrouAnnulation.current = true;
    setAnnulationId(reservation.id);
    setErreur("");
    setMessage("");

    try {
      const response = await fetch(
        `/api/hotels/1/reservations/${reservation.id}/annuler`,
        { method: "PATCH" }
      );

      if (!response.ok) {
        const data = await response.json().catch(() => null);

        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : `Annulation impossible (${response.status}).`
        );
      }

      setReservations((precedentes) =>
        precedentes.map((element) =>
          element.id === reservation.id
            ? { ...element, statut: "annulee" }
            : element
        )
      );

      setMessage(`La réservation n° ${reservation.id} a été annulée.`);
    } catch (error) {
      setErreur(
        error instanceof Error
          ? error.message
          : "Impossible de contacter le serveur."
      );
    } finally {
      verrouAnnulation.current = false;
      setAnnulationId(null);
    }
  }

  return (
    <section aria-labelledby="titre-reservations">
      <div className="dashboard-entete">
        <div>
          <h2 id="titre-reservations">Réservations de l’hôtel</h2>
          <p>Consultez les séjours et gérez les annulations.</p>
        </div>

        <button
          type="button"
          disabled={chargement || annulationId !== null}
          onClick={() => {
            setMessage("");
            setActualisation((valeur) => valeur + 1);
          }}
        >
          Actualiser
        </button>
      </div>

      {erreur && (
        <p className="message-erreur" role="alert">
          {erreur}
        </p>
      )}

      {message && (
        <p className="message-succes" role="status">
          {message}
        </p>
      )}

      {chargement && <p role="status">Chargement des réservations…</p>}

      {!chargement && !erreur && reservations.length === 0 && (
        <p>Aucune réservation enregistrée.</p>
      )}

      {!chargement && reservations.length > 0 && (
        <div className="table-container">
          <table className="reservations-table">
            <caption>Liste des réservations — Hôtel Démo</caption>

            <thead>
              <tr>
                <th scope="col">Référence</th>
                <th scope="col">Client</th>
                <th scope="col">Chambre</th>
                <th scope="col">Séjour</th>
                <th scope="col">Personnes</th>
                <th scope="col">Total</th>
                <th scope="col">Statut</th>
                <th scope="col">Action</th>
              </tr>
            </thead>

            <tbody>
              {reservations.map((reservation) => (
                <tr key={reservation.id}>
                  <th scope="row">#{reservation.id}</th>

                  <td>{reservation.nom_client}</td>

                  <td>
                    {reservation.numero_chambre}
                    <small>{reservation.type_chambre}</small>
                  </td>

                  <td>
                    {afficherDate(reservation.date_arrivee)}
                    {" → "}
                    {afficherDate(reservation.date_depart)}
                    <small>{reservation.nombre_nuits} nuit(s)</small>
                  </td>

                  <td>{reservation.nombre_personnes}</td>

                  <td>
                    {reservation.prix_total.toLocaleString("fr-FR", {
                      style: "currency",
                      currency: "EUR",
                    })}
                  </td>

                  <td>
                    <span
                      className={`statut statut-${reservation.statut}`}
                    >
                      {reservation.statut === "confirmee"
                        ? "Confirmée"
                        : "Annulée"}
                    </span>
                  </td>

                  <td>
                    {reservation.statut === "confirmee" ? (
                      <button
                        type="button"
                        className="bouton-danger"
                        disabled={annulationId !== null}
                        onClick={() => annuler(reservation)}
                        aria-label={`Annuler la réservation ${reservation.id}`}
                      >
                        {annulationId === reservation.id
                          ? "Annulation…"
                          : "Annuler"}
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}