import { useEffect, useRef, useState } from "react";

type Message = { role: "user" | "assistant"; content: string };
type Proposition = {
  chambre_id: number; numero: string; type_chambre: string;
  date_arrivee: string; date_depart: string; nombre_personnes: number;
  nombre_nuits: number; prix_total: string; devise: string;
};
type Reponse = {
  conversation_id: string;
  etat: "discussion" | "collecte_coordonnees" | "attente_confirmation" | "confirmee" | "abandonnee";
  reponse: string;
  terminee?: boolean;
  confirmee?: boolean;
  reservation_id?: number | null;
};
type ResultatVocal = { isFinal: boolean; 0: { transcript: string } };
type Reconnaissance = {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((event: {
    resultIndex: number;
    results: { length: number; [index: number]: ResultatVocal };
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
  // Conservé pour les anciens appels depuis AssistantChat ; aucun formulaire
  // n'est ouvert par ce composant vocal.
  onProposition?: (proposition: Proposition) => void;
};

export default function ConversationVocale({ disabled, onActiveChange }: Props) {
  const [active, setActive] = useState(false);
  const [statut, setStatut] = useState("");
  const [erreur, setErreur] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [terminee, setTerminee] = useState(false);
  const [etat, setEtat] = useState<Reponse["etat"]>("discussion");
  const [reference, setReference] = useState<number | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);

  const termineeRef = useRef(false);
  const envoiRef = useRef(false);
  const surveillanceRef = useRef<ReturnType<typeof window.setInterval> | null>(null);
  const activeRef = useRef(false);
  const sessionRef = useRef(0);
  const idRef = useRef<string | null>(null);
  const reconnaissanceRef = useRef<Reconnaissance | null>(null);
  const requeteRef = useRef<AbortController | null>(null);
  const lectureRef = useRef<SpeechSynthesisUtterance | null>(null);
  const attenteRef = useRef<{ message: string; request_id: string } | null>(null);
  const onActiveChangeRef = useRef(onActiveChange);
  onActiveChangeRef.current = onActiveChange;

  function nettoyer() {
    if (surveillanceRef.current !== null) {
      window.clearInterval(surveillanceRef.current);
      surveillanceRef.current = null;
    }
    envoiRef.current = false;
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
    if (lectureRef.current) {
      lectureRef.current.onend = null;
      lectureRef.current.onerror = null;
      lectureRef.current = null;
    }
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }

  useEffect(() => () => {
    activeRef.current = false;
    sessionRef.current += 1;
    nettoyer();
    onActiveChangeRef.current(false);
  }, []);

  function sessionValide(session: number) {
    return activeRef.current && session === sessionRef.current;
  }

  function arreter() {
    activeRef.current = false;
    sessionRef.current += 1;
    nettoyer();
    setActive(false);
    setStatut("");
    onActiveChangeRef.current(false);
  }

  function echouer(message: string) {
    arreter();
    setErreur(message);
  }

  function ajouter(message: Message) {
    setMessages((anciens) => [...anciens, message].slice(-30));
  }

  function lire(data: Reponse, session: number) {
    if (!sessionValide(session)) return;
    const fin = data.terminee === true || data.confirmee === true ||
      data.etat === "confirmee" || data.etat === "abandonnee";
    // Verrou synchrone : aucun événement tardif ne doit rouvrir le micro.
    termineeRef.current = fin;
    setTerminee(fin);
    setEtat(data.etat);
    if (data.etat === "confirmee" && data.reservation_id) {
      setReference(data.reservation_id);
    }
    if (surveillanceRef.current !== null) window.clearInterval(surveillanceRef.current);
    const lecture = new SpeechSynthesisUtterance(
      data.reponse.replace(/\*\*/g, "").replace(/^\s*[-#]+\s/gm, ""),
    );
    lectureRef.current = lecture;
    lecture.lang = "fr-FR";
    lecture.rate = 1;
    setStatut("L’assistant répond…");
    let fini = false;
    let commencee = false;
    const debut = Date.now();
    const delaiMax = Math.min(90000, Math.max(8000, lecture.text.length * 120 + 5000));
    function terminerLecture(interrompue = false) {
      if (fini || !sessionValide(session)) return;
      fini = true;
      lecture.onend = null;
      lecture.onerror = null;
      lecture.onstart = null;
      if (surveillanceRef.current !== null) window.clearInterval(surveillanceRef.current);
      surveillanceRef.current = null;
      lectureRef.current = null;
      if (fin) {
        arreter();
        setStatut("Conversation terminée.");
        if (interrompue) setErreur("La lecture a été interrompue. Le résultat reste affiché ci-dessous.");
      } else if (interrompue) {
        echouer("Lecture interrompue. Cliquez sur Reprendre pour continuer.");
      } else {
        ecouter(session);
      }
    }
    lecture.onstart = () => { commencee = true; };
    lecture.onend = () => terminerLecture();
    lecture.onerror = () => terminerLecture(true);
    // Certains navigateurs omettent onend. On surveille aussi l'état du moteur
    // et on arrête au délai maximal si le moteur lui-même reste bloqué.
    surveillanceRef.current = window.setInterval(() => {
      if (!sessionValide(session)) return;
      const duree = Date.now() - debut;
      const silencieux = !window.speechSynthesis.speaking && !window.speechSynthesis.pending;
      if (silencieux && duree > 1200 && commencee) terminerLecture();
      else if (silencieux && duree > 4000 && !commencee) terminerLecture(true);
      else if (duree > delaiMax) {
        terminerLecture(true);
        window.speechSynthesis.cancel();
      }
    }, 250);
    try {
      window.speechSynthesis.speak(lecture);
    } catch {
      terminerLecture(true);
    }
  }

  function ecouter(session: number) {
    if (!sessionValide(session) || termineeRef.current || envoiRef.current) return;
    const fenetre = window as FenetreVocale;
    const Constructeur = fenetre.SpeechRecognition || fenetre.webkitSpeechRecognition;
    if (!Constructeur) return echouer("La reconnaissance vocale est indisponible.");
    const reconnaissance = new Constructeur();
    reconnaissanceRef.current = reconnaissance;
    reconnaissance.lang = "fr-FR";
    reconnaissance.continuous = false;
    reconnaissance.interimResults = false;
    let transcription = "";
    reconnaissance.onresult = (event) => {
      if (!sessionValide(session) || termineeRef.current || reconnaissanceRef.current !== reconnaissance) return;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) transcription += ` ${event.results[i][0].transcript}`;
      }
    };
    reconnaissance.onerror = (event) => {
      if (!sessionValide(session)) return;
      const descriptions: Record<string, string> = {
        "not-allowed": "Autorisez le microphone dans votre navigateur.",
        "no-speech": "Aucune parole détectée. Cliquez sur Reprendre.",
        "audio-capture": "Aucun microphone utilisable.",
        network: "Le service de transcription est inaccessible.",
      };
      echouer(descriptions[event.error] || `Erreur du microphone : ${event.error}.`);
    };
    reconnaissance.onend = () => {
      if (!sessionValide(session) || termineeRef.current || reconnaissanceRef.current !== reconnaissance) return;
      // Neutralise immédiatement les événements doublés du même micro.
      reconnaissance.onend = null;
      reconnaissance.onerror = null;
      reconnaissance.onresult = null;
      reconnaissanceRef.current = null;
      const texte = transcription.trim();
      if (!texte) return echouer("Aucune phrase détectée. Cliquez sur Reprendre.");
      if (texte.length > 2000) return echouer("La phrase est trop longue. Faites une demande plus courte.");
      attenteRef.current = { message: texte, request_id: crypto.randomUUID() };
      void envoyer(session);
    };
    try {
      setStatut("Je vous écoute…");
      reconnaissance.start();
    } catch {
      echouer("Impossible de démarrer le microphone.");
    }
  }

  async function appeler(url: string, session: number, corps?: object) {
    const controller = new AbortController();
    requeteRef.current = controller;
    const response = await fetch(url, {
      method: corps ? "POST" : "GET",
      ...(corps ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(corps) } : {}),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null);
    if (!sessionValide(session)) return null;
    if (!response.ok) {
      throw new Error(typeof data?.detail === "string" ? data.detail : `Erreur du serveur (${response.status}).`);
    }
    return data;
  }

  function verifierReponse(data: Reponse | null): data is Reponse {
    if (!data || typeof data.reponse !== "string" || !data.reponse.trim()) {
      throw new Error("Le serveur a retourné une réponse vide.");
    }
    if (!["discussion", "collecte_coordonnees", "attente_confirmation", "confirmee", "abandonnee"].includes(data.etat)) {
      throw new Error("Le serveur a retourné un état de conversation inconnu.");
    }
    return true;
  }

  async function envoyer(session: number) {
    const demande = attenteRef.current;
    if (!demande || !idRef.current || !sessionValide(session) || termineeRef.current || envoiRef.current) return;
    envoiRef.current = true;
    setStatut("L’assistant prépare sa réponse…");
    try {
      const data = await appeler(`/api/assistant/conversations/${idRef.current}/messages`, session, demande);
      if (!sessionValide(session)) return;
      if (verifierReponse(data)) {
        attenteRef.current = null;
        ajouter({ role: "user", content: demande.message });
        ajouter({ role: "assistant", content: data.reponse });
        envoiRef.current = false;
        lire(data, session);
      }
    } catch (error) {
      if (sessionValide(session)) {
        // La même request_id sera réutilisée : pas de double validation.
        echouer(error instanceof Error ? error.message : "Impossible de contacter le serveur.");
      }
    }
  }

  async function demarrer() {
    if (disabled || activeRef.current || termineeRef.current) return;
    const fenetre = window as FenetreVocale;
    if (!(fenetre.SpeechRecognition || fenetre.webkitSpeechRecognition) || !("speechSynthesis" in window)) {
      setErreur("Ce navigateur ne permet pas ce mode vocal. Essayez Chrome ou Edge.");
      return;
    }
    window.speechSynthesis.cancel();
    setErreur("");
    activeRef.current = true;
    const session = ++sessionRef.current;
    setActive(true);
    onActiveChangeRef.current(true);
    setStatut("Ouverture de la conversation…");
    try {
      if (!idRef.current) {
        const creation = await appeler("/api/assistant/conversations", session, {});
        if (!sessionValide(session)) return;
        if (typeof creation?.conversation_id !== "string") throw new Error("Identifiant de conversation absent.");
        idRef.current = creation.conversation_id;
        setConversationId(creation.conversation_id);
        const data: Reponse = {
          conversation_id: creation.conversation_id,
          etat: "discussion",
          reponse: creation.message || "Bonjour, quelles sont vos dates de séjour ?",
        };
        ajouter({ role: "assistant", content: data.reponse });
        lire(data, session);
      } else if (attenteRef.current) {
        await envoyer(session);
      } else {
        const data = await appeler(`/api/assistant/conversations/${idRef.current}`, session);
        if (!sessionValide(session)) return;
        if (verifierReponse(data)) {
          ajouter({ role: "assistant", content: data.reponse });
          lire(data, session);
        }
      }
    } catch (error) {
      if (sessionValide(session)) echouer(error instanceof Error ? error.message : "Impossible de démarrer.");
    }
  }

  function nouvelleConversation() {
    if (activeRef.current || disabled) return;
    idRef.current = null;
    attenteRef.current = null;
    setConversationId(null);
    setMessages([]);
    setErreur("");
    setReference(null);
    setTerminee(false);
    termineeRef.current = false;
    setEtat("discussion");
    setStatut("");
  }

  function confirmerAvecBouton() {
    if (!activeRef.current || termineeRef.current || envoiRef.current || etat !== "attente_confirmation") return;
    const micro = reconnaissanceRef.current;
    reconnaissanceRef.current = null;
    if (micro) {
      micro.onresult = null;
      micro.onend = null;
      micro.onerror = null;
      micro.abort();
    }
    attenteRef.current = { message: "Oui, je confirme", request_id: crypto.randomUUID() };
    void envoyer(sessionRef.current);
  }

  return (
    <section className="reservation-form" aria-labelledby="titre-vocal">
      <h3 id="titre-vocal">Réserver par la voix</h3>
      <p>Parlez après « Je vous écoute ». Le microphone est arrêté pendant la réponse.</p>
      <p>Après le choix de la chambre, indiquez votre nom et votre téléphone.
        Écoutez le récapitulatif, puis dites « Oui, je confirme » pour réserver.
        Pour corriger, dites « Modifier la réservation ».</p>
      <div className="actions">
        <button type="button" disabled={!active && (disabled || terminee)}
          onClick={() => { if (active) arreter(); else void demarrer(); }}>
          {active ? "Arrêter la conversation" : terminee ? "Conversation terminée" : conversationId ? "Reprendre la conversation" : "Démarrer la conversation"}
        </button>
        <button type="button" className="bouton-secondaire" disabled={active || disabled}
          onClick={nouvelleConversation}>Nouvelle conversation vocale</button>
      </div>
      {active && etat === "attente_confirmation" && (
        <button type="button" disabled={statut !== "Je vous écoute…"} onClick={confirmerAvecBouton}>
          Confirmer la réservation
        </button>
      )}
      {statut && <p role="status">{statut}</p>}
      {erreur && <p role="alert">{erreur}</p>}
      {reference && <p className="message-succes" role="status">Réservation n° {reference} confirmée.</p>}
      <div role="log" aria-label="Conversation vocale" aria-live="polite" aria-relevant="additions">
        {messages.map((message, index) => (
          <div key={index}>
            <strong>{message.role === "user" ? "Vous" : "Assistant"}</strong>
            <p style={{ whiteSpace: "pre-wrap" }}>{message.content}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
