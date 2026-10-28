# AI Hôtel — Assistant hôtelier intelligent

Assistant de réservation pour les hôtels, avec une interface web, un chat assisté par intelligence artificielle et un mode vocal dans le navigateur.

Le projet permet de consulter les chambres disponibles, de préparer une réservation, de gérer les séjours et de tester un paiement avec Stripe. Il est développé progressivement avec l'objectif de proposer, à terme, un assistant joignable par téléphone.

> **État du projet : prototype en développement.** Les paiements utilisent le mode test de Stripe. Le mode vocal actuel fonctionne avec le microphone du navigateur ; la réception de véritables appels téléphoniques reste à intégrer.

## Fonctionnalités

- Consultation des chambres et de leurs caractéristiques.
- Recherche des disponibilités selon les dates et le nombre de voyageurs.
- Calcul du prix total du séjour.
- Création, consultation et annulation de réservations.
- Gestion des coordonnées des clients.
- Chat IA connecté aux données de disponibilité via des outils backend.
- Préparation d'une proposition de réservation par l'assistant.
- Reconnaissance et synthèse vocales dans le navigateur.
- Conversations suivies côté serveur avec un identifiant unique.
- Création d'un lien Stripe de test pour régler 50 % du séjour.
- Mise à jour du paiement à partir des événements webhook Stripe.
- Tests d'envoi de SMS avec Twilio.

Une proposition de l'assistant ne constitue pas une réservation confirmée. Dans le parcours avec formulaire, l'utilisateur doit vérifier ses informations et confirmer la réservation.

L'enchaînement complet « réservation vocale, confirmation, lien de paiement et SMS automatique » est en cours d'intégration.

## Technologies

| Partie | Technologies |
| --- | --- |
| Interface web | React, TypeScript, Vite, CSS |
| API backend | Python, FastAPI, Pydantic, Uvicorn |
| Base de données | PostgreSQL, Psycopg |
| Intelligence artificielle | OpenRouter, client Python compatible OpenAI, appels d'outils |
| Paiement | Stripe Checkout et webhooks, en mode test |
| SMS | Twilio |
| Voix | Reconnaissance et synthèse vocales du navigateur |
| Développement | VS Code, Git, GitHub, pgAdmin 4 |

## Fonctionnement

L'interface React communique avec l'API FastAPI. Le backend consulte PostgreSQL pour obtenir les chambres, les disponibilités et les réservations.

L'assistant utilise OpenRouter pour comprendre les demandes et appeler les outils de recherche du backend. Les disponibilités et les prix proviennent de la base de données.

Stripe héberge la page de paiement. Le webhook permet au backend de recevoir les événements de paiement et d'actualiser la base. Le retour du navigateur sur le site ne suffit pas à valider un paiement.

Twilio sert aux tests d'envoi de SMS. Son raccordement automatique au parcours de réservation reste à finaliser.

## Organisation du projet

| Emplacement | Rôle |
| --- | --- |
| `backend/main.py` | Application FastAPI et routes de gestion hôtelière |
| `backend/database.py` | Connexion à PostgreSQL |
| `backend/assistant_ia.py` | Assistant IA, outils et conversations |
| `backend/paiements.py` | Liens de paiement et webhook Stripe |
| `backend/sms.py` | Fonctions et routes liées aux SMS |
| `frontend/src/App.tsx` | Interface principale |
| `frontend/src/AssistantChat.tsx` | Chat avec l'assistant |
| `frontend/src/ConversationVocale.tsx` | Conversation avec le microphone du navigateur |
| `frontend/src/MicrophoneButton.tsx` | Saisie vocale |
| `frontend/src/ReservationForm.tsx` | Formulaire de réservation |
| `frontend/src/ReservationsDashboard.tsx` | Tableau de bord des réservations |

## Installation locale

Les commandes suivantes sont adaptées à **Windows PowerShell**.

### 1. Prérequis

- Python et Node.js avec npm, compatibles avec les dépendances du projet.
- PostgreSQL installé et démarré.
- Une base contenant le schéma et les données de démonstration du projet.
- Une clé OpenRouter pour les fonctions IA.
- Un compte Stripe avec des clés de test pour les fonctions de paiement.
- Un compte Twilio pour les fonctions SMS.

**Base de données :** le clonage du dépôt ne crée pas les tables. Le schéma SQL et les migrations devront être ajoutés au dépôt pour rendre l'installation entièrement reproductible. En attendant, utilisez la base de développement existante.

### 2. Cloner le dépôt

```powershell
git clone https://github.com/YENDOUKOA/AI-h-tel.git
cd AI-h-tel
```

### 3. Préparer le backend

```powershell
cd backend
python -m venv .venv
```

Si un fichier `requirements.txt` est présent :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Sinon, installez les bibliothèques principales utilisées par le prototype :

```powershell
.\.venv\Scripts\python.exe -m pip install fastapi uvicorn "psycopg[binary]" python-dotenv openai stripe twilio
```

Un fichier de dépendances avec des versions fixées reste à ajouter pour reproduire précisément l'environnement.

