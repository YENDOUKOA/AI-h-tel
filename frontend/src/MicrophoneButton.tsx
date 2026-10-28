import { useEffect, useRef, useState } from "react";

type ResultatVocal = {
  isFinal: boolean;
  0: { transcript: string };
};

type EvenementResultat = {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: ResultatVocal;
  };
};

type ReconnaissanceVocale = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: EvenementResultat) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type ConstructeurReconnaissance = new () => ReconnaissanceVocale;

type FenetreVocale = Window & {
  SpeechRecognition?: ConstructeurReconnaissance;
  webkitSpeechRecognition?: ConstructeurReconnaissance;
};

type Props = {
  disabled: boolean;
  onTranscription: (texte: string) => void;
};

export default function MicrophoneButton({
  disabled,
  onTranscription,
}: Props) {
  const [ecoute, setEcoute] = useState(false);
  const [erreur, setErreur] = useState("");

  const reconnaissanceRef = useRef<ReconnaissanceVocale | null>(null);
  const transcriptionRef = useRef(onTranscription);

  useEffect(() => {
    transcriptionRef.current = onTranscription;
  }, [onTranscription]);

  useEffect(() => {
    return () => {
      const reconnaissance = reconnaissanceRef.current;

      if (reconnaissance) {
        reconnaissance.onresult = null;
        reconnaissance.onerror = null;
        reconnaissance.onend = null;
        reconnaissance.abort();
      }
    };
  }, []);

  useEffect(() => {
    if (disabled) {
      reconnaissanceRef.current?.abort();
    }
  }, [disabled]);

  const fenetre = window as FenetreVocale;
  const Constructeur =
    fenetre.SpeechRecognition || fenetre.webkitSpeechRecognition;

  function basculerMicro() {
    if (disabled) return;

    if (reconnaissanceRef.current) {
      reconnaissanceRef.current.stop();
      return;
    }

    if (!Constructeur) return;

    setErreur("");

    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    const reconnaissance = new Constructeur();

    reconnaissance.lang = "fr-FR";
    reconnaissance.continuous = false;
    reconnaissance.interimResults = false;

    reconnaissance.onresult = (event) => {
      const morceaux: string[] = [];

      for (
        let index = event.resultIndex;
        index < event.results.length;
        index++
      ) {
        if (event.results[index].isFinal) {
          morceaux.push(event.results[index][0].transcript);
        }
      }

      const texte = morceaux.join(" ").trim();

      if (texte) {
        transcriptionRef.current(texte);
      }
    };

    reconnaissance.onerror = (event) => {
      const messages: Record<string, string> = {
        "not-allowed":
          "L’accès au microphone a été refusé. Vérifie les permissions du navigateur.",
        "audio-capture":
          "Aucun microphone utilisable n’a été trouvé.",
        "no-speech":
          "Aucune parole détectée. Réessaie.",
        network:
          "Le service de reconnaissance vocale est inaccessible.",
        "service-not-allowed":
          "La reconnaissance vocale n’est pas autorisée dans ce navigateur.",
      };

      if (event.error !== "aborted") {
        setErreur(
          messages[event.error] ||
            `Reconnaissance vocale impossible : ${event.error}.`
        );
      }
    };

    reconnaissance.onend = () => {
      reconnaissanceRef.current = null;
      setEcoute(false);
    };

    reconnaissanceRef.current = reconnaissance;

    try {
      reconnaissance.start();
      setEcoute(true);
    } catch {
      reconnaissanceRef.current = null;
      setEcoute(false);
      setErreur("Impossible de démarrer le microphone.");
    }
  }

  if (!Constructeur) {
    return (
      <p>
        La reconnaissance vocale n’est pas disponible dans ce
        navigateur. Tu peux continuer à écrire.
      </p>
    );
  }

  return (
    <div>
      <button
        type="button"
        className="bouton-secondaire"
        disabled={disabled}
        aria-pressed={ecoute}
        onClick={basculerMicro}
      >
        {ecoute ? "Arrêter le micro" : "Parler au microphone"}
      </button>

      {ecoute && (
        <p role="status">Je vous écoute…</p>
      )}

      {erreur && (
        <p className="message-erreur" role="alert">
          {erreur}
        </p>
      )}
    </div>
  );
}