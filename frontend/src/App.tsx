import { useRef, useState } from "react";
import type { FormEvent } from "react";
import AssistantChat from "./AssistantChat";
import ReservationsDashboard from "./ReservationsDashboard";
import ReservationForm from "./ReservationForm";
import "./App.css";

type ChambreDisponible = {
  id: number;
  numero: string;
  type_chambre: string;
  capacite: number;
  prix_par_nuit: number;
  prix_total: number;
};

type ResultatRecherche = {
  nombre_nuits: number;
  date_arrivee: string;
  date_depart: string;
  nombre_personnes: number;
  chambres_disponibles: ChambreDisponible[];
};

function euros(montant: number) {
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
  }).format(montant);
}

function afficherDate(valeur: string) {
  const [annee, mois, jour] = valeur.split("-");
  return `${jour}/${mois}/${annee}`;
}

function dateLocaleAujourdhui() {
  const maintenant = new Date();
  const annee = maintenant.getFullYear();
  const mois = String(maintenant.getMonth() + 1).padStart(2, "0");
  const jour = String(maintenant.getDate()).padStart(2, "0");

  return `${annee}-${mois}-${jour}`;
}

function App() {
  const [page, setPage] = useState<"recherche" | "reservations">(
    "recherche"
  );

  const [arrivee, setArrivee] = useState("");
  const [depart, setDepart] = useState("");
  const [personnes, setPersonnes] = useState("1");

  const [resultat, setResultat] = useState<ResultatRecherche | null>(
    null
  );

  const [selection, setSelection] = useState<ChambreDisponible | null>(
    null
  );

  const [chargement, setChargement] = useState(false);
  const [reservationEnCours, setReservationEnCours] = useState(false);
  const [erreur, setErreur] = useState("");
  const [confirmation, setConfirmation] = useState("");

  const derniereRecherche = useRef(0);

  function modifierCriteres() {
    derniereRecherche.current += 1;
    setResultat(null);
    setSelection(null);
    setErreur("");
    setConfirmation("");
    setChargement(false);
  }

  async function rechercher(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const rechercheId = ++derniereRecherche.current;

    setSelection(null);
    setConfirmation("");
    setErreur("");
    setResultat(null);
    setChargement(false);

    const nombrePersonnes = Number(personnes);

    if (!Number.isInteger(nombrePersonnes) || nombrePersonnes < 1) {
      setErreur(
        "Indique un nombre entier de personnes supérieur à zéro."
      );
      return;
    }

    if (!arrivee || !depart) {
      setErreur("Renseigne les dates du séjour.");
      return;
    }

    if (arrivee < dateLocaleAujourdhui()) {
      setErreur("La date d’arrivée ne peut pas être passée.");
      return;
    }

    if (depart <= arrivee) {
      setErreur("Le départ doit être après la date d’arrivée.");
      return;
    }

    setChargement(true);

    try {
      const params = new URLSearchParams({
        date_arrivee: arrivee,
        date_depart: depart,
        nombre_personnes: String(nombrePersonnes),
      });

      const response = await fetch(
        `/api/hotels/1/disponibilites?${params}`
      );

      if (!response.ok) {
        const data = await response.json().catch(() => null);

        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : `Recherche impossible (${response.status}).`
        );
      }

      const data: ResultatRecherche = await response.json();

      if (rechercheId === derniereRecherche.current) {
        setResultat(data);
      }
    } catch (error) {
      if (rechercheId === derniereRecherche.current) {
        setErreur(
          error instanceof Error
            ? error.message
            : "Impossible de contacter le serveur."
        );
      }
    } finally {
      if (rechercheId === derniereRecherche.current) {
        setChargement(false);
      }
    }
  }

  if (page === "reservations") {
    return (
      <main className="dashboard">
        <header className="page-header">
          <p className="subtitle">Gestion hôtelière</p>
          <h1>Hôtel Démo</h1>
        </header>

        <button
          type="button"
          className="bouton-secondaire retour"
          onClick={() => {
            modifierCriteres();
            setPage("recherche");
          }}
        >
          Retour à la recherche
        </button>

        <ReservationsDashboard />
      </main>
    );
  }

  return (
    <main className="dashboard">
      <header className="page-header">
        <p className="subtitle">Réservation en ligne</p>
        <h1>Hôtel Démo</h1>
        <p>
          Trouvez votre chambre et préparez votre prochain séjour.
        </p>
      </header>

      <nav className="navigation" aria-label="Navigation principale">
        <button
          type="button"
          disabled={reservationEnCours}
          onClick={() => {
            modifierCriteres();
            setPage("reservations");
          }}
        >
          Voir les réservations
        </button>
      </nav>

      <form className="recherche" onSubmit={rechercher}>
        <fieldset disabled={reservationEnCours}>
          <label>
            Arrivée
            <input
              type="date"
              required
              min={dateLocaleAujourdhui()}
              value={arrivee}
              onChange={(event) => {
                modifierCriteres();
                setArrivee(event.target.value);
              }}
            />
          </label>

          <label>
            Départ
            <input
              type="date"
              required
              min={arrivee || dateLocaleAujourdhui()}
              value={depart}
              onChange={(event) => {
                modifierCriteres();
                setDepart(event.target.value);
              }}
            />
          </label>

          <label>
            Nombre de personnes
            <input
              type="number"
              required
              min={1}
              step={1}
              value={personnes}
              onChange={(event) => {
                modifierCriteres();
                setPersonnes(event.target.value);
              }}
            />
          </label>

          <button type="submit" disabled={chargement}>
            {chargement ? "Recherche…" : "Rechercher"}
          </button>
        </fieldset>
      </form>

      {erreur && (
        <p className="message-erreur" role="alert">
          {erreur}
        </p>
      )}

      {confirmation && (
        <p className="message-succes" role="status">
          {confirmation}
        </p>
      )}

      <div aria-live="polite">
        {resultat && (
          <p className="resume-recherche">
            {resultat.chambres_disponibles.length} chambre(s)
            disponible(s)
            {" du "}
            {afficherDate(resultat.date_arrivee)}
            {" au "}
            {afficherDate(resultat.date_depart)}
            {" — "}
            {resultat.nombre_nuits} nuit(s),{" "}
            {resultat.nombre_personnes} personne(s).
          </p>
        )}

        {resultat?.chambres_disponibles.length === 0 && (
          <p>Aucune chambre ne correspond à ces critères.</p>
        )}
      </div>

      <section
        className="chambres"
        aria-label="Chambres disponibles"
      >
        {resultat?.chambres_disponibles.map((chambre) => (
          <article className="chambre" key={chambre.id}>
            <span className="badge">
              {chambre.type_chambre}
            </span>

            <h2>Chambre {chambre.numero}</h2>

            <p>
              Capacité : {chambre.capacite} personne(s)
            </p>

            <p>{euros(chambre.prix_par_nuit)} / nuit</p>

            <p className="prix">
              {euros(chambre.prix_total)}
            </p>

            <p className="prix-legende">Total du séjour</p>

            <button
              type="button"
              disabled={reservationEnCours}
              onClick={() => {
                setConfirmation("");
                setSelection(chambre);
              }}
            >
              Réserver
            </button>
          </article>
        ))}
      </section>

      {selection && resultat && (
        <ReservationForm
          key={`${selection.id}-${resultat.date_arrivee}-${resultat.date_depart}-${resultat.nombre_personnes}`}
          chambreId={selection.id}
          numero={selection.numero}
          arrivee={resultat.date_arrivee}
          depart={resultat.date_depart}
          personnes={resultat.nombre_personnes}
          prixTotal={selection.prix_total}
          onEnvoiChange={setReservationEnCours}
          onFermer={() => setSelection(null)}
          onConfirmee={(id, prixTotal) => {
            setConfirmation(
              `Réservation n° ${id} confirmée ! Montant : ${euros(
                prixTotal
              )}. Paiement à l’hôtel.`
            );

            setSelection(null);
            setResultat(null);
          }}
        />
      )}
      <AssistantChat />
    </main>
  );
}

export default App;
