# Plateforme d'audit de réactivité — INFRA S.C.I™

Implémentation complète : sourcing (Google Places API), extraction de biens,
persistance des biens en base, envoi échelonné, écoute IMAP, classification
Groq, dashboard.

## Déploiement — étapes à faire de ton côté

Je n'ai pas accès à `vercel.com`, à l'API Google Places, à Groq, ni à tes
boîtes email depuis mon environnement — ces étapes doivent tourner chez toi :

```bash
# 1. Dépendances
pip install -r requirements.txt

# 2. Config
cp .env.example .env
# remplis : DATABASE_URL, GROQ_API_KEY, GOOGLE_PLACES_API_KEY,
# SENDER_EMAIL_1/2/3 + PASSWORD (mots de passe d'application si Gmail),
# ICP_LOCATIONS

# 3. Créer les tables (agencies, properties, audits)
python -c "from lib.db import init_db; init_db()"

# 4. Déployer
npm i -g vercel   # si pas déjà installé
vercel login
vercel link
vercel env add DATABASE_URL
vercel env add GROQ_API_KEY
vercel env add GOOGLE_PLACES_API_KEY
vercel env add SENDER_EMAIL_1
vercel env add SENDER_PASSWORD_1
# ... (répéter pour _2, _3, IMAP_HOST, IMAP_PORT, ICP_LOCATIONS)
vercel deploy --prod
```

Vérifie ensuite dans le dashboard Vercel (Project → Cron Jobs) que les 3
crons sont bien enregistrés, puis déclenche un run manuel de
`/api/cron/find_leads` pour valider le sourcing avant de laisser tourner
en automatique.

## Ce qui a changé depuis la première version

- **Sourcing réel (`lib/places_client.py`)** : `search_agencies()` interroge
  désormais l'API Google Places (Text Search + Place Details) par
  localisation ICP, conforme à ses CGU. Pour chaque agence trouvée, un
  email public est extrait au mieux depuis son propre site (page d'accueil
  puis `/contact`) — pas depuis un moteur de recherche tiers. Si
  `GOOGLE_PLACES_API_KEY` n'est pas défini, fallback automatique sur
  `SEED_AGENCIES_JSON`.
- **Table `properties` ajoutée** (`models/schema.py`) : les biens extraits
  par `find_leads.py` sont maintenant persistés (1 ligne par test_index,
  1/2/3) et lus par `send_tests.py` via `_property_for_test()`. Si aucun
  bien n'est disponible pour un test donné, l'envoi est reporté (pas de
  faux bien généré).

## Points d'attention restants

1. **`extract_properties()` — sélecteurs CSS génériques.**
   Chaque site d'agence a sa propre structure HTML. Les sélecteurs fournis
   (`[class*='property']`, etc.) sont un point de départ raisonnable mais
   **ne marcheront pas tels quels sur tous les sites**. Prévoir un mapping
   par domaine si tu scales au-delà de quelques agences pilotes.

2. **`python-imap==0.0.1` retiré de `requirements.txt`.**
   Ce package n'existe pas vraiment / n'est pas nécessaire : `imaplib` est
   dans la stdlib Python, déjà utilisé dans `lib/email_parser.py`.

3. **Connexions IMAP en environnement serverless (Vercel, <30s).**
   Vercel Cron ne garde pas de process persistant : chaque exécution de
   `check_inbox.py` ouvre/ferme les 3 connexions IMAP à chaque run (toutes
   les 2h). Si le volume grossit, envisage de filtrer par date (`SINCE`)
   plutôt que `UNSEEN` uniquement.

4. **Correspondance réponse ↔ audit (`_match_audit`).**
   Le matching se fait par (boîte réceptrice = adresse émettrice du test) +
   (expéditeur = email public de l'agence). Si une agence répond depuis une
   adresse différente de son email public affiché, le matching échouera —
   à surveiller sur les premiers runs.

5. **Gmail + mots de passe applicatifs.**
   Si tes 3 boîtes sont Gmail, il te faut des "mots de passe d'application"
   (App Passwords), pas ton mot de passe principal — l'auth simple est
   désactivée par défaut par Google.

6. **Extraction d'email best-effort (`extract_public_email`).**
   Certains sites cachent l'email derrière un formulaire de contact sans
   afficher d'adresse en clair — ces agences seront exclues du sourcing
   (log en warning). C'est un choix volontaire : mieux vaut un audit sur
   une agence dont l'email est confirmé que zéro donnée exploitable.

7. **Quota Google Places API.**
   Le quota gratuit mensuel de Google Places est limité (~$200 de crédit
   offert). Pour 15-20 agences/semaine ça passe largement, mais surveille
   la facturation si tu montes en volume ou multiplies les localisations.

## Structure

```
/api/cron/find_leads.py    Sourcing + scraping (cron 06:00 UTC)
/api/cron/send_tests.py    Envoi échelonné via Groq + SMTP (cron 07:00 UTC)
/api/cron/check_inbox.py   Écoute IMAP + classification (cron /2h)
/api/dashboard.py          GET /api/dashboard (+ ?format=csv)
/lib/                      db, groq_client, email_parser, ai_validator, utils
/models/schema.py          Agency, Audit (SQLAlchemy)
```

## Prochaine étape suggérée

Dis-moi si tu veux que j'ajoute la table `properties` (point 6) ou que je
branche une vraie source de sourcing conforme (point 1) — ce sont les deux
trous dans la spec initiale qu'il faut combler avant un run en conditions
réelles.
