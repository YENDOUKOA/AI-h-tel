import { useRef, useState } from "react";
import type { FormEvent } from "react";

type Props = {
  chambreId: number;
  numero: string;
  arrivee: string;
  depart: string;
  personnes: number;
  prixTotal: number;
  onEnvoiChange: (envoi: boolean) => void;
  onFermer: () => void;
  onConfirmee: (id: number, prixTotal: number) => void;
};

type ReservationCreee = {
  id: number;
  prix_total: number;
};

function afficherDate(valeur: string) {
  const [annee, mois, jour] = valeur.split("-");
  return `${jour}/${mois}/${annee}`;
}

export default function ReservationForm(props: Props) {
  const [nom, setNom] = useState("");
  const [telephone, setTelephone] = useState("");
  const [email, setEmail] = useState("");

  const [envoi, setEnvoi] = useState(false);
  const [erreur, setErreur] = useState("");

  const verrouEnvoi = useRef(false);

  async function confirmer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (verrouEnvoi.current) return;

    if (!nom.trim() || !telephone.trim()) {
      setErreur("Le nom et le téléphone sont obligatoires.");
      return;
    }

    verrouEnvoi.current = true;
    setEnvoi(true);
    props.onEnvoiChange(true);
    setErreur("");

    try {
      const response = await fetch(
        "/api/hotels/1/reservations-completes",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            chambre_id: props.chambreId,
            date_arrivee: props.arrivee,
            date_depart: props.depart,
            nombre_personnes: props.personnes,
            client: {
              nom: nom.trim(),
              telephone: telephone.trim(),
              email: email.trim() || null,
            },
          }),
        }
      );

      if (!response.ok) {
        const data = await response.json().catch(() => null);

        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : `La demande a échoué (${response.status}).`
        );
      }

      const reservation: ReservationCreee = await response.json();

      props.onConfirmee(
        reservation.id,
        reservation.prix_total
      );
    } catch (error) {
      setErreur(
        error instanceof Error
          ? error.message
          : "Impossible de contacter le serveur."
      );
    } finally {
      verrouEnvoi.current = false;
      setEnvoi(false);
      props.onEnvoiChange(false);
    }
  }

  return (
    <section
      className="reservation-form"
      aria-label="Formulaire de réservation"
    >
      <h2>Réserver la chambre {props.numero}</h2>

      <div className="recapitulatif">
        <p>
          <strong>Arrivée :</strong> {afficherDate(props.arrivee)}
        </p>

        <p>
          <strong>Départ :</strong> {afficherDate(props.depart)}
        </p>

        <p>
          <strong>Voyageurs :</strong> {props.personnes}
        </p>

        <p>
          <strong>Montant proposé :</strong>{" "}
          {props.prixTotal.toLocaleString("fr-FR", {
            style: "currency",
            currency: "EUR",
          })}
        </p>

        <p>Paiement à l’hôtel.</p>
      </div>

      <form onSubmit={confirmer}>
        <fieldset disabled={envoi}>
          <label>
            Nom complet
            <input
              required
              maxLength={150}
              autoComplete="name"
              value={nom}
              onChange={(event) => setNom(event.target.value)}
            />
          </label>

          <label>
            Téléphone
            <input
              type="tel"
              required
              maxLength={30}
              autoComplete="tel"
              value={telephone}
              onChange={(event) => setTelephone(event.target.value)}
            />
          </label>

          <label>
            E-mail (facultatif)
            <input
              type="email"
              maxLength={254}
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
        </fieldset>

        {erreur && (
          <p className="message-erreur" role="alert">
            {erreur}
          </p>
        )}

        <div className="actions">
          <button type="submit" disabled={envoi}>
            {envoi
              ? "Enregistrement…"
              : "Confirmer la réservation"}
          </button>

          <button
            className="bouton-secondaire"
            type="button"
            disabled={envoi}
            onClick={props.onFermer}
          >
            Fermer
          </button>
        </div>
      </form>
    </section>
  );
}