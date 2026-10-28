import { useEffect, useRef, useState } from "react";
import MicrophoneButton from "./MicrophoneButton";
import ConversationVocale from "./ConversationVocale";
import type { FormEvent } from "react";
import ReservationForm from "./ReservationForm";
import "./AssistantChat.css";

type Message = {
  role: "user" | "assistant";
  content: string;
};

type Proposition = {
  chambre_id: number;
  numero: string;
  type_chambre: string;
  date_arrivee: string;
  date_depart: string;
  nombre_personnes: number;
  nombre_nuits: number;
  prix_total: string;
  devise: string;
};

function arreterLecture() {
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
}

function lireReponse(texte: string) {
  if (!("speechSynthesis" in window)) return;

  arreterLecture();

  const texteNettoye = texte
    .replace(/\*\*/g, "")
    .replace(/^\s*[-#]+\s/gm, "");

  const lecture = new SpeechSynthesisUtterance(texteNettoye);

  lecture.lang = "fr-FR";
  lecture.rate = 1;
  lecture.pitch = 1;

  window.speechSynthesis.speak(lecture);
}


export default function AssistantChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [texte, setTexte] = useState("");
  const [envoi, setEnvoi] = useState(false);
  const [erreur, setErreur] = useState("");
  const [conversationActive, setConversationActive] = useState(false);

  const [voixActive, setVoixActive] = useState(false);
  const voixActiveRef = useRef(false);
  const [envoiVocalAuto, setEnvoiVocalAuto] = useState(false);

  const [proposition, setProposition] = useState<Proposition | null>(
    null
  );

  const [reservationEnCours, setReservationEnCours] = useState(false);
  const [confirmation, setConfirmation] = useState("");

  const verrou = useRef(false);

  const voixDisponible = "speechSynthesis" in window;

  // Arrête la voix lorsque le composant quitte la page.
  useEffect(() => {
    return () => arreterLecture();
  }, []);

  function basculerVoix() {
    const active = !voixActiveRef.current;

    voixActiveRef.current = active;
    setVoixActive(active);

    if (active) {
      lireReponse("Les réponses vocales sont activées.");
    } else {
      arreterLecture();
    }
  }

  async function envoyer(event: FormEvent<HTMLFormElement>) {
  event.preventDefault();
  await envoyerMessage(texte);
}

async function envoyerMessage(message: string) {
  const contenu = message.trim();
  if (
  !contenu ||
  verrou.current ||
  reservationEnCours ||
  conversationActive
) {
  return;
}

    if (!contenu || verrou.current || reservationEnCours) return;

    verrou.current = true;
    setEnvoi(true);
    setErreur("");
    setProposition(null);
    setConfirmation("");
    arreterLecture();
    try {
      const response = await fetch("/api/assistant/message", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: contenu,
          historique: messages.slice(-20),
        }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => null);

        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : `La demande a échoué (${response.status}).`
        );
      }

      const data = await response.json();

      if (
        typeof data.reponse !== "string" ||
        !data.reponse.trim()
      ) {
        throw new Error("L’assistant a retourné une réponse vide.");
      }

      if (data.proposition) {
        const montant = Number(data.proposition.prix_total);

        if (!Number.isFinite(montant) || montant < 0) {
          throw new Error("Le montant proposé est invalide.");
        }

        setProposition(data.proposition);
      }

      setMessages((precedents) => [
        ...precedents,
        { role: "user", content: contenu },
        { role: "assistant", content: data.reponse },
      ]);

      setTexte("");

      if (voixActiveRef.current) {
        lireReponse(data.reponse);
      }
    } catch (error) {
      setErreur(
        error instanceof Error
          ? error.message
          : "Impossible de contacter l’assistant."
      );
    } finally {
      verrou.current = false;
      setEnvoi(false);
    }
  }

  function nouvelleConversation() {
    arreterLecture();
    setMessages([]);
    setTexte("");
    setErreur("");
    setProposition(null);
    setConfirmation("");
  }

  return (
    <section className="assistant-chat" aria-labelledby="titre-chat">
      <header>
        <h2 id="titre-chat">Assistant de l’hôtel</h2>

        <p>
          Recherchez une chambre, puis demandez une proposition.
          Utilisez uniquement des coordonnées fictives pour ces tests.
        </p>
      </header>
      <ConversationVocale
  disabled={envoi || reservationEnCours}
  onActiveChange={(active) => {
    setConversationActive(active);

    if (active) {
      setProposition(null);
      setConfirmation("");
      setErreur("");
    }
  }}
  onProposition={(nouvelleProposition) => {
    setProposition(nouvelleProposition);
  }}
/>

      <div className="actions">
        <button
          type="button"
          className="bouton-secondaire"
          disabled={!voixDisponible || conversationActive}
          aria-pressed={voixActive}
          onClick={basculerVoix}
        >
          {voixActive ? "Désactiver la voix" : "Activer la voix"}
        </button>
envoyerMessage
        <button
          type="button"
          className="bouton-secondaire"
          disabled={!voixDisponible || conversationActive}
          onClick={arreterLecture}
        >
          Arrêter la lecture
        </button>
      </div>

      {!voixDisponible && (
        <p>
          La lecture vocale n’est pas disponible dans ce navigateur.
        </p>
      )}

      <div
        className="chat-messages"
        role="log"
        aria-label="Conversation avec l’assistant"
        aria-live="polite"
        aria-relevant="additions"
      >
        {messages.length === 0 && (
          <p className="chat-indication">
            Exemple : « Je cherche une chambre pour deux personnes
            du 10 au 12 décembre 2026. »
          </p>
        )}

        {messages.map((message, index) => (
          <div
            className={`chat-message chat-${message.role}`}
            key={index}
          >
            <strong>
              {message.role === "user" ? "Vous" : "Assistant"}
            </strong>

            <p>{message.content}</p>
          </div>
        ))}
      </div>

      {envoi && (
        <p role="status">L’assistant prépare sa réponse…</p>
      )}

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
<div>
  <label>
    <input
      type="checkbox"
      checked={envoiVocalAuto}
      disabled={envoi || reservationEnCours}
      onChange={(event) =>
        setEnvoiVocalAuto(event.target.checked)
      }
      style={{ width: "auto", alignSelf: "flex-start" }}
    />
    Envoyer automatiquement après avoir parlé
  </label>

  <MicrophoneButton
    disabled={envoi || reservationEnCours || conversationActive}
    onTranscription={(transcription) => {
      if (envoiVocalAuto) {
        setTexte(transcription);
        void envoyerMessage(transcription);
      } else {
        setTexte((precedent) =>
          `${precedent.trim()} ${transcription}`
            .trim()
            .slice(0, 2000)
        );
      }
    }}
  />
</div>
      <form className="chat-formulaire" onSubmit={envoyer}>
        <label htmlFor="message-assistant">Votre message</label>

        <textarea
          id="message-assistant"
          required
          maxLength={2000}
          rows={3}
          disabled={envoi || reservationEnCours}
          value={texte}
          onChange={(event) => setTexte(event.target.value)}
          placeholder="Indiquez vos dates et le nombre de voyageurs…"
        />

        <div className="actions">
          <button
            type="submit"
            disabled={
  envoi || reservationEnCours || conversationActive || !texte.trim()
}
          >
            {envoi ? "Envoi…" : "Envoyer"}
          </button>

          <button
            type="button"
            className="bouton-secondaire"
            disabled={envoi || reservationEnCours || conversationActive}
            onClick={nouvelleConversation}
          >
            Nouvelle conversation
          </button>
        </div>
      </form>

      {proposition && (
        <ReservationForm
          key={`${proposition.chambre_id}-${proposition.date_arrivee}-${proposition.date_depart}-${proposition.nombre_personnes}`}
          chambreId={proposition.chambre_id}
          numero={proposition.numero}
          arrivee={proposition.date_arrivee}
          depart={proposition.date_depart}
          personnes={proposition.nombre_personnes}
          prixTotal={Number(proposition.prix_total)}
          onEnvoiChange={setReservationEnCours}
          onFermer={() => {
            arreterLecture();
            setProposition(null);
          }}
          onConfirmee={(id, prixTotal) => {
            const messageConfirmation =
              `Réservation n° ${id} confirmée ! Montant : ${
                prixTotal.toLocaleString("fr-FR", {
                  style: "currency",
                  currency: "EUR",
                })
              }. Paiement à l’hôtel.`;

            setConfirmation(messageConfirmation);
            setProposition(null);
            setMessages([]);

            if (voixActiveRef.current) {
              lireReponse(messageConfirmation);
            }
          }}
        />
      )}
    </section>
  );
}