### 4. Configurer l'environnement

Créez un fichier `backend/.env`. Renseignez les variables attendues par `database.py`, `assistant_ia.py`, `paiements.py` et `sms.py`.

Exemple de configuration à adapter aux noms réellement utilisés dans vos fichiers :

```dotenv
# PostgreSQL : vérifier les noms dans database.py
DB_HOST=localhost
DB_PORT=5432
DB_NAME=hotel_assistant
DB_USER=postgres
DB_PASSWORD=REMPLACER_PAR_VOTRE_MOT_DE_PASSE

# Assistant IA
OPENROUTER_API_KEY=REMPLACER_PAR_VOTRE_CLE
OPENROUTER_MODEL=REMPLACER_PAR_UN_IDENTIFIANT_DE_MODELE_COMPATIBLE_AVEC_LES_OUTILS

# Stripe : clés de test uniquement
STRIPE_SECRET_KEY=sk_test_REMPLACER
STRIPE_WEBHOOK_SECRET=whsec_REMPLACER
FRONTEND_URL=http://localhost:5173

# Twilio : vérifier aussi le nom de la variable expéditeur dans sms.py
TWILIO_ACCOUNT_SID=AC_REMPLACER
TWILIO_AUTH_TOKEN=REMPLACER
```

Le numéro expéditeur Twilio doit également être configuré selon `sms.py`. Les possibilités d'envoi dépendent des restrictions du compte Twilio utilisé.

**Ne publiez jamais le fichier `.env`, une clé API ou un mot de passe.** Un éventuel `.env.example` doit uniquement contenir des valeurs fictives.

### 5. Lancer l'API

Depuis le dossier `backend` :

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --port 8001
```

- API : [http://127.0.0.1:8001](http://127.0.0.1:8001)
- Documentation interactive : [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)

### 6. Lancer le frontend

Dans un deuxième terminal, depuis la racine du projet :

```powershell
cd frontend
npm ci
npm run dev
```

Ouvrez l'adresse affichée par Vite, généralement [http://localhost:5173](http://localhost:5173).

Le proxy `/api` de `frontend/vite.config.ts` doit pointer vers `http://127.0.0.1:8001`.

## Tester les paiements

Pour transmettre les événements Stripe à l'API locale, lancez la commande suivante dans un terminal distinct, avec Stripe CLI authentifié sur **le même compte et le même environnement de test que le backend** :

```powershell
stripe listen --events checkout.session.completed,checkout.session.async_payment_succeeded --forward-to http://127.0.0.1:8001/paiements/webhook
```

Copiez le secret de signature annoncé par Stripe CLI dans `STRIPE_WEBHOOK_SECRET`, puis redémarrez le backend. Gardez le terminal d'écoute ouvert pendant le test.

Créez une réservation, générez son lien de paiement depuis la documentation de l'API, puis effectuez un paiement de test. Vérifiez ensuite la réception du webhook et la mise à jour du statut en base.

## Quelques routes de l'API

| Méthode | Route | Fonction |
| --- | --- | --- |
| GET | `/hotels/{hotel_id}/chambres` | Lister les chambres |
| GET | `/hotels/{hotel_id}/disponibilites` | Rechercher les disponibilités |
| POST | `/hotels/{hotel_id}/reservations` | Créer une réservation |
| GET | `/hotels/{hotel_id}/reservations` | Consulter les réservations |
| PATCH | `/hotels/{hotel_id}/reservations/{reservation_id}/annuler` | Annuler une réservation |
| POST | `/hotels/{hotel_id}/clients` | Créer un client |
| POST | `/hotels/{hotel_id}/reservations-completes` | Créer une réservation avec les coordonnées client |
| POST | `/assistant/message` | Échanger avec l'assistant |
| POST | `/assistant/conversations` | Créer une conversation |
| POST | `/assistant/conversations/{conversation_id}/messages` | Continuer une conversation |
| POST | `/assistant/conversations/{conversation_id}/coordonnees` | Enregistrer les coordonnées |
| POST | `/assistant/conversations/{conversation_id}/confirmer` | Confirmer une demande préparée |
| POST | `/paiements/reservations/{reservation_id}/lien-test` | Générer un lien Stripe de test |
| POST | `/paiements/webhook` | Recevoir les événements Stripe |

La documentation `/docs` de l'API démarrée fait référence pour les routes et les paramètres de la version installée.

## Prochaines étapes

- Finaliser la réservation vocale avec récapitulatif et accord explicite.
- Envoyer automatiquement le lien de paiement par SMS après confirmation.
- Raccorder l'assistant à un numéro de téléphone pour traiter de vrais appels.
- Ajouter les migrations SQL et les dépendances Python au dépôt.
- Ajouter l'authentification et les droits d'accès au tableau de bord.
- Préparer la gestion de plusieurs hôtels et les essais avec des établissements pilotes.

## Auteur

**Noël Yendoukoa** — [GitHub](https://github.com/YENDOUKOA)

Projet personnel d'apprentissage et de développement d'une solution d'assistance hôtelière.
