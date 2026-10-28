import { useEffect, useRef, useState } from "react";

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

type ResultatVocal = {
  isFinal: boolean;
  0: { transcript: string };
};

type Reconnaissance = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: {
    resultIndex: number;
    results: {
      length: number;
      [index: number]: ResultatVocal;
    };
  }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  abort: () => void;
};

type FenetreVocale = Window & {
  SpeechRecognition?: new () => Reconnaissance;
  webkitSpeechRecognition?: new () => Reconnaissance;
};

type Props = {
  disabled: boolean;
  onActiveChange: (active: boolean) => void;
  onProposition: (proposition: Proposition) => void;
};

export default function ConversationVocale({
  disabled,
  onActiveChange,
  onProposition,
}: Props) {
  const [active, setActive] = useState(false);
  const [statut, setStatut] = useState("");
  const [erreur, setErreur] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);

  const activeRef = useRef(false);
  const sessionRef = useRef(0);
  const historiqueRef = useRef<Message[]>([]);
  const reconnaissanceRef = useRef<Reconnaissance | null>(null);
  const requeteRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      activeRef.current = false;
      sessionRef.current += 1;

      const reconnaissance = reconnaissanceRef.current;

      if (reconnaissance) {
        reconnaissance.onend = null;
        reconnaissance.onerror = null;
        reconnaissance.onresult = null;
        reconnaissance.abort();
      }

      requeteRef.current?.abort();

      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  function sessionValide(session: number) {
    return activeRef.current && session === sessionRef.current;
  }

  function arreter() {
    activeRef.current = false;
    sessionRef.current += 1;

    const reconnaissance = reconnaissanceRef.current;
    reconnaissanceRef.current = null;

    if (reconnaissance) {
      reconnaissance.onend = null;
      reconnaissance.onerror = null;
      reconnaissance.onresult = null;
      reconnaissance.abort();
    }

    requeteRef.current?.abort();
    requeteRef.current = null;

    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    setActive(false);
    setStatut("");
    onActiveChange(false);
  }

  function echouer(message: string) {
    arreter();
    setErreur(message);
  }

  function ecouter(session: number) {
    if (!sessionValide(session)) return;

    const fenetre = window as FenetreVocale;
    const Constructeur =
      fenetre.SpeechRecognition || fenetre.webkitSpeechRecognition;

    if (!Constructeur) {
      echouer("La reconnaissance vocale est indisponible.");
      return;
    }

    const reconnaissance = new Constructeur();
    reconnaissanceRef.current = reconnaissance;

    reconnaissance.lang = "fr-FR";
    reconnaissance.continuous = false;
    reconnaissance.interimResults = false;

    let transcription = "";

    reconnaissance.onresult = (event) => {
      for (
        let index = event.resultIndex;
        index < event.results.length;
        index++
      ) {
        if (event.results[index].isFinal) {
          transcription += ` ${event.results[index][0].transcript}`;
        }
      }
    };

    reconnaissance.onerror = (event) => {
      if (!sessionValide(session)) return;

      const descriptions: Record<string, string> = {
        "not-allowed": "Autorise le microphone dans ton navigateur.",
        "no-speech": "Aucune parole détectée. Relance la conversation.",
        "audio-capture": "Aucun microphone utilisable.",
        network: "Le service de transcription est inaccessible.",
      };

      echouer(
        descriptions[event.error] ||
          `Erreur du microphone : ${event.error}.`
      );
    };

    reconnaissance.onend = () => {
      if (!sessionValide(session)) return;

      reconnaissanceRef.current = null;
      const texte = transcription.trim();

      if (!texte) {
        echouer("Aucune phrase détectée. Relance la conversation.");
        return;
      }

      void traiter(texte, session);
    };

    try {
      setStatut("Je vous écoute…");
      reconnaissance.start();
    } catch {
      echouer("Impossible de démarrer le microphone.");
    }
  }

  async function traiter(texte: string, session: number) {
    if (!sessionValide(session)) return;

    if (texte.length > 2000) {
      echouer("La phrase est trop longue. Essaie une demande plus courte.");
      return;
    }

    setStatut("L’assistant réfléchit…");

    const controller = new AbortController();
    requeteRef.current = controller;

    try {
      const response = await fetch("/api/assistant/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          message: texte,
          historique: historiqueRef.current.slice(-20),
        }),
      });

      const data = await response.json().catch(() => null);

      if (!sessionValide(session)) return;

      if (!response.ok) {
        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : `Erreur du serveur (${response.status}).`
        );
      }

      if (typeof data?.reponse !== "string" || !data.reponse.trim()) {
        throw new Error("L’assistant a retourné une réponse vide.");
      }

      const nouveauxMessages: Message[] = [
        ...historiqueRef.current,
        { role: "user", content: texte },
        { role: "assistant", content: data.reponse },
      ];

      historiqueRef.current = nouveauxMessages.slice(-20);
      setMessages(nouveauxMessages.slice(-20));

      const proposition: Proposition | null = data.proposition ?? null;

      if (
        proposition &&
        (!Number.isFinite(Number(proposition.prix_total)) ||
          Number(proposition.prix_total) < 0)
      ) {
        throw new Error("Le montant proposé est invalide.");
      }

      const lecture = new SpeechSynthesisUtterance(
        data.reponse
          .replace(/\*\*/g, "")
          .replace(/^\s*[-#]+\s/gm, "")
      );

      lecture.lang = "fr-FR";
      setStatut("L’assistant répond…");

      lecture.onend = () => {
        if (!sessionValide(session)) return;

        if (proposition) {
          arreter();
          onProposition(proposition);
        } else {
          ecouter(session);
        }
      };

      lecture.onerror = () => {
        if (sessionValide(session)) {
          echouer("Lecture vocale interrompue. Relance la conversation.");
        }
      };

      window.speechSynthesis.speak(lecture);
    } catch (error) {
      if (!sessionValide(session)) return;

      echouer(
        error instanceof Error
          ? error.message
          : "Impossible de contacter l’assistant."
      );
    }
  }

  function demarrer() {
    if (disabled || activeRef.current) return;

    const fenetre = window as FenetreVocale;

    if (
      !(fenetre.SpeechRecognition || fenetre.webkitSpeechRecognition) ||
      !("speechSynthesis" in window)
    ) {
      setErreur("Ce navigateur ne permet pas ce mode vocal.");
      return;
    }

    window.speechSynthesis.cancel();
    setErreur("");
    setMessages([]);
    historiqueRef.current = [];

    activeRef.current = true;
    sessionRef.current += 1;
    setActive(true);
    onActiveChange(true);

    ecouter(sessionRef.current);
  }

  return (
    <section className="reservation-form">
      <h3>Conversation vocale continue</h3>

      <p>
        Parlez après « Je vous écoute ». Le microphone reste arrêté
        pendant la réponse de l’assistant.
      </p>

      <button
        type="button"
        disabled={!active && disabled}
        onClick={active ? arreter : demarrer}
      >
        {active ? "Arrêter la conversation" : "Démarrer la conversation"}
      </button>

      {statut && <p role="status">{statut}</p>}
      {erreur && <p role="alert">{erreur}</p>}

      {messages.map((message, index) => (
        <div key={index}>
          <strong>
            {message.role === "user" ? "Vous" : "Assistant"}
          </strong>
          <p style={{ whiteSpace: "pre-wrap" }}>{message.content}</p>
        </div>
      ))}
    </section>
  );
}